from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


class FirmwareCoreTests(unittest.TestCase):
    def test_wav_recovery_and_two_hour_rotation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "firmware/lib/RecorderCore/src/RecorderCore.cpp"
        include = root / "firmware/lib/RecorderCore/src"
        harness = r'''
#include "RecorderCore.h"
#include <cassert>
#include <cstdint>

int main() {
    using namespace recorder;
    static_assert(sizeof(WavHeader) == 44);
    const auto header = makeWavHeader(1'920'000);
    assert(header.dataSize == 1'920'000);
    assert(header.riffSize == 1'920'036);
    assert(recoverablePcmBytes(1'920'045) == 1'920'000);

    std::uint64_t remaining = static_cast<std::uint64_t>(kSampleRate) * 2 * 60 * 60;
    std::uint32_t in_segment = 0;
    unsigned segments = 0;
    while (remaining) {
        const std::uint32_t incoming = remaining > 1024 ? 1024 : static_cast<std::uint32_t>(remaining);
        std::uint32_t consumed = 0;
        while (consumed < incoming) {
            const std::uint32_t count = samplesBeforeRotation(in_segment, incoming - consumed);
            assert(count > 0);
            in_segment += count;
            consumed += count;
            if (in_segment == kSegmentSamples) {
                ++segments;
                in_segment = 0;
            }
        }
        remaining -= incoming;
    }
    if (in_segment) ++segments;
    assert(segments == 24);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            harness_path = Path(directory) / "harness.cpp"
            executable = Path(directory) / "firmware-core-test"
            harness_path.write_text(harness, encoding="utf-8")
            compile_result = subprocess.run(
                ["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(include), str(harness_path), str(source), "-o", str(executable)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(executable)], check=False)
            self.assertEqual(run_result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
