import os
import re
from datetime import datetime, timezone
from threading import Event, Lock

import click

KAOMOJIS = {
    "0_0",
    "(o)_(o)",
    "+_+",
    "+_-",
    "._.",
    "<o>_<o>",
    "<|>_<|>",
    "=_=",
    ">_<",
    "3_3",
    "6_9",
    ">_o",
    "@_@",
    "^_^",
    "o_o",
    "u_u",
    "x_x",
    "|_|",
    "||_||",
}

NO_NAMESPACE_VALUES = {"", "none", "off", "plain", "raw"}
SKIP_NAMESPACE_VALUES = {"skip", "omit", "disable", "disabled", "false", "0", "no"}
SOURCE_TAG_GROUPS = ("general", "character", "series", "meta", "species", "lore")
SOURCE_EXTRA_NAMESPACE_FIELDS = ("rating", "year", "site", "filetype", "artist")
SOURCE_NAMESPACE_FIELDS = SOURCE_TAG_GROUPS + SOURCE_EXTRA_NAMESPACE_FIELDS
SOURCE_NAMESPACE_FACT_KEYS = {
    "rating": "ratings",
    "year": "years",
    "site": "sites",
    "filetype": "filetypes",
    "artist": "artists",
}
SOURCE_MATCH_FACT_KEYS = ("artists", "tags", "years", "ratings", "filetypes")
SOURCE_SITE_ALIASES = {
    "danbooru": {"danbooru", "danbooru.donmai.us"},
    "e621": {"e621", "e621.net"},
    "e926": {"e926", "e926.net"},
    "yandere": {"yandere", "yande.re"},
    "konachan": {"konachan", "konachan.com", "konachan.net"},
    "gelbooru": {"gelbooru", "gelbooru.com"},
    "rule34": {"rule34", "rule34.xxx"},
    "safebooru": {"safebooru", "safebooru.org"},
}
DEFAULT_SOURCE_SITES = (
    "danbooru",
    "e621",
    "e926",
    "yandere",
    "konachan",
    "gelbooru",
    "rule34",
    "safebooru",
)

CACHE_MISS = object()

LOOKUP_PREFIXES = {
    "sha256": "sha256",
    "md5": "md5",
    "sha1": "sha1",
    "sha512": "sha512",
    "url": "url",
    "file_id": "file_id",
}

YEAR_RE = re.compile(r"(19|20)\d{2}")


def dedupe_keep_order(items):
    seen = set()
    ordered = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def split_space_tags(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split() if part.strip()]


def normalize_source_tag(tag):
    tag = str(tag).strip()
    if not tag:
        return ""
    if tag not in KAOMOJIS:
        tag = tag.replace("_", " ")
    return tag.strip()


def build_namespaced_tag(namespace, value):
    clean_value = normalize_source_tag(value)
    if not clean_value:
        return ""

    clean_namespace = str(namespace or "").strip().strip(":")
    if clean_namespace.lower() in NO_NAMESPACE_VALUES:
        clean_namespace = ""
    if clean_namespace:
        return f"{clean_namespace}:{clean_value}"
    return clean_value


def namespace_is_skipped(namespace):
    clean_namespace = str(namespace or "").strip().strip(":").lower()
    return clean_namespace in SKIP_NAMESPACE_VALUES


def make_source_facts():
    facts = {
        "site": "",
        "sites": [],
        "artists": [],
        "tags": [],
        "years": [],
        "notes": [],
        "ratings": [],
        "filetypes": [],
    }
    for group in SOURCE_TAG_GROUPS:
        facts[group] = []
    return facts


def extend_source_values(facts, key, values):
    if key not in facts:
        return

    normalized = []
    for value in values or []:
        clean_value = str(value).strip()
        if clean_value:
            normalized.append(clean_value)

    facts[key].extend(normalized)


def extend_source_group(facts, group, values):
    if group not in facts:
        return

    normalized = []
    for value in values or []:
        clean_value = normalize_source_tag(value)
        if clean_value:
            normalized.append(clean_value)

    facts[group].extend(normalized)
    if group in SOURCE_TAG_GROUPS:
        facts["tags"].extend(normalized)


def finalize_source_facts(facts):
    for key, value in list(facts.items()):
        if isinstance(value, list):
            facts[key] = dedupe_keep_order(value)
    if not facts.get("site") and facts.get("sites"):
        facts["site"] = facts["sites"][0]
    return facts


def merge_source_facts_into(target, source_facts):
    if not source_facts:
        return target

    source_site = str(source_facts.get("site", "")).strip()
    if source_site:
        target.setdefault("sites", []).append(source_site)
        if not target.get("site"):
            target["site"] = source_site

    for key, value in target.items():
        if isinstance(value, list):
            target[key].extend(source_facts.get(key, []))
    return target


def merge_source_facts(*fact_sets):
    facts = make_source_facts()
    for source_facts in fact_sets:
        merge_source_facts_into(facts, source_facts)
    return finalize_source_facts(facts)


def has_source_match_data(facts):
    return any(facts.get(key) for key in SOURCE_MATCH_FACT_KEYS)


def parse_requested_sites(sites_value):
    raw_value = str(sites_value or "").strip().lower()
    if not raw_value or raw_value == "all":
        return list(DEFAULT_SOURCE_SITES)

    requested = []
    invalid = []
    for token in re.split(r"[,\s]+", raw_value):
        if not token:
            continue

        matched = None
        for canonical_name, aliases in SOURCE_SITE_ALIASES.items():
            if token in aliases:
                matched = canonical_name
                break

        if matched:
            if matched not in requested:
                requested.append(matched)
        else:
            invalid.append(token)

    if invalid:
        valid_sites = ", ".join(DEFAULT_SOURCE_SITES)
        raise click.ClickException(
            f"Unknown source site name(s): {', '.join(invalid)}. Valid values: {valid_sites}, or all."
        )

    return requested


def chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def make_singleflight_cache():
    return {
        "values": {},
        "pending": {},
        "lock": Lock(),
    }


def get_singleflight_cached(cache_state, key, factory):
    with cache_state["lock"]:
        cached = cache_state["values"].get(key, CACHE_MISS)
        if cached is not CACHE_MISS:
            return cached

        pending = cache_state["pending"].get(key)
        if pending is None:
            pending = {"event": Event(), "value": None, "error": None}
            cache_state["pending"][key] = pending
            is_owner = True
        else:
            is_owner = False

    if is_owner:
        try:
            value = factory()
        except Exception as exc:
            with cache_state["lock"]:
                cache_state["pending"].pop(key, None)
                pending["error"] = exc
                pending["event"].set()
            raise

        with cache_state["lock"]:
            cache_state["values"][key] = value
            cache_state["pending"].pop(key, None)
            pending["value"] = value
            pending["event"].set()
        return value

    pending["event"].wait()
    if pending["error"] is not None:
        raise pending["error"]
    return pending["value"]


def read_lookup_lines(lookupfile):
    if not os.path.isfile(lookupfile):
        raise click.ClickException(
            f'Lookup file "{lookupfile}" was not found. Create it or point the launcher to a valid text file.'
        )

    lines = []
    with open(lookupfile, encoding="utf-8") as lookup_f:
        for line_number, raw_line in enumerate(lookup_f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lines.append((line_number, line))

    if not lines:
        raise click.ClickException(
            f'Lookup file "{lookupfile}" is empty. Put one sha256/md5/sha1/sha512, file_id, or full https:// URL per line.'
        )

    return lines


def parse_lookup_entry(raw_value):
    lower_value = raw_value.lower()
    if lower_value.startswith(("http://", "https://")):
        return "url", raw_value

    if ":" in raw_value:
        prefix, value = raw_value.split(":", 1)
        prefix = prefix.strip().lower()
        value = value.strip()
        if prefix in LOOKUP_PREFIXES:
            return LOOKUP_PREFIXES[prefix], value

    if raw_value.isdigit():
        return "file_id", raw_value

    if re.fullmatch(r"[0-9a-fA-F]+", raw_value):
        length_map = {
            32: "md5",
            40: "sha1",
            64: "sha256",
            128: "sha512",
        }
        hash_type = length_map.get(len(raw_value))
        if hash_type:
            return hash_type, raw_value.lower()

    raise click.ClickException(
        f"Could not understand lookup value: {raw_value}. "
        "Use sha256/md5/sha1/sha512, file_id, or a full https:// URL."
    )


def extract_known_urls(metadata_record):
    urls = []
    for key in ("known_urls", "detailed_known_urls"):
        value = metadata_record.get(key, [])
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict):
                for candidate_key in ("normalised_url", "url", "request_url"):
                    candidate = item.get(candidate_key)
                    if candidate:
                        urls.append(candidate)
                        break
    return dedupe_keep_order(urls)


def coerce_year(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        integer_value = int(value)
        if 1900 <= integer_value <= 2100:
            return integer_value
        if integer_value > 100000000:
            try:
                return datetime.fromtimestamp(integer_value, tz=timezone.utc).year
            except (OverflowError, OSError, ValueError):
                return None
        return None

    match = YEAR_RE.search(str(value))
    if match:
        return int(match.group(0))
    return None


def extract_years_from_record(metadata_record):
    years = []
    for key, value in metadata_record.items():
        if key.startswith("time_"):
            year = coerce_year(value)
            if year:
                years.append(year)

    timestamps = metadata_record.get("timestamps")
    if isinstance(timestamps, dict):
        for value in timestamps.values():
            year = coerce_year(value)
            if year:
                years.append(year)

    return dedupe_keep_order(years)
