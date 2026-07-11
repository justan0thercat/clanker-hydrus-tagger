from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic

import click

from .source_lookup_backends import (
    LOOKUP_RECORD_MAX_WORKERS,
    SOURCE_LOOKUP_MAX_WORKERS,
    SOURCE_REQUEST_CONTROLLER,
    collect_metadata_hashes,
    collect_source_facts,
    fetch_metadata_records,
    lookup_sources_by_md5,
    resolve_hashes,
    resolve_lookup_file,
)
from .source_lookup_common import (
    extract_known_urls,
    has_source_match_data,
    make_singleflight_cache,
    make_source_facts,
    merge_source_facts,
    parse_lookup_entry,
    parse_requested_sites,
)
from .source_lookup_tags import (
    build_lookup_tags,
    parse_namespace_config,
    source_facts_satisfy_lookup_mode,
)

__all__ = [
    "build_lookup_tags",
    "build_md5_lookup_map",
    "choose_record_worker_count",
    "collect_metadata_hashes",
    "collect_source_facts",
    "extract_known_urls",
    "lookup_sources_by_md5",
    "make_singleflight_cache",
    "make_source_facts",
    "parse_lookup_entry",
    "parse_namespace_config",
    "parse_requested_sites",
    "process_lookup_record",
    "resolve_hashes",
    "run_lookup",
]


def format_seconds(seconds):
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def build_mode_stop_when(mode, metadata_record, namespace):
    def stop_when(facts):
        return source_facts_satisfy_lookup_mode(mode, facts, metadata_record, namespace)

    return stop_when


def iter_nested_tag_strings(value):
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from iter_nested_tag_strings(nested)
        return
    if isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from iter_nested_tag_strings(nested)


def extract_service_storage_tags(metadata_record, tag_service):
    tags_by_service = metadata_record.get("tags") or {}
    for service_key, service_data in tags_by_service.items():
        service_name = service_data.get("name") if isinstance(service_data, dict) else None
        if service_key != tag_service and service_name != tag_service:
            continue
        if isinstance(service_data, dict) and "storage_tags" in service_data:
            tag_container = service_data["storage_tags"]
        else:
            tag_container = service_data
        return set(iter_nested_tag_strings(tag_container))
    return set()


def has_existing_namespace_tag(metadata_record, tag_service, namespace, mode):
    if mode not in {"artist", "year"}:
        return False
    if not isinstance(namespace, str):
        return False
    namespace = namespace.strip()
    if not namespace:
        return False

    prefix = f"{namespace}:".casefold()
    existing_tags = extract_service_storage_tags(metadata_record, tag_service)
    return any(tag.casefold().startswith(prefix) for tag in existing_tags)


def filter_existing_service_tags(tags_to_add, metadata_record, tag_service):
    existing_tags = extract_service_storage_tags(metadata_record, tag_service)
    if not existing_tags:
        return list(tags_to_add)

    existing_tag_keys = {tag.casefold() for tag in existing_tags}
    return [tag for tag in tags_to_add if tag.casefold() not in existing_tag_keys]


def flush_tag_batches(client, tag_service, hashes_by_tag_tuple):
    flushed_records = 0
    flushed_tags = 0

    for tags_tuple, hashes_to_tag in list(hashes_by_tag_tuple.items()):
        client.add_tags(
            hashes=hashes_to_tag,
            service_names_to_tags={tag_service: list(tags_tuple)},
        )
        flushed_records += len(hashes_to_tag)
        flushed_tags += len(hashes_to_tag) * len(tags_tuple)

    hashes_by_tag_tuple.clear()
    return flushed_records, flushed_tags


def build_lookup_batch_line(completed, total, started_at, matched_records, tagged_records):
    elapsed = monotonic() - started_at
    rate = completed / elapsed if elapsed > 0 else 0.0
    remaining = ((total - completed) / rate) if rate > 0 else 0.0
    line = (
        f"Progress: {completed}/{total} "
        f"({(completed / total * 100) if total else 100:.1f}%) | "
        f"found {matched_records} | tagged {tagged_records} | "
        f"elapsed {format_seconds(elapsed)}"
    )
    if completed >= 10 and elapsed >= 30 and rate > 0:
        line += f" | eta {format_seconds(remaining)}"
    return line


def should_emit_lookup_progress(completed, total, last_emit_at):
    if total <= 0 or completed >= total:
        return True
    if total <= 25:
        return True

    now = monotonic()
    if completed in {1, 5, 10}:
        return True
    if now - last_emit_at >= 10.0:
        return True
    if total <= 100 and completed % 5 == 0:
        return True
    if total <= 500 and completed % 10 == 0:
        return True
    return completed % 25 == 0


def summarize_source_request_events(events):
    counts = {}
    ordered_events = []
    for event in events:
        text = str(event)
        if text not in counts:
            counts[text] = 0
            ordered_events.append(text)
        counts[text] += 1

    summarized = []
    for event in ordered_events:
        count = counts[event]
        if count > 1:
            summarized.append(f"{event} (x{count})")
        else:
            summarized.append(event)
    return summarized


def emit_source_request_events():
    for event in summarize_source_request_events(SOURCE_REQUEST_CONTROLLER.drain_events()):
        click.echo(f"Notice: {event}")


def build_md5_lookup_map(client, metadata_records):
    record_hashes = collect_metadata_hashes(metadata_records)
    if not record_hashes:
        return {}
    return resolve_hashes(client, record_hashes, "sha256", "md5")


def process_lookup_record(
    record,
    hash_inputs,
    file_id_inputs_by_hash,
    md5_by_sha256,
    tag_service,
    timeout,
    requested_sites,
    namespace,
    mode,
    url_cache,
    hash_cache,
    allow_parallel_lookups,
):
    record_hash = str(record.get("hash", "unknown"))
    record_hash_key = record_hash.lower()
    inputs = list(
        dict.fromkeys(
            hash_inputs.get(record_hash_key, []) + file_id_inputs_by_hash.get(record_hash_key, [])
        )
    )
    if has_existing_namespace_tag(record, tag_service, namespace, mode):
        report_bits = [
            record_hash,
            "inputs=" + ", ".join(inputs or ["metadata-only"]),
            "skip=existing namespace tag",
            "tags=already tagged",
        ]
        return {
            "record_hash": record_hash,
            "urls": [],
            "md5_hash": None,
            "md5_lookup_attempted": False,
            "facts": make_source_facts(),
            "url_has_matches": False,
            "hash_has_matches": False,
            "tags_to_add": [],
            "extra_source": None,
            "result_message": "already tagged",
            "report_line": "\t".join(report_bits),
        }

    urls = extract_known_urls(record)
    stop_when = build_mode_stop_when(mode, record, namespace)
    url_facts = collect_source_facts(
        urls,
        timeout,
        url_cache,
        allow_parallel=allow_parallel_lookups,
        stop_when=stop_when,
    )
    url_has_matches = has_source_match_data(url_facts)
    url_satisfies_mode = source_facts_satisfy_lookup_mode(mode, url_facts, record, namespace)

    md5_hash = md5_by_sha256.get(record_hash_key)
    md5_lookup_attempted = False
    hash_facts = make_source_facts()
    hash_has_matches = False
    if md5_hash and not url_satisfies_mode:
        md5_lookup_attempted = True
        hash_facts = lookup_sources_by_md5(
            md5_hash,
            timeout,
            hash_cache,
            requested_sites,
            allow_parallel=allow_parallel_lookups,
            stop_when=stop_when,
        )
        hash_has_matches = has_source_match_data(hash_facts)

    facts = merge_source_facts(url_facts, hash_facts)
    if mode == "year":
        tags_to_add, extra_source = build_lookup_tags(mode, facts, record, namespace)
    else:
        tags_to_add = build_lookup_tags(mode, facts, record, namespace)
        extra_source = None
    found_tags = list(tags_to_add)
    tags_to_add = filter_existing_service_tags(tags_to_add, record, tag_service)
    if tags_to_add:
        result_message = ", ".join(tags_to_add)
    elif found_tags:
        result_message = "nothing new"
    else:
        result_message = "nothing found"

    report_bits = [
        record_hash,
        "inputs=" + ", ".join(inputs or ["metadata-only"]),
        "urls=" + str(len(urls)),
        "md5=" + str(md5_hash or "none"),
        "md5_lookup=" + ("used" if md5_lookup_attempted else "skipped"),
        "sites=" + ", ".join(facts["sites"] or ["none"]),
        "tags=" + result_message,
    ]
    if mode == "year":
        report_bits.append("year_source=" + str(extra_source))
    if facts["notes"]:
        report_bits.append("notes=" + " | ".join(facts["notes"]))

    return {
        "record_hash": record_hash,
        "urls": urls,
        "md5_hash": md5_hash,
        "md5_lookup_attempted": md5_lookup_attempted,
        "facts": facts,
        "url_has_matches": url_has_matches,
        "hash_has_matches": hash_has_matches,
        "tags_to_add": tags_to_add,
        "extra_source": extra_source,
        "result_message": result_message,
        "report_line": "\t".join(report_bits),
    }


def choose_record_worker_count(total_records):
    if total_records <= 0:
        return 0
    return min(LOOKUP_RECORD_MAX_WORKERS, max(4, SOURCE_LOOKUP_MAX_WORKERS * 2), total_records)


def should_parallelize_inner_lookups(total_records):
    # Network fan-out is already bounded by per-site request policies in the
    # backend controller. Disabling inner parallelism for large batches turns
    # URL and md5 site lookups into effectively serial work per record, which
    # tanks throughput on real libraries.
    return total_records > 0


def run_lookup(
    client,
    mode,
    lookupfile,
    tag_service,
    namespace,
    privacy,
    timeout,
    report,
    doublecheck_file_system,
    sites="all",
):
    started_at = monotonic()
    SOURCE_REQUEST_CONTROLLER.reset()
    requested_sites = parse_requested_sites(sites)

    hash_inputs, file_id_inputs, unresolved = resolve_lookup_file(
        client,
        lookupfile,
        doublecheck_file_system,
    )

    resolved_hashes = sorted(hash_inputs.keys())
    resolved_file_ids = sorted(file_id_inputs.keys())
    if not resolved_hashes and not resolved_file_ids:
        for unresolved_line in unresolved:
            click.echo(unresolved_line, err=True)
        raise click.ClickException("Nothing was resolved from the lookup file.")

    metadata_records = fetch_metadata_records(client, resolved_hashes, resolved_file_ids)
    file_id_inputs_by_hash = defaultdict(list)
    for record in metadata_records:
        file_id = record.get("file_id")
        record_hash = record.get("hash")
        if file_id in file_id_inputs and record_hash:
            file_id_inputs_by_hash[str(record_hash).lower()].extend(file_id_inputs[file_id])

    report_lines = []
    url_cache = make_singleflight_cache()
    hash_cache = make_singleflight_cache()
    total_added = 0
    records_with_urls = 0
    source_url_count = 0
    records_with_url_source_hits = 0
    records_with_md5_available = 0
    md5_lookup_attempted_count = 0
    records_with_hash_source_hits = 0
    updated_records = 0
    matched_records = 0

    # File-id-only inputs land here too. Otherwise md5 fallback silently misses them.
    md5_by_sha256 = build_md5_lookup_map(client, metadata_records)

    total_records = len(metadata_records)
    click.echo(f"Total files: {total_records}")
    click.echo(f"Sites: {', '.join(requested_sites)}")

    file_workers = choose_record_worker_count(total_records)
    allow_parallel_lookups = should_parallelize_inner_lookups(total_records)
    if total_records:
        click.echo(f"Processing up to {file_workers} files at a time")

    processed_records = 0
    last_progress_emit_at = started_at
    hashes_by_tag_tuple = defaultdict(list)

    def flush_pending_tags():
        nonlocal updated_records
        nonlocal total_added

        if not hashes_by_tag_tuple:
            return

        flushed_records, flushed_tags = flush_tag_batches(
            client,
            tag_service,
            hashes_by_tag_tuple,
        )
        updated_records += flushed_records
        total_added += flushed_tags

    def consume_result(result):
        nonlocal records_with_urls
        nonlocal source_url_count
        nonlocal records_with_url_source_hits
        nonlocal records_with_md5_available
        nonlocal md5_lookup_attempted_count
        nonlocal records_with_hash_source_hits
        nonlocal updated_records
        nonlocal matched_records
        nonlocal processed_records
        nonlocal total_added
        nonlocal last_progress_emit_at

        record_hash = result["record_hash"]
        urls = result["urls"]
        md5_hash = result["md5_hash"]
        md5_lookup_attempted = result["md5_lookup_attempted"]
        facts = result["facts"]
        url_has_matches = result["url_has_matches"]
        hash_has_matches = result["hash_has_matches"]
        tags_to_add = result["tags_to_add"]
        result_message = result["result_message"]

        if urls:
            records_with_urls += 1
            source_url_count += len(urls)
        if has_source_match_data(facts):
            matched_records += 1
        if url_has_matches:
            records_with_url_source_hits += 1
        if md5_hash:
            records_with_md5_available += 1
        if md5_lookup_attempted:
            md5_lookup_attempted_count += 1
        if hash_has_matches:
            records_with_hash_source_hits += 1

        if tags_to_add:
            hashes_by_tag_tuple[tuple(tags_to_add)].append(record_hash)

        if not privacy:
            click.echo(f"{record_hash}: {result_message}")

        report_lines.append(result["report_line"])
        processed_records += 1
        if should_emit_lookup_progress(processed_records, total_records, last_progress_emit_at):
            flush_pending_tags()
            emit_source_request_events()
            click.echo(
                build_lookup_batch_line(
                    processed_records,
                    total_records,
                    started_at,
                    matched_records,
                    updated_records,
                )
            )
            last_progress_emit_at = monotonic()

    if total_records == 1:
        consume_result(
            process_lookup_record(
                metadata_records[0],
                hash_inputs,
                file_id_inputs_by_hash,
                md5_by_sha256,
                tag_service,
                timeout,
                requested_sites,
                namespace,
                mode,
                url_cache,
                hash_cache,
                allow_parallel_lookups,
            )
        )
    elif total_records > 1:
        with ThreadPoolExecutor(max_workers=file_workers) as executor:
            futures = [
                executor.submit(
                    process_lookup_record,
                    record,
                    hash_inputs,
                    file_id_inputs_by_hash,
                    md5_by_sha256,
                    tag_service,
                    timeout,
                    requested_sites,
                    namespace,
                    mode,
                    url_cache,
                    hash_cache,
                    allow_parallel_lookups,
                )
                for record in metadata_records
            ]
            for future in as_completed(futures):
                consume_result(future.result())

    flush_pending_tags()
    emit_source_request_events()

    for unresolved_line in unresolved:
        click.echo(unresolved_line, err=True)
        report_lines.append("unresolved\t" + unresolved_line)

    if report:
        with open(report, "w", encoding="utf-8") as report_f:
            report_f.write("\n".join(report_lines))
            report_f.write("\n")
        click.echo(f"Report written to {report}")

    click.echo(
        "Summary: "
        f"{updated_records} tagged, "
        f"{matched_records} found, "
        f"{md5_lookup_attempted_count} md5 fallback lookups, "
        f"{format_seconds(monotonic() - started_at)} total."
    )
    request_stats = SOURCE_REQUEST_CONTROLLER.snapshot()
    click.echo(
        "Requests: "
        f"{request_stats['requests']} requests, "
        f"{request_stats['retries']} retries, "
        f"{request_stats['rate_limits']} rate limits seen, "
        f"{request_stats['recovered']} successful retries, "
        f"{request_stats['suspensions']} suspensions opened, "
        f"{request_stats['suspended_skips']} requests skipped during suspension."
    )

    if metadata_records and records_with_urls == 0 and records_with_hash_source_hits == 0:
        click.echo(
            "Warning: Hydrus returned 0 known_urls for every resolved file, and exact hash lookups also found nothing. "
            "That usually means these files are not mirrored on the supported source sites, or the site no longer exposes them by hash.",
            err=True,
        )

    click.echo(f"Lookup finished. Added {total_added} new tags across {updated_records} files.")
