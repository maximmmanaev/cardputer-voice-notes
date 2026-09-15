from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mac-agent/src"))

from cardputer_agent.media import convert_and_validate


class AgentMediaTests(unittest.TestCase):
    def test_converts_pcm_wav_to_valid_telegram_voice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            destination = root / "voice.ogg"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"".join(
                    struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * i / 16_000)))
                    for i in range(16_000)
                ))
            metadata = convert_and_validate(source, destination)
            self.assertEqual(metadata.codec, "opus")
            self.assertEqual(metadata.channels, 1)
            self.assertEqual(metadata.sample_rate, 48_000)
            self.assertAlmostEqual(metadata.duration, 1.0, delta=0.05)
            self.assertGreater(destination.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
