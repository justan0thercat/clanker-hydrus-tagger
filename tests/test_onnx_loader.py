import unittest

from clanker_hydrus_tagger import onnx_loader


class DownloadProgressFormattingTests(unittest.TestCase):
    def test_formats_progress_line_with_bar_and_speed(self):
        line = onnx_loader._format_download_progress_line(
            "model.onnx",
            downloaded=5 * 1024 * 1024,
            total_bytes=10 * 1024 * 1024,
            started_at=0.0,
            current_time=2.0,
        )

        self.assertIn("model.onnx:", line)
        self.assertIn("[", line)
        self.assertIn("]", line)
        self.assertIn(" 50.0%", line)
        self.assertIn("5.00 MB/10.0 MB", line)
        self.assertIn("2.50 MB/s", line)

    def test_formats_progress_line_without_total_size(self):
        line = onnx_loader._format_download_progress_line(
            "selected_tags.csv",
            downloaded=3 * 1024 * 1024,
            total_bytes=None,
            started_at=0.0,
            current_time=3.0,
        )

        self.assertIn("selected_tags.csv:", line)
        self.assertIn("3.00 MB downloaded", line)
        self.assertIn("1.00 MB/s", line)


if __name__ == "__main__":
    unittest.main()
