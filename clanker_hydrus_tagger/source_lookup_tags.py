import click

from .source_lookup_common import (
    SOURCE_EXTRA_NAMESPACE_FIELDS,
    SOURCE_NAMESPACE_FACT_KEYS,
    SOURCE_NAMESPACE_FIELDS,
    SOURCE_TAG_GROUPS,
    build_namespaced_tag,
    dedupe_keep_order,
    extract_years_from_record,
    namespace_is_skipped,
    normalize_source_tag,
)


def pick_year(source_years, metadata_years):
    if source_years:
        return min(source_years), "source"
    if metadata_years:
        return min(metadata_years), "hydrus"
    return None, "none"


def build_grouped_source_tags(facts, namespace_config):
    grouped_values = set()
    for group in SOURCE_TAG_GROUPS:
        if group == "general":
            continue
        namespace = namespace_config.get(group, group)
        if namespace_is_skipped(namespace):
            continue
        for value in facts.get(group, []):
            normalized = normalize_source_tag(value)
            if normalized:
                grouped_values.add(normalized)

    tags = []
    for group in SOURCE_TAG_GROUPS:
        namespace = namespace_config.get(group, group)
        if namespace_is_skipped(namespace):
            continue
        for value in facts.get(group, []):
            normalized = normalize_source_tag(value)
            if group == "general" and normalized and normalized in grouped_values:
                continue
            tag = build_namespaced_tag(namespace, value)
            if tag:
                tags.append(tag)

    for field in SOURCE_EXTRA_NAMESPACE_FIELDS:
        namespace = namespace_config.get(field, field)
        if namespace_is_skipped(namespace):
            continue
        fact_key = SOURCE_NAMESPACE_FACT_KEYS[field]
        for value in facts.get(fact_key, []):
            if field == "year" and value is None:
                continue
            tag = build_namespaced_tag(namespace, str(value))
            if tag:
                tags.append(tag)

    return dedupe_keep_order(tags)


def parse_namespace_config(raw_value, default_namespace=""):
    config = {group: default_namespace for group in SOURCE_NAMESPACE_FIELDS}
    group_aliases = {
        "copyright": "series",
        "franchise": "series",
    }
    text = str(raw_value or "").strip()
    if not text:
        return config

    if "=" not in text and ":" not in text:
        for group in SOURCE_NAMESPACE_FIELDS:
            config[group] = text
        return config

    for part in text.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        separator = "=" if "=" in chunk else ":"
        key, value = chunk.split(separator, 1)
        key = key.strip().lower()
        key = group_aliases.get(key, key)
        value = value.strip()
        if key == "all":
            for group in SOURCE_NAMESPACE_FIELDS:
                config[group] = value
            continue
        if key not in config:
            valid_groups = ", ".join(SOURCE_NAMESPACE_FIELDS)
            raise click.ClickException(
                f"Unknown source tag group namespace key: {key}. Valid groups: {valid_groups}, all."
            )
        config[key] = value

    return config


def build_lookup_tags(mode, facts, metadata_record, namespace):
    if mode == "artist":
        tags = [build_namespaced_tag(namespace, artist) for artist in facts["artists"]]
        return dedupe_keep_order(tag for tag in tags if tag)

    if mode == "all":
        if isinstance(namespace, dict):
            return build_grouped_source_tags(facts, namespace)
        tags = [normalize_source_tag(tag) for tag in facts["tags"]]
        return dedupe_keep_order(tag for tag in tags if tag)

    metadata_years = extract_years_from_record(metadata_record)
    year, year_source = pick_year(facts["years"], metadata_years)
    if not year:
        return [], year_source
    return [build_namespaced_tag(namespace, str(year))], year_source


def all_mode_has_requested_coverage(facts, namespace_config):
    if not isinstance(namespace_config, dict):
        return bool(facts.get("tags"))

    enabled_groups = [
        group
        for group in SOURCE_TAG_GROUPS
        if not namespace_is_skipped(namespace_config.get(group, group))
    ]
    enabled_extra_fields = [
        field
        for field in SOURCE_EXTRA_NAMESPACE_FIELDS
        if not namespace_is_skipped(namespace_config.get(field, field))
    ]

    if enabled_groups and not any(facts.get(group) for group in enabled_groups):
        return False

    for field in enabled_extra_fields:
        fact_key = SOURCE_NAMESPACE_FACT_KEYS[field]
        if not facts.get(fact_key):
            return False

    return bool(enabled_groups or enabled_extra_fields)


def source_facts_satisfy_lookup_mode(mode, facts, metadata_record, namespace):
    if mode == "all":
        return all_mode_has_requested_coverage(facts, namespace)

    if mode == "year":
        return bool(facts.get("years"))

    tags = build_lookup_tags(mode, facts, metadata_record, namespace)
    return bool(tags)
