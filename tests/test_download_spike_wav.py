from __future__ import annotations

import io
import unittest

from scripts.download_spike_wav import DownloadError, parse_begin_line, read_exact


class DownloadSpikeWavTests(unittest.TestCase):
    def test_parses_begin_line(self) -> None:
        path, size = parse_begin_line(
            b"SPIKE_A_DOWNLOAD_BEGIN path=/spike-a-0f3c-002.wav size=1920044\r\n"
        )
        self.assertEqual(path, "/spike-a-0f3c-002.wav")
        self.assertEqual(size, 1_920_044)

    def test_rejects_unexpected_line(self) -> None:
        with self.assertRaises(DownloadError):
            parse_begin_line(b"SPIKE_A_STATUS=IDLE\n")

    def test_read_exact_handles_partial_reads(self) -> None:
        stream = io.BytesIO(b"abcdef")
        self.assertEqual(read_exact(stream, 6, chunk_size=2), b"abcdef")

    def test_read_exact_rejects_truncated_payload(self) -> None:
        with self.assertRaises(DownloadError):
            read_exact(io.BytesIO(b"abc"), 4)


if __name__ == "__main__":
    unittest.main()
