import unittest

from clanker_hydrus_tagger.tag_namespaces import (
    format_model_output_tags,
    format_model_rating_tag,
    get_model_tag_category,
    parse_model_namespace_config,
    parse_model_skip_existing_namespaces,
)


class ParseModelNamespaceConfigTests(unittest.TestCase):
    def test_auto_uses_category_defaults(self):
        config = parse_model_namespace_config("auto")

        self.assertEqual(config["general"], "")
        self.assertEqual(config["character"], "character")
        self.assertEqual(config["artist"], "creator")
        self.assertEqual(config["rating"], "rating")

    def test_all_plain_disables_namespaces_without_skipping_tags(self):
        config = parse_model_namespace_config("all=")

        self.assertTrue(all(value == "" for value in config.values()))

    def test_creator_alias_updates_artist_namespace(self):
        config = parse_model_namespace_config("creator=artist")

        self.assertEqual(config["artist"], "artist")


class ParseModelSkipExistingNamespacesTests(unittest.TestCase):
    def test_empty_value_disables_feature(self):
        self.assertEqual(parse_model_skip_existing_namespaces(""), set())

    def test_aliases_and_all_are_supported(self):
        self.assertEqual(
            parse_model_skip_existing_namespaces("creator,character"),
            {"artist", "character"},
        )
        self.assertEqual(
            parse_model_skip_existing_namespaces("all"),
            {
                "general",
                "character",
                "copyright",
                "artist",
                "meta",
                "species",
                "lore",
                "rating",
                "year",
            },
        )


class FormatModelOutputTagsTests(unittest.TestCase):
    def test_model_tag_category_resolves_numeric_aliases(self):
        tag_to_category = {
            "mika_pikazo": "1",
            "hakurei_reimu": "4",
            "sensitive": "9",
        }

        self.assertEqual(get_model_tag_category("mika_pikazo", tag_to_category), "artist")
        self.assertEqual(get_model_tag_category("hakurei_reimu", tag_to_category), "character")

    def test_numeric_danbooru_categories_are_namespaced(self):
        config = parse_model_namespace_config("auto")
        tags = ["1girl", "hakurei_reimu", "touhou", "mika_pikazo"]
        tag_to_category = {
            "1girl": "0",
            "hakurei_reimu": "4",
            "touhou": "3",
            "mika_pikazo": "1",
            "sensitive": "9",
        }

        formatted = format_model_output_tags(tags, tag_to_category, config)

        self.assertEqual(
            formatted,
            [
                "1girl",
                "character:hakurei reimu",
                "copyright:touhou",
                "creator:mika pikazo",
            ],
        )

    def test_numeric_e621_categories_are_namespaced(self):
        config = parse_model_namespace_config("auto")
        tags = ["dragon", "original_character", "fox", "shoop"]
        tag_to_category = {
            "dragon": "0",
            "original_character": "4",
            "fox": "5",
            "shoop": "1",
            "lore_dump": "7",
        }

        formatted = format_model_output_tags(tags, tag_to_category, config)

        self.assertEqual(
            formatted,
            [
                "dragon",
                "character:original character",
                "species:fox",
                "creator:shoop",
            ],
        )

    def test_camie_categories_become_hydrus_namespaces(self):
        config = parse_model_namespace_config("auto")
        tags = ["1girl", "hakurei_reimu", "touhou", "translated", "year_2014"]
        tag_to_category = {
            "1girl": "general",
            "hakurei_reimu": "character",
            "touhou": "copyright",
            "translated": "meta",
            "year_2014": "year",
        }

        formatted = format_model_output_tags(tags, tag_to_category, config)

        self.assertEqual(
            formatted,
            [
                "1girl",
                "character:hakurei reimu",
                "copyright:touhou",
                "meta:translated",
                "year:2014",
            ],
        )

    def test_skipped_categories_are_omitted(self):
        config = parse_model_namespace_config("meta=skip,artist=skip")
        tags = ["translated", "mika_pikazo"]
        tag_to_category = {
            "translated": "meta",
            "mika_pikazo": "artist",
        }

        formatted = format_model_output_tags(tags, tag_to_category, config)

        self.assertEqual(formatted, [])

    def test_rating_tag_is_normalized(self):
        config = parse_model_namespace_config("auto")

        self.assertEqual(format_model_rating_tag("rating_explicit", config), "rating:explicit")


if __name__ == "__main__":
    unittest.main()
