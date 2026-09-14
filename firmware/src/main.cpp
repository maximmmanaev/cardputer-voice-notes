#include <Arduino.h>
#include <M5Cardputer.h>
#include <SD.h>
#include <SPI.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include <algorithm>
#include <cstdint>
#include <cstring>

namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr uint16_t kChannels = 1;
constexpr uint16_t kBitsPerSample = 16;
constexpr size_t kBlockSamples = 1024;
constexpr uint32_t kDurationSeconds = 60;
constexpr uint32_t kTargetSamples = kSampleRate * kDurationSeconds;
constexpr uint32_t kWriteTimeoutMs = 2500;

constexpr int kSdSck = 40;
constexpr int kSdMiso = 39;
constexpr int kSdMosi = 14;
constexpr int kSdCs = 12;

#pragma pack(push, 1)
struct WavHeader {
    char riff[4];
    uint32_t riffSize;
    char wave[4];
    char fmt[4];
    uint32_t fmtSize;
    uint16_t audioFormat;
    uint16_t channels;
    uint32_t sampleRate;
    uint32_t byteRate;
    uint16_t blockAlign;
    uint16_t bitsPerSample;
    char data[4];
    uint32_t dataSize;
};
#pragma pack(pop)

static_assert(sizeof(WavHeader) == 44, "WAV header must be 44 bytes");

enum class State { Idle, Recording, Complete, Error };

State g_state = State::Idle;
File g_file;
QueueHandle_t g_completed = nullptr;
int16_t g_audio[2][kBlockSamples];
size_t g_blockSamples[2] = {0, 0};
uint32_t g_scheduledSamples = 0;
uint32_t g_writtenSamples = 0;
uint32_t g_lastCompletedAtMs = 0;
uint32_t g_lastFlushSamples = 0;
volatile bool g_queueOverflow = false;
uint32_t g_lastIdleStatusAtMs = 0;
char g_partPath[64] = {};
char g_finalPath[64] = {};
String g_error;

WavHeader makeHeader(uint32_t pcmBytes) {
    WavHeader header{};
    memcpy(header.riff, "RIFF", 4);
    header.riffSize = 36 + pcmBytes;
    memcpy(header.wave, "WAVE", 4);
    memcpy(header.fmt, "fmt ", 4);
    header.fmtSize = 16;
    header.audioFormat = 1;
    header.channels = kChannels;
    header.sampleRate = kSampleRate;
    header.byteRate = kSampleRate * kChannels * (kBitsPerSample / 8);
    header.blockAlign = kChannels * (kBitsPerSample / 8);
    header.bitsPerSample = kBitsPerSample;
    memcpy(header.data, "data", 4);
    header.dataSize = pcmBytes;
    return header;
}

void drawStatus() {
    auto& display = M5Cardputer.Display;
    display.fillScreen(BLACK);
    display.setTextDatum(top_left);
    display.setTextSize(1);
    display.setCursor(6, 5);

    if (g_state == State::Recording) {
        display.setTextColor(RED, BLACK);
        display.setTextSize(2);
        display.print("REC");
        display.setTextSize(1);
        display.setTextColor(WHITE, BLACK);
        display.setCursor(6, 35);
        display.printf("%02lu / 60 sec\n", static_cast<unsigned long>(g_writtenSamples / kSampleRate));
        display.printf("PCM %lu / %lu\n",
                       static_cast<unsigned long>(g_writtenSamples),
                       static_cast<unsigned long>(kTargetSamples));
        display.printf("SD free %llu MiB\n",
                       static_cast<unsigned long long>((SD.totalBytes() - SD.usedBytes()) / (1024ULL * 1024ULL)));
    } else if (g_state == State::Complete) {
        display.setTextColor(GREEN, BLACK);
        display.setTextSize(2);
        display.println("SPIKE A OK");
        display.setTextSize(1);
        display.setTextColor(WHITE, BLACK);
        display.println(g_finalPath);
        display.println("Copy SD to Mac");
    } else if (g_state == State::Error) {
        display.setTextColor(RED, BLACK);
        display.setTextSize(2);
        display.println("SPIKE ERROR");
        display.setTextSize(1);
        display.setTextColor(WHITE, BLACK);
        display.println(g_error);
        display.println(".part retained");
    } else {
        display.setTextColor(GREEN, BLACK);
        display.setTextSize(2);
        display.println("ADV AUDIO READY");
        display.setTextSize(1);
        display.setTextColor(WHITE, BLACK);
        display.println("BtnG0: record 60s");
        display.printf("SD free %llu MiB\n",
                       static_cast<unsigned long long>((SD.totalBytes() - SD.usedBytes()) / (1024ULL * 1024ULL)));
    }
}

void failRecording(const String& message) {
    g_error = message;
    Serial.printf("SPIKE_A_ERROR=%s\n", message.c_str());
    M5Cardputer.Mic.end();
    if (g_file) {
        g_file.flush();
        g_file.close();
    }
    g_state = State::Error;
    drawStatus();
}

void onAudioBlockComplete(void*, void* data, size_t) {
    uint8_t index = data == static_cast<void*>(g_audio[0]) ? 0 : 1;
    if (xQueueSend(g_completed, &index, 0) != pdTRUE) {
        g_queueOverflow = true;
    }
}

bool scheduleBlock(uint8_t index) {
    if (g_scheduledSamples >= kTargetSamples) return true;
    const uint32_t remaining = kTargetSamples - g_scheduledSamples;
    const size_t count = std::min<size_t>(kBlockSamples, remaining);
    g_blockSamples[index] = count;
    if (!M5Cardputer.Mic.record(g_audio[index], count, kSampleRate, false)) return false;
    g_scheduledSamples += count;
    return true;
}

bool chooseOutputPaths() {
    const uint16_t deviceSuffix = static_cast<uint16_t>(ESP.getEfuseMac());
    for (unsigned sequence = 1; sequence <= 999; ++sequence) {
        snprintf(g_finalPath, sizeof(g_finalPath), "/spike-a-%04x-%03u.wav", deviceSuffix, sequence);
        snprintf(g_partPath, sizeof(g_partPath), "%s.part", g_finalPath);
        if (!SD.exists(g_finalPath) && !SD.exists(g_partPath)) return true;
    }
    return false;
}

void startRecording() {
    if (!chooseOutputPaths()) {
        failRecording("No free spike filename");
        return;
    }

    g_file = SD.open(g_partPath, FILE_WRITE);
    if (!g_file) {
        failRecording("Cannot create WAV.part");
        return;
    }

    const WavHeader placeholder = makeHeader(0);
    if (g_file.write(reinterpret_cast<const uint8_t*>(&placeholder), sizeof(placeholder)) != sizeof(placeholder)) {
        failRecording("Cannot write WAV header");
        return;
    }

    xQueueReset(g_completed);
    g_queueOverflow = false;
    g_scheduledSamples = 0;
    g_writtenSamples = 0;
    g_lastFlushSamples = 0;

    M5Cardputer.Speaker.end();
    M5Cardputer.Mic.setSampleRate(kSampleRate);
    M5Cardputer.Mic.setBufferReleaseCallback(nullptr, onAudioBlockComplete);
    if (!M5Cardputer.Mic.begin()) {
        failRecording("ES8311/Mic begin failed");
        return;
    }

    // M5Unified documents an approximately one-second ES8311 capture warm-up.
    delay(1200);
    g_lastCompletedAtMs = millis();
    g_state = State::Recording;
    drawStatus();
    Serial.printf("SPIKE_A_RECORDING=%s\n", g_partPath);

    if (!scheduleBlock(0) || !scheduleBlock(1)) {
        failRecording("Mic queue rejected initial buffers");
    }
}

void finishRecording() {
    M5Cardputer.Mic.end();
    const uint32_t pcmBytes = g_writtenSamples * sizeof(int16_t);
    const WavHeader header = makeHeader(pcmBytes);
    if (!g_file.seek(0) ||
        g_file.write(reinterpret_cast<const uint8_t*>(&header), sizeof(header)) != sizeof(header)) {
        failRecording("Cannot finalize WAV header");
        return;
    }
    g_file.flush();
    g_file.close();

    if (!SD.rename(g_partPath, g_finalPath)) {
        failRecording("Cannot rename completed WAV");
        return;
    }

    g_state = State::Complete;
    Serial.printf("SPIKE_A_COMPLETE=%s bytes=%lu samples=%lu\n",
                  g_finalPath,
                  static_cast<unsigned long>(pcmBytes + sizeof(WavHeader)),
                  static_cast<unsigned long>(g_writtenSamples));
    drawStatus();
}

void serviceRecording() {
    if (g_queueOverflow) {
        failRecording("Audio queue overflow");
        return;
    }

    uint8_t index = 0;
    if (xQueueReceive(g_completed, &index, pdMS_TO_TICKS(20)) == pdTRUE) {
        g_lastCompletedAtMs = millis();
        const size_t byteCount = g_blockSamples[index] * sizeof(int16_t);
        if (g_file.write(reinterpret_cast<const uint8_t*>(g_audio[index]), byteCount) != byteCount) {
            failRecording("SD write failed");
            return;
        }
        g_writtenSamples += g_blockSamples[index];

        if (g_writtenSamples - g_lastFlushSamples >= kSampleRate * 2) {
            g_file.flush();
            g_lastFlushSamples = g_writtenSamples;
            drawStatus();
        }

        if (g_scheduledSamples < kTargetSamples && !scheduleBlock(index)) {
            failRecording("Mic queue rejected buffer");
            return;
        }

        if (g_writtenSamples == kTargetSamples) finishRecording();
    } else if (millis() - g_lastCompletedAtMs > kWriteTimeoutMs) {
        failRecording("Audio capture timeout");
    }
}

bool findLatestCompletedPath(char* path, size_t pathSize) {
    const uint16_t deviceSuffix = static_cast<uint16_t>(ESP.getEfuseMac());
    for (int sequence = 999; sequence >= 1; --sequence) {
        snprintf(path, pathSize, "/spike-a-%04x-%03d.wav", deviceSuffix, sequence);
        if (SD.exists(path)) return true;
    }
    path[0] = '\0';
    return false;
}

void downloadLatestRecording() {
    char path[64] = {};
    if (!findLatestCompletedPath(path, sizeof(path))) {
        Serial.println("SPIKE_A_DOWNLOAD_ERROR=no_completed_wav");
        return;
    }

    File source = SD.open(path, FILE_READ);
    if (!source) {
        Serial.println("SPIKE_A_DOWNLOAD_ERROR=open_failed");
        return;
    }

    const size_t size = source.size();
    Serial.printf("SPIKE_A_DOWNLOAD_BEGIN path=%s size=%u\n", path, static_cast<unsigned>(size));
    Serial.flush();

    uint8_t buffer[4096];
    size_t sent = 0;
    while (source.available()) {
        const size_t count = source.read(buffer, sizeof(buffer));
        if (count == 0) break;
        size_t offset = 0;
        while (offset < count) {
            const size_t written = Serial.write(buffer + offset, count - offset);
            if (written == 0) {
                delay(1);
                continue;
            }
            offset += written;
            sent += written;
        }
    }
    source.close();
    Serial.write('\n');
    Serial.printf("SPIKE_A_DOWNLOAD_END size=%u\n", static_cast<unsigned>(sent));
    Serial.flush();
}

void haltWithBootError(const char* message) {
    Serial.printf("SPIKE_A_BOOT_ERROR=%s\n", message);
    M5Cardputer.Display.fillScreen(BLACK);
    M5Cardputer.Display.setTextColor(RED, BLACK);
    M5Cardputer.Display.setTextSize(2);
    M5Cardputer.Display.setCursor(4, 10);
    M5Cardputer.Display.println("BOOT ERROR");
    M5Cardputer.Display.setTextSize(1);
    M5Cardputer.Display.println(message);
    while (true) {
        Serial.printf("SPIKE_A_BOOT_ERROR=%s firmware=%s\n", message, FIRMWARE_VERSION);
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

    Serial.printf("FIRMWARE_VERSION=%s\n", FIRMWARE_VERSION);
    Serial.printf("BUILD_TIMESTAMP=%sT%s\n", __DATE__, __TIME__);
    Serial.printf("M5_BOARD_ID=%d\n", static_cast<int>(M5.getBoard()));
    Serial.printf("DEVICE_SUFFIX=%04x\n", static_cast<uint16_t>(ESP.getEfuseMac()));

    if (M5.getBoard() != m5::board_t::board_M5CardputerADV) {
        haltWithBootError("Not Cardputer-Adv");
    }

    SPI.begin(kSdSck, kSdMiso, kSdMosi, kSdCs);
    if (!SD.begin(kSdCs, SPI, 25000000)) haltWithBootError("microSD init failed");
    if (SD.cardType() == CARD_NONE) haltWithBootError("microSD absent");

    g_completed = xQueueCreate(2, sizeof(uint8_t));
    if (g_completed == nullptr) haltWithBootError("Audio queue alloc failed");

    Serial.printf("CARDPUTER_ADV_CONFIRMED=1\n");
    Serial.printf("SD_SIZE_BYTES=%llu\n", static_cast<unsigned long long>(SD.cardSize()));
    Serial.printf("SD_FREE_BYTES=%llu\n",
                  static_cast<unsigned long long>(SD.totalBytes() - SD.usedBytes()));
    drawStatus();
}

void loop() {
    M5Cardputer.update();

    if (g_state == State::Recording) {
        serviceRecording();
        return;
    }

    if (Serial.available()) {
        const int command = Serial.read();
        if (command == 'D') downloadLatestRecording();
    }
    if (M5Cardputer.BtnA.wasPressed() && g_state == State::Idle) startRecording();
    if (g_state == State::Idle && millis() - g_lastIdleStatusAtMs >= 2000) {
        g_lastIdleStatusAtMs = millis();
        Serial.printf("SPIKE_A_STATUS=IDLE firmware=%s board=%d sd_free=%llu\n",
                      FIRMWARE_VERSION,
                      static_cast<int>(M5.getBoard()),
                      static_cast<unsigned long long>(SD.totalBytes() - SD.usedBytes()));
    }
    delay(10);
}
