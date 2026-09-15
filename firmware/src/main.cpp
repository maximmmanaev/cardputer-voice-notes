#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <M5Cardputer.h>
#include <Preferences.h>
#include <RecorderCore.h>
#include <RecorderSecrets.h>
#include <SD.h>
#include <SPI.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <mbedtls/sha256.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {

constexpr size_t kBlockSamples = 1024;
constexpr uint32_t kCaptureTimeoutMs = 2500;
constexpr uint32_t kMinimumRecordingMs = 1500;
constexpr uint32_t kDisplayDimMs = 15000;
constexpr uint32_t kIdleSyncMs = 10 * 60 * 1000;
constexpr uint32_t kChargingSyncMs = 60 * 1000;
constexpr uint64_t kMinimumFreeBytes = 256ULL * 1024ULL * 1024ULL;
constexpr size_t kUploadBlockBytes = 4096;
constexpr unsigned kMaxFilesPerSyncCycle = 12;

constexpr int kSdSck = 40;
constexpr int kSdMiso = 39;
constexpr int kSdMosi = 14;
constexpr int kSdCs = 12;

enum class State { Idle, Recording, Stopping, Syncing, SyncError, SyncCancelled, SdError, LowSpace };

State g_state = State::Idle;
File g_file;
QueueHandle_t g_completed = nullptr;
int16_t g_audio[2][kBlockSamples];
uint32_t g_samplesInSegment = 0;
uint64_t g_samplesInSession = 0;
uint32_t g_lastAudioAtMs = 0;
uint32_t g_lastFlushSamples = 0;
uint32_t g_recordingStartedAtMs = 0;
uint32_t g_lastInputAtMs = 0;
uint32_t g_lastDrawAtMs = 0;
uint32_t g_lastSyncAtMs = 0;
uint32_t g_sessionNumber = 0;
uint32_t g_bootNumber = 0;
uint16_t g_segmentNumber = 0;
uint8_t g_inFlight = 0;
volatile bool g_queueOverflow = false;
bool g_stopRequested = false;
bool g_syncRequested = false;
bool g_sWasDown = false;
String g_statusDetail;
String g_currentPartPath;
String g_currentFinalPath;
SPIClass g_sdSpi(FSPI);

uint64_t freeBytes() {
    return SD.totalBytes() - SD.usedBytes();
}

String basenameOf(const String& path) {
    const int slash = path.lastIndexOf('/');
    return slash >= 0 ? path.substring(slash + 1) : path;
}

bool isClosedRecordingName(const String& name) {
    const String base = basenameOf(name);
    return base.startsWith("rec-") && base.endsWith(".wav") && !base.endsWith(".wav.part");
}

bool isSynced(const String& filename) {
    File journal = SD.open("/sync-acks.log", FILE_READ);
    if (!journal) return false;
    const String wanted = basenameOf(filename);
    while (journal.available()) {
        String line = journal.readStringUntil('\n');
        line.trim();
        if (line == wanted) {
            journal.close();
            return true;
        }
    }
    journal.close();
    return false;
}

bool appendSyncedAck(const String& filename) {
    if (isSynced(filename)) return true;
    File journal = SD.open("/sync-acks.log", FILE_APPEND);
    if (!journal) return false;
    const String line = basenameOf(filename) + "\n";
    const bool ok = journal.print(line) == line.length();
    journal.flush();
    journal.close();
    return ok;
}

String oldestSyncedRecording() {
    File root = SD.open("/");
    if (!root) return {};
    String oldest;
    for (File entry = root.openNextFile(); entry; entry = root.openNextFile()) {
        const String name = entry.name();
        const bool candidate = !entry.isDirectory() && isClosedRecordingName(name) && isSynced(name);
        entry.close();
        if (candidate && (oldest.isEmpty() || basenameOf(name) < basenameOf(oldest))) oldest = name;
    }
    root.close();
    return oldest;
}

bool ensureRecordingSpace() {
    while (freeBytes() < kMinimumFreeBytes) {
        const String removable = oldestSyncedRecording();
        if (removable.isEmpty() || !SD.remove(removable)) return false;
        Serial.printf("RETENTION_REMOVED_SYNCED=%s\n", basenameOf(removable).c_str());
    }
    return true;
}

void drawStatus(bool force = false) {
    const uint32_t now = millis();
    if (!force && now - g_lastDrawAtMs < 500) return;
    g_lastDrawAtMs = now;
    auto& display = M5Cardputer.Display;
    display.fillScreen(BLACK);
    display.setTextDatum(top_left);
    display.setTextSize(1);
    display.setTextColor(WHITE, BLACK);
    display.setCursor(5, 4);

    switch (g_state) {
        case State::Recording:
        case State::Stopping: {
            display.setTextColor(RED, BLACK);
            display.setTextSize(2);
            display.println(g_state == State::Stopping ? "STOPPING" : "REC");
            display.setTextSize(1);
            display.setTextColor(WHITE, BLACK);
            const uint64_t seconds = g_samplesInSession / recorder::kSampleRate;
            display.printf("%02llu:%02llu:%02llu  fragment %u\n",
                           seconds / 3600,
                           (seconds / 60) % 60,
                           seconds % 60,
                           static_cast<unsigned>(g_segmentNumber));
            display.printf("SD free %llu MiB  battery %ld%%\n",
                           freeBytes() / (1024ULL * 1024ULL),
                           static_cast<long>(M5.Power.getBatteryLevel()));
            display.println("BtnG0: stop + send");
            break;
        }
        case State::Syncing:
            display.setTextColor(YELLOW, BLACK);
            display.setTextSize(2);
            display.println("SYNCING");
            display.setTextSize(1);
            display.setTextColor(WHITE, BLACK);
            display.println(g_statusDetail);
            display.println("Fn+`: cancel upload");
            break;
        case State::SyncCancelled:
            display.setTextColor(YELLOW, BLACK);
            display.setTextSize(2);
            display.println("SYNC CANCELLED");
            display.setTextSize(1);
            display.setTextColor(WHITE, BLACK);
            display.println("File remains queued");
            display.println("S: retry sync");
            break;
        case State::SyncError:
            display.setTextColor(ORANGE, BLACK);
            display.setTextSize(2);
            display.println("SYNC ERROR");
            display.setTextSize(1);
            display.setTextColor(WHITE, BLACK);
            display.println(g_statusDetail);
            display.println("File remains queued");
            break;
        case State::SdError:
            display.setTextColor(RED, BLACK);
            display.setTextSize(2);
            display.println("SD ERROR");
            display.setTextSize(1);
            display.println(g_statusDetail);
            break;
        case State::LowSpace:
            display.setTextColor(RED, BLACK);
            display.setTextSize(2);
            display.println("LOW SPACE");
            display.setTextSize(1);
            display.println("No synced file to remove");
            break;
        case State::Idle:
        default:
            display.setTextColor(GREEN, BLACK);
            display.setTextSize(2);
            display.println("RECORDER READY");
            display.setTextSize(1);
            display.setTextColor(WHITE, BLACK);
            display.println("BtnG0: start recording");
            display.println("S: sync now");
            display.printf("SD %llu MiB free  battery %ld%%\n",
                           freeBytes() / (1024ULL * 1024ULL),
                           static_cast<long>(M5.Power.getBatteryLevel()));
            break;
    }
}

void noteInput() {
    g_lastInputAtMs = millis();
    M5Cardputer.Display.setBrightness(128);
}

void serviceBacklight() {
    if (millis() - g_lastInputAtMs < kDisplayDimMs) return;
    M5Cardputer.Display.setBrightness(g_state == State::Recording || g_state == State::Stopping ? 12 : 0);
}

void failRecording(const String& message) {
    Serial.printf("RECORDING_ERROR=%s\n", message.c_str());
    M5Cardputer.Mic.end();
    if (g_file) {
        g_file.flush();
        g_file.close();
    }
    g_inFlight = 0;
    g_statusDetail = message;
    g_state = State::SdError;
    drawStatus(true);
}

void onAudioBlockComplete(void*, void* data, size_t) {
    const uint8_t index = data == static_cast<void*>(g_audio[0]) ? 0 : 1;
    if (xQueueSend(g_completed, &index, 0) != pdTRUE) g_queueOverflow = true;
}

bool scheduleBlock(uint8_t index) {
    if (!M5Cardputer.Mic.record(g_audio[index], kBlockSamples, recorder::kSampleRate, false)) return false;
    ++g_inFlight;
    return true;
}

bool chooseSegmentPaths() {
    const uint16_t suffix = static_cast<uint16_t>(ESP.getEfuseMac());
    for (; g_segmentNumber <= 999; ++g_segmentNumber) {
        char finalPath[96];
        snprintf(finalPath,
                 sizeof(finalPath),
                 "/rec-%04x-b%06lu-s%06lu-f%03u.wav",
                 suffix,
                 static_cast<unsigned long>(g_bootNumber),
                 static_cast<unsigned long>(g_sessionNumber),
                 static_cast<unsigned>(g_segmentNumber));
        const String partPath = String(finalPath) + ".part";
        if (!SD.exists(finalPath) && !SD.exists(partPath)) {
            g_currentFinalPath = finalPath;
            g_currentPartPath = partPath;
            return true;
        }
    }
    return false;
}

bool openSegment() {
    if (!chooseSegmentPaths()) return false;
    g_file = SD.open(g_currentPartPath, FILE_WRITE);
    if (!g_file) return false;
    const recorder::WavHeader header = recorder::makeWavHeader(0);
    if (g_file.write(reinterpret_cast<const uint8_t*>(&header), sizeof(header)) != sizeof(header)) {
        g_file.close();
        return false;
    }
    g_samplesInSegment = 0;
    g_lastFlushSamples = 0;
    return true;
}

bool finalizeSegment() {
    if (!g_file) return true;
    const uint32_t pcmBytes = g_samplesInSegment * sizeof(int16_t);
    const recorder::WavHeader header = recorder::makeWavHeader(pcmBytes);
    if (!g_file.seek(0) || g_file.write(reinterpret_cast<const uint8_t*>(&header), sizeof(header)) != sizeof(header)) return false;
    g_file.flush();
    g_file.close();
    if (!SD.rename(g_currentPartPath, g_currentFinalPath)) return false;
    Serial.printf("RECORDING_CLOSED=%s bytes=%lu samples=%lu\n",
                  g_currentFinalPath.c_str(),
                  static_cast<unsigned long>(pcmBytes + sizeof(recorder::WavHeader)),
                  static_cast<unsigned long>(g_samplesInSegment));
    ++g_segmentNumber;
    return true;
}

bool writeAudioSamples(const int16_t* samples, uint32_t count) {
    uint32_t offset = 0;
    while (offset < count) {
        if (g_samplesInSegment == recorder::kSegmentSamples && (!finalizeSegment() || !openSegment())) return false;
        const uint32_t portion = recorder::samplesBeforeRotation(g_samplesInSegment, count - offset);
        const size_t bytes = portion * sizeof(int16_t);
        if (portion == 0 || g_file.write(reinterpret_cast<const uint8_t*>(samples + offset), bytes) != bytes) return false;
        g_samplesInSegment += portion;
        g_samplesInSession += portion;
        offset += portion;
        if (g_samplesInSegment == recorder::kSegmentSamples && (!finalizeSegment() || !openSegment())) return false;
    }
    if (g_samplesInSegment - g_lastFlushSamples >= recorder::kSampleRate * 2) {
        g_file.flush();
        g_lastFlushSamples = g_samplesInSegment;
    }
    return true;
}

void finishRecordingSession() {
    M5Cardputer.Mic.end();
    if (g_samplesInSegment == 0) {
        g_file.close();
        SD.remove(g_currentPartPath);
    } else if (!finalizeSegment()) {
        failRecording("Cannot finalize WAV");
        return;
    }
    g_inFlight = 0;
    g_stopRequested = false;
    g_state = State::Idle;
    g_syncRequested = true;
    g_statusDetail = "Recording saved";
    Serial.printf("RECORDING_SESSION_COMPLETE samples=%llu\n", g_samplesInSession);
    drawStatus(true);
}

void startRecording() {
    if (!ensureRecordingSpace()) {
        g_state = State::LowSpace;
        drawStatus(true);
        return;
    }
    ++g_sessionNumber;
    g_segmentNumber = 1;
    g_samplesInSession = 0;
    if (!openSegment()) {
        failRecording("Cannot create WAV.part");
        return;
    }
    xQueueReset(g_completed);
    g_queueOverflow = false;
    g_stopRequested = false;
    g_inFlight = 0;
    M5Cardputer.Speaker.end();
    M5Cardputer.Mic.setSampleRate(recorder::kSampleRate);
    M5Cardputer.Mic.setBufferReleaseCallback(nullptr, onAudioBlockComplete);
    if (!M5Cardputer.Mic.begin()) {
        failRecording("ES8311/Mic begin failed");
        return;
    }
    delay(1200);
    g_lastAudioAtMs = millis();
    g_recordingStartedAtMs = millis();
    g_state = State::Recording;
    noteInput();
    drawStatus(true);
    Serial.printf("RECORDING_STARTED=%s\n", g_currentPartPath.c_str());
    if (!scheduleBlock(0) || !scheduleBlock(1)) failRecording("Mic queue rejected buffers");
}

void requestStopRecording() {
    if (millis() - g_recordingStartedAtMs < kMinimumRecordingMs) return;
    g_stopRequested = true;
    g_state = State::Stopping;
    drawStatus(true);
}

void serviceRecording() {
    if (g_queueOverflow) {
        failRecording("Audio queue overflow");
        return;
    }
    uint8_t index = 0;
    if (xQueueReceive(g_completed, &index, pdMS_TO_TICKS(20)) == pdTRUE) {
        if (g_inFlight > 0) --g_inFlight;
        g_lastAudioAtMs = millis();
        if (!writeAudioSamples(g_audio[index], kBlockSamples)) {
            failRecording("SD write failed");
            return;
        }
        if (!g_stopRequested && !scheduleBlock(index)) {
            failRecording("Mic queue rejected buffer");
            return;
        }
        if (g_stopRequested && g_inFlight == 0) finishRecordingSession();
    } else if (millis() - g_lastAudioAtMs > kCaptureTimeoutMs) {
        failRecording("Audio capture timeout");
    }
    drawStatus();
}

void recoverPartials() {
    std::vector<String> paths;
    File root = SD.open("/");
    if (!root) return;
    for (File entry = root.openNextFile(); entry; entry = root.openNextFile()) {
        const String name = entry.name();
        if (!entry.isDirectory() && name.endsWith(".wav.part")) paths.push_back(name);
        entry.close();
    }
    root.close();
    for (const String& path : paths) {
        File partial = SD.open(path, "r+");
        if (!partial || partial.size() < sizeof(recorder::WavHeader)) {
            if (partial) partial.close();
            Serial.printf("RECOVERY_SKIPPED=%s\n", path.c_str());
            continue;
        }
        const uint32_t pcmBytes = recorder::recoverablePcmBytes(partial.size());
        const recorder::WavHeader header = recorder::makeWavHeader(pcmBytes);
        if (!partial.seek(0) || partial.write(reinterpret_cast<const uint8_t*>(&header), sizeof(header)) != sizeof(header)) {
            partial.close();
            continue;
        }
        partial.flush();
        partial.close();
        const String recovered = path.substring(0, path.length() - 9) + ".recovered.wav";
        if (SD.rename(path, recovered)) Serial.printf("RECOVERED_WAV=%s bytes=%lu\n", recovered.c_str(), pcmBytes + 44UL);
    }
}

String nextUnsyncedRecording() {
    File root = SD.open("/");
    if (!root) return {};
    String selected;
    for (File entry = root.openNextFile(); entry; entry = root.openNextFile()) {
        const String name = entry.name();
        const bool candidate = !entry.isDirectory() && isClosedRecordingName(name) && !isSynced(name);
        entry.close();
        if (candidate && (selected.isEmpty() || basenameOf(name) < basenameOf(selected))) selected = name;
    }
    root.close();
    return selected;
}

bool checkCancelKey() {
    M5Cardputer.update();
    const bool esc = M5Cardputer.Keyboard.keysState().esc;
    if (esc) noteInput();
    return esc;
}

bool connectKnownWifi() {
    if (strlen(RECORDER_WIFI_SSID) == 0 || strlen(RECORDER_DEVICE_TOKEN) < 16) {
        g_statusDetail = "Run firmware config";
        return false;
    }
    WiFi.mode(WIFI_STA);
    WiFi.begin(RECORDER_WIFI_SSID, RECORDER_WIFI_PASSWORD);
    const uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < 10000) {
        if (checkCancelKey()) {
            WiFi.disconnect(true);
            g_state = State::SyncCancelled;
            return false;
        }
        delay(100);
    }
    if (WiFi.status() != WL_CONNECTED) {
        g_statusDetail = "Known Wi-Fi unavailable";
        WiFi.disconnect(true);
        return false;
    }
    return true;
}

bool resolveAgent(IPAddress& address, uint16_t& port) {
    port = RECORDER_SYNC_PORT;
    const uint16_t suffix = static_cast<uint16_t>(ESP.getEfuseMac());
    char host[40];
    snprintf(host, sizeof(host), "cardputer-%04x", suffix);
    if (MDNS.begin(host)) {
        const int count = MDNS.queryService("cardputer-sync", "tcp");
        if (count > 0) {
            address = MDNS.IP(0);
            port = MDNS.port(0);
            return true;
        }
    }
    if (strlen(RECORDER_SYNC_FALLBACK_IP) > 0 && address.fromString(RECORDER_SYNC_FALLBACK_IP)) return true;
    g_statusDetail = "Mac agent not found";
    return false;
}

bool sha256File(const String& path, char output[65]) {
    File source = SD.open(path, FILE_READ);
    if (!source) return false;
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    if (mbedtls_sha256_starts_ret(&context, 0) != 0) {
        source.close();
        mbedtls_sha256_free(&context);
        return false;
    }
    uint8_t block[kUploadBlockBytes];
    while (source.available()) {
        const size_t count = source.read(block, sizeof(block));
        if (count == 0 || mbedtls_sha256_update_ret(&context, block, count) != 0) {
            source.close();
            mbedtls_sha256_free(&context);
            return false;
        }
        if (checkCancelKey()) {
            source.close();
            mbedtls_sha256_free(&context);
            g_state = State::SyncCancelled;
            return false;
        }
    }
    source.close();
    uint8_t digest[32];
    const bool ok = mbedtls_sha256_finish_ret(&context, digest) == 0;
    mbedtls_sha256_free(&context);
    if (!ok) return false;
    for (size_t i = 0; i < sizeof(digest); ++i) sprintf(output + i * 2, "%02x", digest[i]);
    output[64] = '\0';
    return true;
}

String recordingId(const String& path) {
    String id = basenameOf(path);
    if (id.endsWith(".wav")) id.remove(id.length() - 4);
    id.replace('.', '-');
    return id;
}

bool parseStatusJson(const String& body, size_t& offset, bool& durableAck) {
    JsonDocument document;
    if (deserializeJson(document, body) != DeserializationError::Ok) return false;
    offset = document["offset"] | 0;
    durableAck = document["durable_ack"] | false;
    return true;
}

bool fetchRemoteStatus(const IPAddress& address, uint16_t port, const String& id, size_t& offset, bool& durableAck) {
    HTTPClient http;
    const String url = String("http://") + address.toString() + ":" + port + "/v1/recordings/" + id;
    if (!http.begin(url)) return false;
    http.setConnectTimeout(5000);
    http.setTimeout(8000);
    http.addHeader("Authorization", String("Bearer ") + RECORDER_DEVICE_TOKEN);
    const int status = http.GET();
    const String body = status > 0 ? http.getString() : String();
    http.end();
    return status == 200 && parseStatusJson(body, offset, durableAck);
}

bool readHttpResponse(WiFiClient& client, int& status, String& body) {
    const uint32_t started = millis();
    while (!client.available() && client.connected() && millis() - started < 15000) {
        if (checkCancelKey()) {
            client.stop();
            g_state = State::SyncCancelled;
            return false;
        }
        delay(10);
    }
    if (!client.available()) return false;
    const String statusLine = client.readStringUntil('\n');
    status = statusLine.substring(statusLine.indexOf(' ') + 1).toInt();
    size_t contentLength = 0;
    while (client.available() || client.connected()) {
        String line = client.readStringUntil('\n');
        if (line == "\r" || line.isEmpty()) break;
        line.trim();
        if (line.startsWith("content-length:")) {
            line.remove(0, 15);
            line.trim();
            contentLength = line.toInt();
        }
    }
    body.reserve(contentLength);
    while (body.length() < contentLength && millis() - started < 20000) {
        while (client.available() && body.length() < contentLength) body += static_cast<char>(client.read());
        delay(1);
    }
    return status > 0;
}

bool uploadFromOffset(const IPAddress& address,
                      uint16_t port,
                      const String& path,
                      const String& id,
                      size_t offset,
                      const char* sha256) {
    File source = SD.open(path, FILE_READ);
    if (!source || offset > source.size() || !source.seek(offset)) {
        if (source) source.close();
        return false;
    }
    const size_t total = source.size();
    WiFiClient client;
    client.setTimeout(15000);
    if (!client.connect(address, port, 5000)) {
        source.close();
        return false;
    }
    const String filename = basenameOf(path);
    const uint64_t durationMs = total > 44 ? ((total - 44ULL) / 2ULL) * 1000ULL / recorder::kSampleRate : 0;
    client.printf("PUT /v1/recordings/%s HTTP/1.1\r\n", id.c_str());
    client.printf("Host: %s:%u\r\n", address.toString().c_str(), port);
    client.printf("Authorization: Bearer %s\r\n", RECORDER_DEVICE_TOKEN);
    client.print("Content-Type: application/octet-stream\r\n");
    client.printf("Content-Length: %u\r\n", static_cast<unsigned>(total - offset));
    client.printf("X-Upload-Offset: %u\r\n", static_cast<unsigned>(offset));
    client.printf("X-Recording-Size: %u\r\n", static_cast<unsigned>(total));
    client.printf("X-Recording-SHA256: %s\r\n", sha256);
    client.printf("X-Recording-Filename: %s\r\n", filename.c_str());
    client.printf("X-Duration-Ms: %llu\r\n", durationMs);
    client.print("Connection: close\r\n\r\n");

    uint8_t block[kUploadBlockBytes];
    size_t sent = offset;
    while (sent < total) {
        if (checkCancelKey()) {
            source.close();
            client.stop();
            g_state = State::SyncCancelled;
            return false;
        }
        const size_t wanted = std::min(kUploadBlockBytes, total - sent);
        const size_t count = source.read(block, wanted);
        if (count == 0) break;
        size_t blockOffset = 0;
        while (blockOffset < count) {
            const size_t written = client.write(block + blockOffset, count - blockOffset);
            if (written == 0) {
                if (!client.connected()) {
                    source.close();
                    return false;
                }
                delay(2);
                continue;
            }
            blockOffset += written;
            sent += written;
        }
        g_statusDetail = filename + " " + String(static_cast<unsigned>(sent * 100ULL / total)) + "%";
        drawStatus();
    }
    source.close();
    if (sent != total) {
        client.stop();
        return false;
    }
    int status = 0;
    String body;
    const bool responseRead = readHttpResponse(client, status, body);
    client.stop();
    size_t acknowledgedOffset = 0;
    bool durableAck = false;
    return responseRead && status == 200 && parseStatusJson(body, acknowledgedOffset, durableAck) && durableAck &&
           acknowledgedOffset == total;
}

bool syncOne(const IPAddress& address, uint16_t port, const String& path) {
    const String id = recordingId(path);
    size_t offset = 0;
    bool durableAck = false;
    if (!fetchRemoteStatus(address, port, id, offset, durableAck)) return false;
    if (durableAck) return appendSyncedAck(path);
    char hash[65];
    g_statusDetail = basenameOf(path) + " hashing";
    drawStatus(true);
    if (!sha256File(path, hash)) return false;
    if (!uploadFromOffset(address, port, path, id, offset, hash)) return false;
    return appendSyncedAck(path);
}

void runSyncCycle() {
    g_syncRequested = false;
    g_lastSyncAtMs = millis();
    if (nextUnsyncedRecording().isEmpty()) {
        g_state = State::Idle;
        return;
    }
    g_state = State::Syncing;
    g_statusDetail = "Connecting Wi-Fi";
    noteInput();
    drawStatus(true);
    if (!connectKnownWifi()) {
        if (g_state != State::SyncCancelled) g_state = State::SyncError;
        drawStatus(true);
        return;
    }
    IPAddress address;
    uint16_t port = 0;
    if (!resolveAgent(address, port)) {
        g_state = State::SyncError;
        WiFi.disconnect(true);
        drawStatus(true);
        return;
    }
    bool failure = false;
    for (unsigned count = 0; count < kMaxFilesPerSyncCycle; ++count) {
        const String path = nextUnsyncedRecording();
        if (path.isEmpty()) break;
        g_statusDetail = basenameOf(path);
        drawStatus(true);
        if (!syncOne(address, port, path)) {
            failure = g_state != State::SyncCancelled;
            break;
        }
        Serial.printf("SYNC_DURABLE_ACK=%s\n", basenameOf(path).c_str());
    }
    WiFi.disconnect(true);
    if (g_state == State::SyncCancelled) {
        drawStatus(true);
        return;
    }
    g_state = failure ? State::SyncError : State::Idle;
    g_statusDetail = failure ? "Will retry later" : "Sync complete";
    drawStatus(true);
}

void haltWithBootError(const char* message) {
    g_state = State::SdError;
    g_statusDetail = message;
    while (true) {
        drawStatus(true);
        Serial.printf("BOOT_ERROR=%s firmware=%s\n", message, FIRMWARE_VERSION);
        delay(2000);
    }
}

}  // namespace

void setup() {
    Serial.begin(115200);
    delay(500);
    auto config = M5.config();
    config.internal_mic = true;
    config.internal_spk = true;
    M5Cardputer.begin(config);
    M5Cardputer.Display.setRotation(1);
    M5Cardputer.Display.setBrightness(128);
    g_lastInputAtMs = millis();

    Serial.printf("FIRMWARE_VERSION=%s\n", FIRMWARE_VERSION);
    Serial.printf("BUILD_TIMESTAMP=%sT%s\n", __DATE__, __TIME__);
    Serial.printf("M5_BOARD_ID=%d\n", static_cast<int>(M5.getBoard()));
    if (M5.getBoard() != m5::board_t::board_M5CardputerADV) haltWithBootError("Not Cardputer-Adv");

    g_sdSpi.begin(kSdSck, kSdMiso, kSdMosi, kSdCs);
    if (!SD.begin(kSdCs, g_sdSpi, 25000000) || SD.cardType() == CARD_NONE) haltWithBootError("microSD unavailable");

    Preferences preferences;
    preferences.begin("recorder", false);
    g_bootNumber = preferences.getUInt("boot", 0) + 1;
    preferences.putUInt("boot", g_bootNumber);
    preferences.end();

    g_completed = xQueueCreate(4, sizeof(uint8_t));
    if (!g_completed) haltWithBootError("Audio queue alloc failed");
    recoverPartials();
    if (!ensureRecordingSpace()) g_state = State::LowSpace;
    Serial.printf("CARDPUTER_ADV_READY=1 boot=%lu sd_free=%llu\n",
                  static_cast<unsigned long>(g_bootNumber),
                  freeBytes());
    drawStatus(true);
}

void loop() {
    M5Cardputer.update();
    const auto& keys = M5Cardputer.Keyboard.keysState();
    const bool g0Pressed = M5Cardputer.BtnA.wasPressed();
    if (g0Pressed || M5Cardputer.Keyboard.isPressed()) noteInput();

    if (g0Pressed) {
        if (g_state == State::Recording) requestStopRecording();
        else if (g_state == State::Idle || g_state == State::SyncError || g_state == State::SyncCancelled) startRecording();
    }

    const bool sDown = std::find(keys.word.begin(), keys.word.end(), 's') != keys.word.end();
    if (sDown && !g_sWasDown && g_state != State::Recording && g_state != State::Stopping) g_syncRequested = true;
    g_sWasDown = sDown;

    if (g_state == State::Recording || g_state == State::Stopping) {
        serviceRecording();
        serviceBacklight();
        return;
    }

    const uint32_t syncInterval = M5.Power.isCharging() == m5::Power_Class::is_charging ? kChargingSyncMs : kIdleSyncMs;
    if (!g_syncRequested && millis() - g_lastSyncAtMs >= syncInterval) g_syncRequested = true;
    if (g_syncRequested) runSyncCycle();
    drawStatus();
    serviceBacklight();
    delay(10);
}
