#include "RecorderCore.h"

#include <algorithm>
#include <cstring>
#include <limits>

namespace recorder {

WavHeader makeWavHeader(std::uint32_t pcmBytes) {
    WavHeader header{};
    std::memcpy(header.riff, "RIFF", 4);
    header.riffSize = 36 + pcmBytes;
    std::memcpy(header.wave, "WAVE", 4);
    std::memcpy(header.fmt, "fmt ", 4);
    header.fmtSize = 16;
    header.audioFormat = 1;
    header.channels = kChannels;
    header.sampleRate = kSampleRate;
    header.byteRate = kSampleRate * kChannels * (kBitsPerSample / 8);
    header.blockAlign = kChannels * (kBitsPerSample / 8);
    header.bitsPerSample = kBitsPerSample;
    std::memcpy(header.data, "data", 4);
    header.dataSize = pcmBytes;
    return header;
}

std::uint32_t recoverablePcmBytes(std::uint64_t fileSize) {
    if (fileSize <= sizeof(WavHeader)) return 0;
    std::uint64_t pcmBytes = (fileSize - sizeof(WavHeader)) & ~std::uint64_t{1};
    return static_cast<std::uint32_t>(std::min<std::uint64_t>(pcmBytes, std::numeric_limits<std::uint32_t>::max()));
}

std::uint32_t samplesBeforeRotation(std::uint32_t samplesInSegment, std::uint32_t incomingSamples) {
    if (samplesInSegment >= kSegmentSamples || incomingSamples == 0) return 0;
    return std::min(incomingSamples, kSegmentSamples - samplesInSegment);
}

}  // namespace recorder
