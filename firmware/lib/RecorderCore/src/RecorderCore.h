#pragma once

#include <cstdint>

namespace recorder {

constexpr std::uint32_t kSampleRate = 16000;
constexpr std::uint16_t kChannels = 1;
constexpr std::uint16_t kBitsPerSample = 16;
constexpr std::uint32_t kSegmentSeconds = 5 * 60;
constexpr std::uint32_t kSegmentSamples = kSampleRate * kSegmentSeconds;

#pragma pack(push, 1)
struct WavHeader {
    char riff[4];
    std::uint32_t riffSize;
    char wave[4];
    char fmt[4];
    std::uint32_t fmtSize;
    std::uint16_t audioFormat;
    std::uint16_t channels;
    std::uint32_t sampleRate;
    std::uint32_t byteRate;
    std::uint16_t blockAlign;
    std::uint16_t bitsPerSample;
    char data[4];
    std::uint32_t dataSize;
};
#pragma pack(pop)

static_assert(sizeof(WavHeader) == 44, "WAV header must be 44 bytes");

WavHeader makeWavHeader(std::uint32_t pcmBytes);
std::uint32_t recoverablePcmBytes(std::uint64_t fileSize);
std::uint32_t samplesBeforeRotation(std::uint32_t samplesInSegment, std::uint32_t incomingSamples);

}  // namespace recorder
