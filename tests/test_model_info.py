import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clanker_hydrus_tagger import model_info


class LoadModelInfoTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_cwd = os.getcwd()
        os.chdir(self.tempdir.name)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.tempdir.cleanup()

    def test_uses_local_info_json_when_present(self):
        info_path = Path("model") / "JTP-3" / "info.json"
        info_path.parent.mkdir(parents=True, exist_ok=True)
        expected = {"modelname": "JTP-3", "repo_id": "0xk1ru/jtp-3-onnx"}
        info_path.write_text(json.dumps(expected), encoding="utf-8")

        with patch("clanker_hydrus_tagger.model_info.onnx_loader.ensure_huggingface_files") as mocked_download:
            result = model_info.load_model_info("JTP-3")

        self.assertEqual(result, expected)
        mocked_download.assert_not_called()

    def test_downloads_missing_info_json_from_bootstrap_repo(self):
        expected = {"modelname": "JTP-3", "repo_id": "0xk1ru/jtp-3-onnx"}
        model_dir = Path("model") / "JTP-3"
        model_dir.mkdir(parents=True, exist_ok=True)

        def fake_download(*args, **kwargs):
            info_path = model_dir / "info.json"
            info_path.write_text(json.dumps(expected), encoding="utf-8")

        with patch(
            "clanker_hydrus_tagger.model_info.onnx_loader.ensure_huggingface_files",
            side_effect=fake_download,
        ) as mocked_download:
            result = model_info.load_model_info("JTP-3")

        self.assertEqual(result, expected)
        mocked_download.assert_called_once_with(
            model_dir,
            "0xk1ru/jtp-3-onnx",
            ["info.json"],
            revision="main",
            model_name="JTP-3",
        )

    def test_unknown_model_without_info_json_raises_clear_error(self):
        with self.assertRaisesRegex(
            FileNotFoundError,
            "no bootstrap Hugging Face repo is configured",
        ):
            model_info.load_model_info("unknown-model")


if __name__ == "__main__":
    unittest.main()
