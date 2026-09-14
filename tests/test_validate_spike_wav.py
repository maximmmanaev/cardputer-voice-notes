import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from scripts.validate_spike_wav import ProbeError, validate_wav


class ValidateSpikeWavTests(unittest.TestCase):
    def test_accepts_pcm_s16le_mono_16khz_with_expected_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "spike.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16_000)
                wav.writeframes(b"\0\0" * 16_000)

            fake_probe = {
                "streams": [{
                    "codec_name": "pcm_s16le",
                    "sample_rate": "16000",
                    "channels": 1,
                    "bits_per_sample": 16,
                    "duration": "1.000000",
                }],
                "format": {"format_name": "wav", "duration": "1.000000"},
            }
            with patch("scripts.validate_spike_wav.run_ffprobe", return_value=fake_probe):
                result = validate_wav(wav_path, expected_duration=1.0, tolerance=0.02)

            self.assertEqual(result["codec"], "pcm_s16le")
            self.assertEqual(result["sample_rate"], 16_000)
            self.assertEqual(result["channels"], 1)

    def test_rejects_wrong_sample_rate(self):
        fake_probe = {
            "streams": [{
                "codec_name": "pcm_s16le",
                "sample_rate": "8000",
                "channels": 1,
                "bits_per_sample": 16,
                "duration": "60.0",
            }],
            "format": {"format_name": "wav", "duration": "60.0"},
        }
        with patch("scripts.validate_spike_wav.run_ffprobe", return_value=fake_probe):
            with self.assertRaisesRegex(ProbeError, "sample rate"):
                validate_wav(Path("recording.wav"), expected_duration=60.0)


if __name__ == "__main__":
    unittest.main()

