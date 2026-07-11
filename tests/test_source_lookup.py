import os
import sys
import types
import unittest
from collections import defaultdict
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, call, patch

import click

sys.modules.setdefault(
    "hydrus_api",
    types.SimpleNamespace(
        Client=object,
        ConnectionError=Exception,
        InsufficientAccess=Exception,
        APIError=Exception,
    ),
)

from clanker_hydrus_tagger import source_lookup
from clanker_hydrus_tagger import source_lookup_backends
from clanker_hydrus_tagger import source_lookup_common


class ParseLookupEntryTests(unittest.TestCase):
    def test_parses_url(self):
        self.assertEqual(
            source_lookup.parse_lookup_entry("https://example.com/post/1"),
            ("url", "https://example.com/post/1"),
        )

    def test_parses_hashes_and_file_id(self):
        self.assertEqual(
            source_lookup.parse_lookup_entry("a" * 32),
            ("md5", "a" * 32),
        )
        self.assertEqual(
            source_lookup.parse_lookup_entry("b" * 64),
            ("sha256", "b" * 64),
        )
        self.assertEqual(
            source_lookup.parse_lookup_entry("12345"),
            ("file_id", "12345"),
        )

    def test_rejects_invalid_value(self):
        with self.assertRaises(click.ClickException):
            source_lookup.parse_lookup_entry("not-a-valid-lookup")


class ReadLookupLinesTests(unittest.TestCase):
    def test_reports_missing_lookup_file(self):
        with self.assertRaisesRegex(click.ClickException, 'Lookup file "missing.txt" was not found'):
            source_lookup_common.read_lookup_lines("missing.txt")

    def test_reports_empty_lookup_file(self):
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            path = handle.name
            handle.write("\n# comment only\n")

        try:
            with self.assertRaisesRegex(click.ClickException, 'Lookup file ".*" is empty'):
                source_lookup_common.read_lookup_lines(path)
        finally:
            os.remove(path)


class ParseRequestedSitesTests(unittest.TestCase):
    def test_normalizes_aliases(self):
        self.assertEqual(
            source_lookup.parse_requested_sites("danbooru.donmai.us e621.net"),
            ["danbooru", "e621"],
        )

    def test_rejects_unknown_site(self):
        with self.assertRaises(click.ClickException):
            source_lookup.parse_requested_sites("danbooru unknown-site")


class ParseNamespaceConfigTests(unittest.TestCase):
    def test_supports_all_and_aliases(self):
        config = source_lookup.parse_namespace_config("all=src,copyright=franchise")
        self.assertEqual(config["general"], "src")
        self.assertEqual(config["series"], "franchise")
        self.assertEqual(config["artist"], "src")

    def test_rejects_unknown_group(self):
        with self.assertRaises(click.ClickException):
            source_lookup.parse_namespace_config("badgroup=test")


class BuildMd5LookupMapTests(unittest.TestCase):
    def test_uses_metadata_record_hashes(self):
        client = object()
        metadata_records = [
            {"hash": "A" * 64},
            {"hash": "b" * 64},
            {"hash": "A" * 64},
            {"file_id": 5},
        ]

        with patch.object(source_lookup, "resolve_hashes", return_value={"a" * 64: "m1", "b" * 64: "m2"}) as resolve_hashes:
            result = source_lookup.build_md5_lookup_map(client, metadata_records)

        self.assertEqual(result, {"a" * 64: "m1", "b" * 64: "m2"})
        resolve_hashes.assert_called_once_with(client, ["a" * 64, "b" * 64], "sha256", "md5")


class ParallelismPolicyTests(unittest.TestCase):
    def test_keeps_inner_parallelism_enabled_for_large_batches(self):
        self.assertTrue(source_lookup.should_parallelize_inner_lookups(1269))


class FlushTagBatchesTests(unittest.TestCase):
    def test_flushes_grouped_tags_and_returns_applied_counts(self):
        client = Mock()
        hashes_by_tag_tuple = defaultdict(
            list,
            {
                ("artist:one",): ["a" * 64, "b" * 64],
                ("artist:two", "year:2024"): ["c" * 64],
            },
        )

        flushed_records, flushed_tags = source_lookup.flush_tag_batches(
            client,
            "A.I. Tags",
            hashes_by_tag_tuple,
        )

        self.assertEqual(flushed_records, 3)
        self.assertEqual(flushed_tags, 4)
        self.assertEqual(dict(hashes_by_tag_tuple), {})
        client.add_tags.assert_has_calls(
            [
                call(
                    hashes=["a" * 64, "b" * 64],
                    service_names_to_tags={"A.I. Tags": ["artist:one"]},
                ),
                call(
                    hashes=["c" * 64],
                    service_names_to_tags={"A.I. Tags": ["artist:two", "year:2024"]},
                ),
            ],
            any_order=True,
        )


class RunLookupTagFlushTests(unittest.TestCase):
    def test_progress_reports_only_flushed_tags(self):
        client = Mock()
        record_hash = "a" * 64
        echo_lines = []
        result = {
            "record_hash": record_hash,
            "urls": [],
            "md5_hash": None,
            "md5_lookup_attempted": False,
            "facts": source_lookup.make_source_facts(),
            "url_has_matches": False,
            "hash_has_matches": False,
            "tags_to_add": ["artist:test artist"],
            "extra_source": None,
            "result_message": "artist:test artist",
            "report_line": "report-line",
        }

        with patch.object(source_lookup, "resolve_lookup_file", return_value=({record_hash: ["sha256:" + record_hash]}, {}, [])), \
             patch.object(source_lookup, "fetch_metadata_records", return_value=[{"hash": record_hash}]), \
             patch.object(source_lookup, "build_md5_lookup_map", return_value={}), \
             patch.object(source_lookup, "process_lookup_record", return_value=result), \
             patch.object(click, "echo", side_effect=lambda line, **kwargs: echo_lines.append(line)):
            source_lookup.run_lookup(
                client=client,
                mode="artist",
                lookupfile="hashes.txt",
                tag_service="A.I. Tags",
                namespace="artist",
                privacy=True,
                timeout=5,
                report=None,
                doublecheck_file_system=False,
                sites="danbooru",
            )

        client.add_tags.assert_called_once_with(
            hashes=[record_hash],
            service_names_to_tags={"A.I. Tags": ["artist:test artist"]},
        )
        self.assertTrue(any("tagged 1" in line for line in echo_lines))
        self.assertTrue(any("1 tagged" in line for line in echo_lines))

    def test_progress_reports_source_request_events(self):
        client = Mock()
        record_hash = "a" * 64
        echo_lines = []
        result = {
            "record_hash": record_hash,
            "urls": [],
            "md5_hash": None,
            "md5_lookup_attempted": False,
            "facts": source_lookup.make_source_facts(),
            "url_has_matches": False,
            "hash_has_matches": False,
            "tags_to_add": [],
            "extra_source": None,
            "result_message": "nothing found",
            "report_line": "report-line",
        }

        with patch.object(source_lookup, "resolve_lookup_file", return_value=({record_hash: ["sha256:" + record_hash]}, {}, [])), \
             patch.object(source_lookup, "fetch_metadata_records", return_value=[{"hash": record_hash}]), \
             patch.object(source_lookup, "build_md5_lookup_map", return_value={}), \
             patch.object(source_lookup, "process_lookup_record", return_value=result), \
             patch.object(source_lookup.SOURCE_REQUEST_CONTROLLER, "drain_events", side_effect=[[
                 "gelbooru: rate limited (HTTP 429), backing off for 5.0s",
                 "gelbooru: rate limited (HTTP 429), backing off for 5.0s",
             ], []]), \
             patch.object(click, "echo", side_effect=lambda line, **kwargs: echo_lines.append(line)):
            source_lookup.run_lookup(
                client=client,
                mode="artist",
                lookupfile="hashes.txt",
                tag_service="A.I. Tags",
                namespace="artist",
                privacy=True,
                timeout=5,
                report=None,
                doublecheck_file_system=False,
                sites="gelbooru",
            )

        self.assertTrue(
            any(
                line == "Notice: gelbooru: rate limited (HTTP 429), backing off for 5.0s (x2)"
                for line in echo_lines
            )
        )


class SourceRequestEventFormattingTests(unittest.TestCase):
    def test_summarizes_repeated_events_while_preserving_order(self):
        self.assertEqual(
            source_lookup.summarize_source_request_events([
                "gelbooru: transient network error",
                "gelbooru: transient network error",
                "gelbooru: throttling requests",
                "gelbooru: transient network error",
            ]),
            [
                "gelbooru: transient network error (x3)",
                "gelbooru: throttling requests",
            ],
        )


class SourceRequestControllerTests(unittest.TestCase):
    def tearDown(self):
        source_lookup_backends.SOURCE_REQUEST_CONTROLLER.reset()

    def test_opens_circuit_after_consecutive_transient_failures(self):
        controller = source_lookup_backends.SourceRequestController()
        url = "https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&tags=md5%3Aabc"

        with patch.object(
            source_lookup_backends,
            "SOURCE_RETRY_MAX_ATTEMPTS",
            0,
        ), patch.object(
            source_lookup_backends,
            "SOURCE_CIRCUIT_BREAKER_THRESHOLD",
            2,
        ), patch.object(
            source_lookup_backends,
            "SOURCE_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
            30.0,
        ), patch(
            "clanker_hydrus_tagger.source_lookup_backends.urlopen",
            side_effect=URLError("boom"),
        ):
            with self.assertRaises(URLError):
                controller.fetch_json(url, timeout=5)
            with self.assertRaises(URLError):
                controller.fetch_json(url, timeout=5)

            with self.assertRaises(source_lookup_backends.TemporarySourceSuspensionError) as suspended_exc:
                controller.fetch_json(url, timeout=5)

        self.assertIn("temporarily suspended", str(suspended_exc.exception))
        self.assertEqual(
            controller.snapshot(),
            {
                "requests": 2,
                "retries": 0,
                "rate_limits": 0,
                "recovered": 0,
                "suspensions": 1,
                "suspended_skips": 1,
            },
        )
        self.assertEqual(
            controller.drain_events(),
            [
                "gelbooru: temporarily suspending requests for 30.0s after 2 consecutive failures",
                "gelbooru: skipped 1 request while temporarily suspended",
            ],
        )

    def test_success_resets_circuit_breaker_failure_streak(self):
        controller = source_lookup_backends.SourceRequestController()
        url = "https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&tags=md5%3Aabc"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"[]"

        with patch.object(
            source_lookup_backends,
            "SOURCE_RETRY_MAX_ATTEMPTS",
            0,
        ), patch.object(
            source_lookup_backends,
            "SOURCE_CIRCUIT_BREAKER_THRESHOLD",
            2,
        ), patch.object(
            source_lookup_backends,
            "SOURCE_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
            30.0,
        ), patch(
            "clanker_hydrus_tagger.source_lookup_backends.urlopen",
            side_effect=[URLError("boom"), FakeResponse(), URLError("boom"), URLError("boom")],
        ):
            with self.assertRaises(URLError):
                controller.fetch_json(url, timeout=5)

            self.assertEqual(controller.fetch_json(url, timeout=5), [])

            with self.assertRaises(URLError):
                controller.fetch_json(url, timeout=5)
            with self.assertRaises(URLError):
                controller.fetch_json(url, timeout=5)

            with self.assertRaises(source_lookup_backends.TemporarySourceSuspensionError) as suspended_exc:
                controller.fetch_json(url, timeout=5)

        self.assertIn("temporarily suspended", str(suspended_exc.exception))


class ProcessLookupRecordTests(unittest.TestCase):
    def test_passes_stop_condition_to_url_lookup(self):
        record = {"hash": "A" * 64, "known_urls": ["https://example.com/post/1"]}
        url_facts = source_lookup.make_source_facts()

        with patch.object(source_lookup, "extract_known_urls", return_value=record["known_urls"]), \
             patch.object(source_lookup, "collect_source_facts", return_value=url_facts) as collect_source_facts, \
             patch.object(source_lookup, "lookup_sources_by_md5", return_value=source_lookup.make_source_facts()):
            source_lookup.process_lookup_record(
                record=record,
                hash_inputs={"a" * 64: ["sha256:" + "a" * 64]},
                file_id_inputs_by_hash={},
                md5_by_sha256={},
                tag_service="A.I. Tags",
                timeout=5,
                requested_sites=["danbooru"],
                namespace="artist",
                mode="artist",
                url_cache={},
                hash_cache={},
                allow_parallel_lookups=False,
            )

        collect_source_facts.assert_called_once_with(
            record["known_urls"],
            5,
            {},
            allow_parallel=False,
            stop_when=unittest.mock.ANY,
        )

    def test_skips_md5_fallback_when_url_facts_already_satisfy_mode(self):
        record = {"hash": "A" * 64, "known_urls": ["https://example.com/post/1"]}
        url_facts = source_lookup.make_source_facts()
        url_facts["artists"] = ["artist name"]
        hash_facts = source_lookup.make_source_facts()

        with patch.object(source_lookup, "extract_known_urls", return_value=record["known_urls"]), \
             patch.object(source_lookup, "collect_source_facts", return_value=url_facts), \
             patch.object(source_lookup, "lookup_sources_by_md5", return_value=hash_facts) as lookup_md5:
            result = source_lookup.process_lookup_record(
                record=record,
                hash_inputs={"a" * 64: ["sha256:" + "a" * 64]},
                file_id_inputs_by_hash={},
                md5_by_sha256={"a" * 64: "m" * 32},
                tag_service="A.I. Tags",
                timeout=5,
                requested_sites=["danbooru"],
                namespace="artist",
                mode="artist",
                url_cache={},
                hash_cache={},
                allow_parallel_lookups=False,
            )

        self.assertFalse(result["md5_lookup_attempted"])
        self.assertEqual(result["tags_to_add"], ["artist:artist name"])
        lookup_md5.assert_not_called()

    def test_runs_md5_fallback_when_urls_do_not_produce_data(self):
        record = {"hash": "A" * 64, "known_urls": ["https://example.com/post/1"]}
        url_facts = source_lookup.make_source_facts()
        hash_facts = source_lookup.make_source_facts()
        hash_facts["artists"] = ["fallback artist"]
        hash_facts["sites"] = ["danbooru"]

        with patch.object(source_lookup, "extract_known_urls", return_value=record["known_urls"]), \
             patch.object(source_lookup, "collect_source_facts", return_value=url_facts), \
             patch.object(source_lookup, "lookup_sources_by_md5", return_value=hash_facts) as lookup_md5:
            result = source_lookup.process_lookup_record(
                record=record,
                hash_inputs={"a" * 64: ["sha256:" + "a" * 64]},
                file_id_inputs_by_hash={},
                md5_by_sha256={"a" * 64: "m" * 32},
                tag_service="A.I. Tags",
                timeout=5,
                requested_sites=["danbooru"],
                namespace="artist",
                mode="artist",
                url_cache={},
                hash_cache={},
                allow_parallel_lookups=False,
            )

        self.assertTrue(result["md5_lookup_attempted"])
        self.assertEqual(result["tags_to_add"], ["artist:fallback artist"])
        lookup_md5.assert_called_once_with(
            "m" * 32,
            5,
            {},
            ["danbooru"],
            allow_parallel=False,
            stop_when=unittest.mock.ANY,
        )

    def test_search_all_skips_md5_when_url_facts_cover_requested_outputs(self):
        record = {"hash": "A" * 64, "known_urls": ["https://example.com/post/1"]}
        url_facts = source_lookup.make_source_facts()
        url_facts["general"] = ["test tag"]
        url_facts["tags"] = ["test tag"]
        url_facts["ratings"] = ["safe"]
        url_facts["years"] = [2024]
        url_facts["sites"] = ["danbooru"]
        url_facts["filetypes"] = ["image/jpeg"]
        hash_facts = source_lookup.make_source_facts()

        namespace = source_lookup.parse_namespace_config(
            "general=,rating=rating,year=year,site=source,filetype=filetype,artist=skip"
        )

        with patch.object(source_lookup, "extract_known_urls", return_value=record["known_urls"]), \
             patch.object(source_lookup, "collect_source_facts", return_value=url_facts), \
             patch.object(source_lookup, "lookup_sources_by_md5", return_value=hash_facts) as lookup_md5:
            result = source_lookup.process_lookup_record(
                record=record,
                hash_inputs={"a" * 64: ["sha256:" + "a" * 64]},
                file_id_inputs_by_hash={},
                md5_by_sha256={"a" * 64: "m" * 32},
                tag_service="A.I. Tags",
                timeout=5,
                requested_sites=["danbooru"],
                namespace=namespace,
                mode="all",
                url_cache={},
                hash_cache={},
                allow_parallel_lookups=False,
            )

        self.assertFalse(result["md5_lookup_attempted"])
        self.assertIn("test tag", result["tags_to_add"])
        self.assertIn("rating:safe", result["tags_to_add"])
        lookup_md5.assert_not_called()

    def test_search_all_uses_md5_when_url_facts_miss_enabled_outputs(self):
        record = {"hash": "A" * 64, "known_urls": ["https://example.com/post/1"]}
        url_facts = source_lookup.make_source_facts()
        url_facts["general"] = ["test tag"]
        url_facts["tags"] = ["test tag"]
        url_facts["ratings"] = ["safe"]
        url_facts["sites"] = ["danbooru"]
        url_facts["filetypes"] = ["image/jpeg"]
        hash_facts = source_lookup.make_source_facts()
        hash_facts["years"] = [2024]
        hash_facts["sites"] = ["danbooru"]

        namespace = source_lookup.parse_namespace_config(
            "general=,rating=rating,year=year,site=source,filetype=filetype,artist=skip"
        )

        with patch.object(source_lookup, "extract_known_urls", return_value=record["known_urls"]), \
             patch.object(source_lookup, "collect_source_facts", return_value=url_facts), \
             patch.object(source_lookup, "lookup_sources_by_md5", return_value=hash_facts) as lookup_md5:
            result = source_lookup.process_lookup_record(
                record=record,
                hash_inputs={"a" * 64: ["sha256:" + "a" * 64]},
                file_id_inputs_by_hash={},
                md5_by_sha256={"a" * 64: "m" * 32},
                tag_service="A.I. Tags",
                timeout=5,
                requested_sites=["danbooru"],
                namespace=namespace,
                mode="all",
                url_cache={},
                hash_cache={},
                allow_parallel_lookups=False,
            )

        self.assertTrue(result["md5_lookup_attempted"])
        self.assertIn("year:2024", result["tags_to_add"])
        lookup_md5.assert_called_once()


class SourceLookupModeCoverageTests(unittest.TestCase):
    def test_all_mode_requires_enabled_extra_fields(self):
        facts = source_lookup.make_source_facts()
        facts["general"] = ["test tag"]
        facts["tags"] = ["test tag"]
        facts["ratings"] = ["safe"]
        facts["sites"] = ["danbooru"]
        facts["filetypes"] = ["image/jpeg"]

        namespace = source_lookup.parse_namespace_config(
            "general=,rating=rating,year=year,site=source,filetype=filetype,artist=skip"
        )

        self.assertFalse(
            source_lookup.source_facts_satisfy_lookup_mode(
                "all",
                facts,
                {},
                namespace,
            )
        )

        facts["years"] = [2024]
        self.assertTrue(
            source_lookup.source_facts_satisfy_lookup_mode(
                "all",
                facts,
                {},
                namespace,
            )
        )


class LookupBackendsEarlyStopTests(unittest.TestCase):
    def test_collect_source_facts_stops_after_first_satisfying_url(self):
        first = source_lookup.make_source_facts()
        first["artists"] = ["artist one"]
        first["site"] = "first.example"

        second = source_lookup.make_source_facts()
        second["artists"] = ["artist two"]
        second["site"] = "second.example"

        with patch.object(
            source_lookup_backends,
            "lookup_source_url",
            side_effect=[first, second],
        ) as lookup_source_url:
            facts = source_lookup_backends.collect_source_facts(
                ["https://first.example/post/1", "https://second.example/post/2"],
                timeout=5,
                cache={},
                allow_parallel=False,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertEqual(facts["artists"], ["artist one"])
        self.assertEqual(lookup_source_url.call_count, 1)

    def test_collect_source_facts_keeps_parallel_fanout_with_stop_condition(self):
        first = source_lookup.make_source_facts()
        first["artists"] = ["artist one"]
        first["site"] = "first.example"

        second = source_lookup.make_source_facts()
        second["artists"] = ["artist two"]
        second["site"] = "second.example"

        with patch.object(
            source_lookup_backends,
            "lookup_source_url",
            side_effect=[first, second],
        ) as lookup_source_url:
            facts = source_lookup_backends.collect_source_facts(
                ["https://first.example/post/1", "https://second.example/post/2"],
                timeout=5,
                cache={},
                allow_parallel=True,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertTrue(facts["artists"])
        self.assertGreaterEqual(lookup_source_url.call_count, 1)
        self.assertLessEqual(lookup_source_url.call_count, 2)

    def test_collect_source_facts_uses_local_urls_before_network_groups(self):
        local = source_lookup.make_source_facts()
        local["artists"] = ["artist one"]
        local["site"] = "x.com"

        def lookup_side_effect(url, timeout, cache):
            if "x.com" in url:
                return local
            raise AssertionError("network lookup should not run after local URL satisfied stop condition")

        with patch.object(
            source_lookup_backends,
            "lookup_source_url",
            side_effect=lookup_side_effect,
        ) as lookup_source_url:
            facts = source_lookup_backends.collect_source_facts(
                ["https://danbooru.donmai.us/posts/1", "https://x.com/test/status/1"],
                timeout=5,
                cache={},
                allow_parallel=True,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertEqual(facts["artists"], ["artist one"])
        lookup_source_url.assert_called_once_with("https://x.com/test/status/1", 5, {})

    def test_collect_source_facts_skips_lower_priority_network_groups_after_match(self):
        danbooru = source_lookup.make_source_facts()
        danbooru["artists"] = ["artist one"]
        danbooru["site"] = "danbooru"

        def lookup_side_effect(url, timeout, cache):
            if "danbooru" in url:
                return danbooru
            if "gelbooru" in url:
                raise AssertionError("lower-priority network group should not run after match")
            raise AssertionError(f"unexpected URL: {url}")

        with patch.object(
            source_lookup_backends,
            "lookup_source_url",
            side_effect=lookup_side_effect,
        ) as lookup_source_url:
            facts = source_lookup_backends.collect_source_facts(
                ["https://gelbooru.com/index.php?page=post&s=view&id=2", "https://danbooru.donmai.us/posts/1"],
                timeout=5,
                cache={},
                allow_parallel=True,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertEqual(facts["artists"], ["artist one"])
        lookup_source_url.assert_called_once_with("https://danbooru.donmai.us/posts/1", 5, {})

    def test_lookup_sources_by_md5_stops_after_first_satisfying_site(self):
        first = source_lookup.make_source_facts()
        first["artists"] = ["artist one"]
        first["site"] = "danbooru"

        second = source_lookup.make_source_facts()
        second["artists"] = ["artist two"]
        second["site"] = "e621.net"

        with patch.object(
            source_lookup_backends,
            "lookup_danbooru_by_md5",
            return_value=first,
        ) as lookup_danbooru, patch.object(
            source_lookup_backends,
            "lookup_e621_by_md5",
            return_value=second,
        ) as lookup_e621:
            facts = source_lookup_backends.lookup_sources_by_md5(
                "m" * 32,
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
                requested_sites=["danbooru", "e621"],
                allow_parallel=False,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertEqual(facts["artists"], ["artist one"])
        lookup_danbooru.assert_called_once()
        lookup_e621.assert_not_called()

    def test_lookup_sources_by_md5_keeps_parallel_fanout_with_stop_condition(self):
        first = source_lookup.make_source_facts()
        first["artists"] = ["artist one"]
        first["site"] = "danbooru"

        second = source_lookup.make_source_facts()
        second["artists"] = ["artist two"]
        second["site"] = "e621.net"

        with patch.object(
            source_lookup_backends,
            "lookup_danbooru_by_md5",
            return_value=first,
        ) as lookup_danbooru, patch.object(
            source_lookup_backends,
            "lookup_e621_by_md5",
            return_value=second,
        ) as lookup_e621:
            facts = source_lookup_backends.lookup_sources_by_md5(
                "m" * 32,
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
                requested_sites=["danbooru", "e621"],
                allow_parallel=True,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertTrue(facts["artists"])
        lookup_danbooru.assert_called_once()
        self.assertLessEqual(lookup_e621.call_count, 1)

    def test_lookup_sources_by_md5_skips_lower_priority_groups_after_match(self):
        first = source_lookup.make_source_facts()
        first["artists"] = ["artist one"]
        first["site"] = "danbooru"

        with patch.object(
            source_lookup_backends,
            "lookup_danbooru_by_md5",
            return_value=first,
        ) as lookup_danbooru, patch.object(
            source_lookup_backends,
            "lookup_e621_by_md5",
            return_value=None,
        ) as lookup_e621, patch.object(
            source_lookup_backends,
            "lookup_moebooru_by_md5",
            return_value=None,
        ) as lookup_moebooru, patch.object(
            source_lookup_backends,
            "lookup_gelbooru_by_md5",
            return_value=None,
        ) as lookup_gelbooru:
            facts = source_lookup_backends.lookup_sources_by_md5(
                "m" * 32,
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
                requested_sites=["danbooru", "e621", "yandere", "gelbooru"],
                allow_parallel=True,
                stop_when=lambda current: bool(current["artists"]),
            )

        self.assertEqual(facts["artists"], ["artist one"])
        lookup_danbooru.assert_called_once()
        self.assertLessEqual(lookup_e621.call_count, 1)
        lookup_moebooru.assert_not_called()
        lookup_gelbooru.assert_not_called()


class Rule34ApiTests(unittest.TestCase):
    def test_lookup_gelbooru_by_md5_uses_authenticated_rule34_api_when_configured(self):
        md5_hash = "a" * 32

        with patch.dict(
            "os.environ",
            {"RULE34_USER_ID": "12345", "RULE34_API_KEY": "secret"},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "fetch_json_or_none",
            return_value=[{
                "owner": "artist_name",
                "tags": "tag_one tag_two",
                "created_at": "2024-01-01 00:00:00",
                "rating": "e",
                "file_ext": "jpg",
            }],
        ) as fetch_json:
            facts = source_lookup_backends.lookup_gelbooru_by_md5("rule34.xxx", md5_hash, 5)

        called_url = fetch_json.call_args.args[0]
        self.assertIn("https://api.rule34.xxx/index.php?", called_url)
        self.assertIn("user_id=12345", called_url)
        self.assertIn("api_key=secret", called_url)
        self.assertIn(f"tags=md5%3A{md5_hash}", called_url)
        self.assertEqual(facts["site"], "rule34.xxx")
        self.assertEqual(facts["sites"], ["rule34.xxx"])
        self.assertEqual(facts["artists"], ["artist_name"])
        self.assertEqual(facts["ratings"], ["explicit"])

    def test_lookup_gelbooru_by_md5_falls_back_to_public_rule34_host_without_credentials(self):
        md5_hash = "b" * 32

        with patch.dict(
            "os.environ",
            {"RULE34_USER_ID": "", "RULE34_API_KEY": ""},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "fetch_json_or_none",
            return_value=[],
        ) as fetch_json:
            source_lookup_backends.lookup_gelbooru_by_md5("rule34.xxx", md5_hash, 5)

        called_url = fetch_json.call_args.args[0]
        self.assertIn("https://rule34.xxx/index.php?", called_url)
        self.assertNotIn("api.rule34.xxx", called_url)
        self.assertNotIn("user_id=", called_url)
        self.assertNotIn("api_key=", called_url)

    def test_lookup_gelbooru_by_md5_uses_authenticated_gelbooru_api_when_configured(self):
        md5_hash = "c" * 32

        with patch.dict(
            "os.environ",
            {"GELBOORU_USER_ID": "67890", "GELBOORU_API_KEY": "gel-secret"},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "fetch_json_or_none",
            return_value=[],
        ) as fetch_json:
            source_lookup_backends.lookup_gelbooru_by_md5("gelbooru.com", md5_hash, 5)

        called_url = fetch_json.call_args.args[0]
        self.assertIn("https://gelbooru.com/index.php?", called_url)
        self.assertIn("user_id=67890", called_url)
        self.assertIn("api_key=gel-secret", called_url)
        self.assertIn(f"tags=md5%3A{md5_hash}", called_url)

    def test_lookup_source_url_adds_rule34_auth_hint_when_request_fails_without_credentials(self):
        http_error = HTTPError(
            url="https://rule34.xxx/index.php?page=post&s=view&id=1",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

        with patch.dict(
            "os.environ",
            {"RULE34_USER_ID": "", "RULE34_API_KEY": ""},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "fetch_json",
            side_effect=http_error,
        ):
            facts = source_lookup_backends.lookup_source_url(
                "https://rule34.xxx/index.php?page=post&s=view&id=1",
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
            )

        self.assertEqual(facts["site"], "rule34.xxx")
        self.assertIn(
            "rule34.xxx: set RULE34_USER_ID and RULE34_API_KEY in .env to use api.rule34.xxx",
            facts["notes"],
        )

    def test_lookup_source_url_adds_gelbooru_auth_hint_when_request_fails_without_credentials(self):
        http_error = HTTPError(
            url="https://gelbooru.com/index.php?page=post&s=view&id=1",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

        with patch.dict(
            "os.environ",
            {"GELBOORU_USER_ID": "", "GELBOORU_API_KEY": ""},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "fetch_json",
            side_effect=http_error,
        ):
            facts = source_lookup_backends.lookup_source_url(
                "https://gelbooru.com/index.php?page=post&s=view&id=1",
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
            )

        self.assertEqual(facts["site"], "gelbooru.com")
        self.assertIn(
            "gelbooru.com: set GELBOORU_USER_ID and GELBOORU_API_KEY in .env for authenticated DAPI access",
            facts["notes"],
        )

    def test_lookup_source_url_skips_notes_for_temporary_source_suspension(self):
        with patch.object(
            source_lookup_backends,
            "lookup_gelbooru",
            side_effect=source_lookup_backends.TemporarySourceSuspensionError("gelbooru", 12.0),
        ):
            facts = source_lookup_backends.lookup_source_url(
                "https://gelbooru.com/index.php?page=post&s=view&id=1",
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
            )

        self.assertEqual(facts["site"], "gelbooru.com")
        self.assertEqual(facts["notes"], [])

    def test_lookup_sources_by_md5_adds_rule34_auth_hint_when_request_fails_without_credentials(self):
        http_error = HTTPError(
            url="https://rule34.xxx/index.php?page=dapi&s=post&q=index&tags=md5%3Aabc",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

        with patch.dict(
            "os.environ",
            {"RULE34_USER_ID": "", "RULE34_API_KEY": ""},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "lookup_gelbooru_by_md5",
            side_effect=http_error,
        ):
            facts = source_lookup_backends.lookup_sources_by_md5(
                "c" * 32,
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
                requested_sites=["rule34"],
                allow_parallel=False,
            )

        self.assertIn(
            "rule34.xxx: set RULE34_USER_ID and RULE34_API_KEY in .env to use api.rule34.xxx",
            facts["notes"],
        )

    def test_lookup_sources_by_md5_adds_gelbooru_auth_hint_when_request_fails_without_credentials(self):
        http_error = HTTPError(
            url="https://gelbooru.com/index.php?page=dapi&s=post&q=index&tags=md5%3Aabc",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

        with patch.dict(
            "os.environ",
            {"GELBOORU_USER_ID": "", "GELBOORU_API_KEY": ""},
            clear=False,
        ), patch.object(
            source_lookup_backends,
            "lookup_gelbooru_by_md5",
            side_effect=http_error,
        ):
            facts = source_lookup_backends.lookup_sources_by_md5(
                "d" * 32,
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
                requested_sites=["gelbooru"],
                allow_parallel=False,
            )

        self.assertIn(
            "gelbooru.com: set GELBOORU_USER_ID and GELBOORU_API_KEY in .env for authenticated DAPI access",
            facts["notes"],
        )

    def test_lookup_sources_by_md5_skips_notes_for_temporary_source_suspension(self):
        with patch.object(
            source_lookup_backends,
            "lookup_gelbooru_by_md5",
            side_effect=source_lookup_backends.TemporarySourceSuspensionError("gelbooru", 12.0),
        ):
            facts = source_lookup_backends.lookup_sources_by_md5(
                "d" * 32,
                timeout=5,
                cache=source_lookup.make_singleflight_cache(),
                requested_sites=["gelbooru"],
                allow_parallel=False,
            )

        self.assertEqual(facts["notes"], [])


class ResolveLookupFileOptimizationTests(unittest.TestCase):
    def test_batches_alt_hash_lookups_by_type_and_fans_out_duplicates(self):
        client = object()

        with patch.object(
            source_lookup_backends,
            "read_lookup_lines",
            return_value=[
                (1, "a" * 32),
                (2, "A" * 32),
                (3, "b" * 32),
            ],
        ), patch.object(
            source_lookup_backends,
            "resolve_hashes",
            return_value={
                "a" * 32: "1" * 64,
                "b" * 32: "2" * 64,
            },
        ) as resolve_hashes:
            hash_inputs, file_id_inputs, unresolved = source_lookup_backends.resolve_lookup_file(
                client=client,
                lookupfile="lookup.txt",
                doublecheck_file_system=False,
            )

        self.assertEqual(dict(file_id_inputs), {})
        self.assertEqual(unresolved, [])
        self.assertEqual(
            dict(hash_inputs),
            {
                "1" * 64: ["a" * 32, "A" * 32],
                "2" * 64: ["b" * 32],
            },
        )
        resolve_hashes.assert_called_once_with(
            client,
            ["a" * 32, "b" * 32],
            "md5",
            "sha256",
        )

    def test_resolves_duplicate_urls_once_and_fans_out_results(self):
        client = object()
        shared_url = "https://example.com/post/1"

        with patch.object(
            source_lookup_backends,
            "read_lookup_lines",
            return_value=[
                (1, shared_url),
                (2, shared_url),
                (3, "https://example.com/post/2"),
            ],
        ), patch.object(
            source_lookup_backends,
            "resolve_lookup_urls",
            return_value={
                shared_url: ["a" * 64, "b" * 64],
                "https://example.com/post/2": [],
            },
        ) as resolve_lookup_urls:
            hash_inputs, file_id_inputs, unresolved = source_lookup_backends.resolve_lookup_file(
                client=client,
                lookupfile="lookup.txt",
                doublecheck_file_system=True,
            )

        self.assertEqual(dict(file_id_inputs), {})
        self.assertEqual(
            dict(hash_inputs),
            {
                "a" * 64: [shared_url, shared_url],
                "b" * 64: [shared_url, shared_url],
            },
        )
        self.assertEqual(
            unresolved,
            ["line 3: https://example.com/post/2 -> nothing matched in Hydrus"],
        )
        resolve_lookup_urls.assert_called_once_with(
            client,
            [shared_url, "https://example.com/post/2"],
            True,
        )

    def test_resolve_lookup_urls_dedupes_before_parallel_resolution(self):
        client = object()
        shared_url = "https://example.com/post/1"

        with patch.object(
            source_lookup_backends,
            "resolve_url_to_hashes",
            side_effect=lambda current_client, url, doublecheck: [url.rsplit("/", 1)[-1] * 64],
        ) as resolve_url_to_hashes:
            resolved = source_lookup_backends.resolve_lookup_urls(
                client=client,
                url_values=[shared_url, shared_url, "https://example.com/post/2"],
                doublecheck_file_system=False,
            )

        self.assertEqual(
            resolved,
            {
                shared_url: ["1" * 64],
                "https://example.com/post/2": ["2" * 64],
            },
        )
        self.assertEqual(resolve_url_to_hashes.call_count, 2)


if __name__ == "__main__":
    unittest.main()
