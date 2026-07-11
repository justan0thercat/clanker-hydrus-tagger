import unittest

from clanker_hydrus_tagger import __main__ as main
from clanker_hydrus_tagger.tag_namespaces import (
    filter_model_tags_by_existing_namespaces,
    parse_model_namespace_config,
)


class FilterModelTagsByExistingNamespacesTests(unittest.TestCase):
    def test_skips_artist_when_creator_namespace_already_exists(self):
        existing_tags = {"creator:trusted artist"}
        tag_to_category = {
            "mika_pikazo": "artist",
            "1girl": "general",
        }

        filtered = filter_model_tags_by_existing_namespaces(
            ["mika_pikazo", "1girl"],
            existing_tags,
            tag_to_category,
            parse_model_namespace_config("auto"),
            {"artist"},
        )

        self.assertEqual(filtered, ["1girl"])

    def test_keeps_tags_when_namespace_is_plain(self):
        existing_tags = {"creator:trusted artist"}
        tag_to_category = {
            "1girl": "general",
        }

        filtered = filter_model_tags_by_existing_namespaces(
            ["1girl"],
            existing_tags,
            tag_to_category,
            parse_model_namespace_config("all="),
            {"general"},
        )

        self.assertEqual(filtered, ["1girl"])

    def test_keeps_tags_when_service_has_no_matching_namespace(self):
        existing_tags = {"meta:translated"}
        tag_to_category = {
            "hakurei_reimu": "character",
        }

        filtered = filter_model_tags_by_existing_namespaces(
            ["hakurei_reimu"],
            existing_tags,
            tag_to_category,
            parse_model_namespace_config("auto"),
            {"character"},
        )

        self.assertEqual(filtered, ["hakurei_reimu"])


class FormatModelLoadErrorTests(unittest.TestCase):
    def test_mentions_missing_model_folder(self):
        message = main.format_model_load_error("JTP-3", False, FileNotFoundError("Model not found: model\\JTP-3\\model.onnx"))

        self.assertIn('Model "JTP-3" failed to initialize.', message)
        self.assertIn("model\\JTP-3", message)
        self.assertIn("GPU mode", message)

    def test_mentions_cpu_fallback_for_cuda_failures(self):
        message = main.format_model_load_error(
            "wd-eva02-large-tagger-v3",
            False,
            RuntimeError("LoadLibrary failed with error 126 while loading CUDAExecutionProvider"),
        )

        self.assertIn("install_cpu.bat", message)
        self.assertIn("install_gpu.bat", message)

    def test_mentions_huggingface_for_download_failures(self):
        message = main.format_model_load_error(
            "camie-tagger",
            True,
            RuntimeError("Couldn't reach Hugging Face while downloading model.onnx: timed out"),
        )

        self.assertIn("Hugging Face", message)
        self.assertNotIn("GPU mode", message)


if __name__ == "__main__":
    unittest.main()
