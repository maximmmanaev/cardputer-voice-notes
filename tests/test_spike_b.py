from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.spike_b import ProbeError, build_ffmpeg_command, normalize_group_id, validate_ogg


class SpikeBTests(unittest.TestCase):
    def test_builds_voice_compatible_opus_command(self) -> None:
        command = build_ffmpeg_command(Path("input.wav"), Path("output.ogg"))
        self.assertIn("libopus", command)
        self.assertIn("24000", command)
        self.assertIn("48000", command)
        self.assertEqual(command[-1], "output.ogg")

    def test_accepts_negative_group_ids(self) -> None:
        self.assertEqual(normalize_group_id("-12345"), -12345)
        self.assertEqual(normalize_group_id("-1001234567890"), -1001234567890)

    def test_rejects_non_group_id(self) -> None:
        with self.assertRaises(ValueError):
            normalize_group_id("12345")

    def test_validates_ogg_opus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ogg = Path(directory) / "voice.ogg"
            ogg.write_bytes(b"not empty")
            probe = {
                "streams": [{
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "sample_rate": "48000",
                    "channels": 1,
                    "duration": "60.0065",
                }],
                "format": {"format_name": "ogg", "duration": "60.0065"},
            }
            with patch("scripts.spike_b.run_ffprobe", return_value=probe):
                result = validate_ogg(ogg, source_duration=60.0)
        self.assertEqual(result["codec"], "opus")
        self.assertEqual(result["channels"], 1)

    def test_rejects_non_opus_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ogg = Path(directory) / "voice.ogg"
            ogg.write_bytes(b"not empty")
            probe = {
                "streams": [{"codec_type": "audio", "codec_name": "vorbis", "channels": 1}],
                "format": {"format_name": "ogg", "duration": "60.0"},
            }
            with patch("scripts.spike_b.run_ffprobe", return_value=probe):
                with self.assertRaises(ProbeError):
                    validate_ogg(ogg, source_duration=60.0)


if __name__ == "__main__":
    unittest.main()
