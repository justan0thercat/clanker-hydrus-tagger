import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock, Semaphore
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import hydrus_api

from .source_lookup_common import (
    YEAR_RE,
    chunked,
    coerce_year,
    dedupe_keep_order,
    extend_source_group,
    extend_source_values,
    finalize_source_facts,
    get_singleflight_cached,
    make_source_facts,
    merge_source_facts_into,
    parse_lookup_entry,
    read_lookup_lines,
    split_space_tags,
)

HTTP_HEADERS = {
    "User-Agent": "clanker-hydrus-tagger/1.0 (Hydrus source lookup helper)"
}
RULE34_API_HOST = "api.rule34.xxx"

CPU_COUNT = os.cpu_count() or 8
DEFAULT_SOURCE_LOOKUP_MAX_WORKERS = min(8, max(4, CPU_COUNT))
DEFAULT_LOOKUP_RECORD_MAX_WORKERS = min(32, max(8, CPU_COUNT * 2))
DEFAULT_LOOKUP_URL_RESOLVE_MAX_WORKERS = min(16, max(4, CPU_COUNT * 2))
SOURCE_LOOKUP_MAX_WORKERS = max(
    1,
    int(os.getenv("SOURCE_LOOKUP_MAX_WORKERS", str(DEFAULT_SOURCE_LOOKUP_MAX_WORKERS))),
)
LOOKUP_RECORD_MAX_WORKERS = max(
    1,
    int(os.getenv("LOOKUP_RECORD_MAX_WORKERS", str(DEFAULT_LOOKUP_RECORD_MAX_WORKERS))),
)
LOOKUP_URL_RESOLVE_MAX_WORKERS = max(
    1,
    int(os.getenv("LOOKUP_URL_RESOLVE_MAX_WORKERS", str(DEFAULT_LOOKUP_URL_RESOLVE_MAX_WORKERS))),
)
SOURCE_RETRY_MAX_ATTEMPTS = max(
    0,
    int(os.getenv("SOURCE_RETRY_MAX_ATTEMPTS", "2")),
)
SOURCE_RETRY_BASE_DELAY_MS = max(
    100,
    int(os.getenv("SOURCE_RETRY_BASE_DELAY_MS", "750")),
)
SOURCE_RETRY_MAX_DELAY_MS = max(
    SOURCE_RETRY_BASE_DELAY_MS,
    int(os.getenv("SOURCE_RETRY_MAX_DELAY_MS", "5000")),
)
SOURCE_REQUEST_SITE_POLICIES = {
    "danbooru": {
        "aliases": {"danbooru.donmai.us"},
        "max_concurrency": 2,
        "min_interval_seconds": 0.35,
    },
    "e621_family": {
        "aliases": {"e621.net", "e926.net"},
        "max_concurrency": 2,
        "min_interval_seconds": 0.35,
    },
    "yandere": {
        "aliases": {"yande.re"},
        "max_concurrency": 1,
        "min_interval_seconds": 0.75,
    },
    "konachan": {
        "aliases": {"konachan.com", "konachan.net"},
        "max_concurrency": 1,
        "min_interval_seconds": 0.75,
    },
    "gelbooru": {
        "aliases": {"gelbooru.com"},
        "max_concurrency": 1,
        "min_interval_seconds": 0.75,
    },
    "rule34": {
        "aliases": {"rule34.xxx", RULE34_API_HOST},
        "max_concurrency": 1,
        "min_interval_seconds": 1.0,
    },
    "safebooru": {
        "aliases": {"safebooru.org"},
        "max_concurrency": 1,
        "min_interval_seconds": 1.0,
    },
    "default": {
        "aliases": set(),
        "max_concurrency": 1,
        "min_interval_seconds": 0.5,
    },
}
SOURCE_REQUEST_SITE_BY_DOMAIN = {}
for source_request_bucket, source_request_policy in SOURCE_REQUEST_SITE_POLICIES.items():
    if source_request_bucket == "default":
        continue
    for source_request_alias in source_request_policy["aliases"]:
        SOURCE_REQUEST_SITE_BY_DOMAIN[source_request_alias] = source_request_bucket

SOURCE_MD5_PRIORITY_GROUPS = (
    ("danbooru", "e621", "e926"),
    ("yandere", "konachan"),
    ("gelbooru", "rule34", "safebooru"),
)
SOURCE_URL_PRIORITY_GROUPS = SOURCE_MD5_PRIORITY_GROUPS


def fetch_json(url, timeout):
    return SOURCE_REQUEST_CONTROLLER.fetch_json(url, timeout)


def fetch_json_or_none(url, timeout):
    try:
        return fetch_json(url, timeout)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def normalize_domain(netloc):
    domain = netloc.lower().split(":", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def get_source_request_bucket(url):
    domain = normalize_domain(urlparse(url).netloc)
    return SOURCE_REQUEST_SITE_BY_DOMAIN.get(domain, "default")


def parse_retry_after_seconds(value):
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return max(0.0, float(text))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def is_retryable_http_error(exc):
    return exc.code in {429, 500, 502, 503, 504, 520, 521, 522, 524}


def compute_retry_delay_seconds(exc, attempt):
    retry_after = None
    if isinstance(exc, HTTPError):
        retry_after = parse_retry_after_seconds(getattr(exc, "headers", {}).get("Retry-After"))

    if retry_after is None:
        retry_after = SOURCE_RETRY_BASE_DELAY_MS / 1000.0 * (2 ** attempt)

    max_delay_seconds = SOURCE_RETRY_MAX_DELAY_MS / 1000.0
    return min(max_delay_seconds, max(0.1, retry_after))


class SourceRequestController:
    def __init__(self):
        self._states = {}
        self._states_lock = Lock()
        self._stats_lock = Lock()
        self._events_lock = Lock()
        self.reset()

    def reset(self):
        with self._stats_lock:
            self._stats = {
                "requests": 0,
                "retries": 0,
                "rate_limits": 0,
                "recovered": 0,
            }

        with self._events_lock:
            self._events = []

        with self._states_lock:
            for state in self._states.values():
                with state["lock"]:
                    state["next_allowed_at"] = 0.0
                    state["backoff_until"] = 0.0

    def snapshot(self):
        with self._stats_lock:
            return dict(self._stats)

    def _increment(self, key):
        with self._stats_lock:
            self._stats[key] += 1

    def _emit_event(self, message):
        with self._events_lock:
            self._events.append(str(message))

    def drain_events(self):
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def _get_state(self, bucket):
        with self._states_lock:
            state = self._states.get(bucket)
            if state is not None:
                return state

            policy = SOURCE_REQUEST_SITE_POLICIES.get(bucket, SOURCE_REQUEST_SITE_POLICIES["default"])
            state = {
                "lock": Lock(),
                "semaphore": Semaphore(policy["max_concurrency"]),
                "next_allowed_at": 0.0,
                "backoff_until": 0.0,
                "policy": policy,
            }
            self._states[bucket] = state
            return state

    def _reserve_slot(self, state):
        with state["lock"]:
            now = monotonic()
            ready_at = max(now, state["next_allowed_at"], state["backoff_until"])
            state["next_allowed_at"] = ready_at + state["policy"]["min_interval_seconds"]
            return max(0.0, ready_at - now)

    def _extend_backoff(self, state, delay_seconds):
        with state["lock"]:
            state["backoff_until"] = max(state["backoff_until"], monotonic() + delay_seconds)

    def fetch_json(self, url, timeout):
        bucket = get_source_request_bucket(url)
        state = self._get_state(bucket)

        for attempt in range(SOURCE_RETRY_MAX_ATTEMPTS + 1):
            state["semaphore"].acquire()
            try:
                wait_seconds = self._reserve_slot(state)
                if wait_seconds > 0:
                    if wait_seconds >= 1.0:
                        self._emit_event(
                            f"{bucket}: throttling requests, waiting {wait_seconds:.1f}s before next request"
                        )
                    sleep(wait_seconds)

                self._increment("requests")
                request = Request(url, headers=HTTP_HEADERS)
                with urlopen(request, timeout=timeout) as response:
                    payload = response.read().decode("utf-8")
                if attempt > 0:
                    self._increment("recovered")
                    self._emit_event(f"{bucket}: request recovered after retry")
                return json.loads(payload)
            except HTTPError as exc:
                if exc.code == 429:
                    self._increment("rate_limits")
                if attempt < SOURCE_RETRY_MAX_ATTEMPTS and is_retryable_http_error(exc):
                    self._increment("retries")
                    delay_seconds = compute_retry_delay_seconds(exc, attempt)
                    self._extend_backoff(state, delay_seconds)
                    if exc.code == 429:
                        self._emit_event(
                            f"{bucket}: rate limited (HTTP 429), backing off for {delay_seconds:.1f}s"
                        )
                    else:
                        self._emit_event(
                            f"{bucket}: HTTP {exc.code}, backing off for {delay_seconds:.1f}s before retry"
                        )
                    continue
                raise
            except (URLError, TimeoutError, OSError):
                if attempt < SOURCE_RETRY_MAX_ATTEMPTS:
                    self._increment("retries")
                    delay_seconds = compute_retry_delay_seconds(None, attempt)
                    self._extend_backoff(state, delay_seconds)
                    self._emit_event(
                        f"{bucket}: transient network error, backing off for {delay_seconds:.1f}s before retry"
                    )
                    continue
                raise
            finally:
                state["semaphore"].release()

        raise RuntimeError(f"Source request unexpectedly exhausted retries: {url}")


SOURCE_REQUEST_CONTROLLER = SourceRequestController()


def normalize_source_rating(value):
    rating = str(value or "").strip().lower()
    rating_map = {
        "s": "safe",
        "safe": "safe",
        "q": "questionable",
        "questionable": "questionable",
        "e": "explicit",
        "explicit": "explicit",
        "g": "general",
        "general": "general",
        "m": "mature",
        "mature": "mature",
    }
    return rating_map.get(rating, rating)


def set_source_site(facts, site):
    site_name = str(site or "").strip()
    if not site_name:
        return facts
    facts["site"] = site_name
    facts.setdefault("sites", []).append(site_name)
    return facts


def get_rule34_credentials():
    user_id = str(os.getenv("RULE34_USER_ID", "")).strip()
    api_key = str(os.getenv("RULE34_API_KEY", "")).strip()
    return user_id, api_key


def get_gelbooru_credentials():
    user_id = str(os.getenv("GELBOORU_USER_ID", "")).strip()
    api_key = str(os.getenv("GELBOORU_API_KEY", "")).strip()
    return user_id, api_key


def get_booru_credentials(site):
    site_name = str(site or "").strip()
    if site_name == "rule34.xxx":
        return get_rule34_credentials()
    if site_name == "gelbooru.com":
        return get_gelbooru_credentials()
    return "", ""


def has_booru_credentials(site):
    user_id, api_key = get_booru_credentials(site)
    return bool(user_id and api_key)


def get_booru_auth_hint(site):
    site_name = str(site or "").strip()
    if site_name == "rule34.xxx" and not has_booru_credentials(site_name):
        return "rule34.xxx: set RULE34_USER_ID and RULE34_API_KEY in .env to use api.rule34.xxx"
    if site_name == "gelbooru.com" and not has_booru_credentials(site_name):
        return "gelbooru.com: set GELBOORU_USER_ID and GELBOORU_API_KEY in .env for authenticated DAPI access"
    return None


def build_booru_api_url(domain, params):
    query_params = dict(params)
    host = domain
    user_id, api_key = get_booru_credentials(domain)
    if user_id and api_key:
        query_params["user_id"] = user_id
        query_params["api_key"] = api_key
        if domain == "rule34.xxx":
            host = RULE34_API_HOST
    return f"https://{host}/index.php?{urlencode(query_params)}"


def build_gelbooru_post_api_url(domain, post_id):
    if domain in {"gelbooru.com", "rule34.xxx"}:
        return build_booru_api_url(domain, {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "id": post_id,
            "json": "1",
        })
    return (
        f"https://{domain}/index.php?"
        f"{urlencode({'page': 'dapi', 's': 'post', 'q': 'index', 'id': post_id, 'json': '1'})}"
    )


def build_gelbooru_md5_api_url(domain, md5_hash):
    if domain in {"gelbooru.com", "rule34.xxx"}:
        return build_booru_api_url(domain, {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
            "tags": f"md5:{md5_hash}",
        })
    return (
        f"https://{domain}/index.php?"
        f"{urlencode({'page': 'dapi', 's': 'post', 'q': 'index', 'json': '1', 'tags': f'md5:{md5_hash}'})}"
    )


def domain_matches(domain, values):
    for value in values:
        if domain == value or domain.endswith("." + value):
            return True
    return False


def classify_lookup_source_url(url):
    parsed = urlparse(url)
    domain = normalize_domain(parsed.netloc)
    query = parse_qs(parsed.query)

    if domain_matches(domain, ["danbooru.donmai.us"]) and re.search(r"/posts/(\d+)", parsed.path):
        return "network", "danbooru"
    if domain_matches(domain, ["e621.net"]) and re.search(r"/posts/(\d+)", parsed.path):
        return "network", "e621"
    if domain_matches(domain, ["e926.net"]) and re.search(r"/posts/(\d+)", parsed.path):
        return "network", "e926"
    if domain_matches(domain, ["yande.re"]) and re.search(r"/post/show/(\d+)", parsed.path):
        return "network", "yandere"
    if domain_matches(domain, ["konachan.com", "konachan.net"]) and re.search(r"/post/show/(\d+)", parsed.path):
        return "network", "konachan"

    if domain_matches(domain, ["gelbooru.com", "rule34.xxx", RULE34_API_HOST, "safebooru.org"]):
        is_post_view = (
            query.get("page", [""])[0] == "post" and
            query.get("s", [""])[0] == "view" and
            query.get("id", [""])[0].isdigit()
        )
        has_post_path = re.search(r"/posts?/(\d+)", parsed.path)
        if is_post_view or has_post_path:
            canonical_site = {
                "gelbooru.com": "gelbooru",
                "rule34.xxx": "rule34",
                RULE34_API_HOST: "rule34",
                "safebooru.org": "safebooru",
            }.get(domain)
            if canonical_site:
                return "network", canonical_site

    return "local", None


def lookup_danbooru(parsed, timeout):
    domain = normalize_domain(parsed.netloc)
    if not domain_matches(domain, ["danbooru.donmai.us"]):
        return None

    match = re.search(r"/posts/(\d+)", parsed.path)
    if not match:
        return None

    post = fetch_json(f"https://danbooru.donmai.us/posts/{match.group(1)}.json", timeout)
    facts = make_source_facts()
    set_source_site(facts, "danbooru")
    facts["artists"] = split_space_tags(post.get("tag_string_artist"))
    extend_source_group(facts, "general", split_space_tags(post.get("tag_string_general")))
    extend_source_group(facts, "character", split_space_tags(post.get("tag_string_character")))
    extend_source_group(facts, "series", split_space_tags(post.get("tag_string_copyright")))
    extend_source_group(facts, "meta", split_space_tags(post.get("tag_string_meta")))
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    extend_source_values(facts, "filetypes", [post.get("file_ext")])
    return finalize_source_facts(facts)


def lookup_e621(parsed, timeout):
    domain = normalize_domain(parsed.netloc)
    if not domain_matches(domain, ["e621.net", "e926.net"]):
        return None

    match = re.search(r"/posts/(\d+)", parsed.path)
    if not match:
        return None

    response = fetch_json(f"https://{domain}/posts/{match.group(1)}.json", timeout)
    post = response.get("post", {})
    tag_groups = post.get("tags", {})

    facts = make_source_facts()
    set_source_site(facts, domain)
    if isinstance(tag_groups, dict):
        for key, value in tag_groups.items():
            if key == "artist":
                continue
            if key == "copyright":
                extend_source_group(facts, "series", split_space_tags(value))
            elif key == "species":
                extend_source_group(facts, "species", split_space_tags(value))
            elif key == "character":
                extend_source_group(facts, "character", split_space_tags(value))
            elif key == "meta":
                extend_source_group(facts, "meta", split_space_tags(value))
            elif key in {"general", "lore"}:
                extend_source_group(facts, key, split_space_tags(value))
            else:
                extend_source_group(facts, "general", split_space_tags(value))

    facts["artists"] = split_space_tags(tag_groups.get("artist", [])) if isinstance(tag_groups, dict) else []
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    file_info = post.get("file", {})
    if isinstance(file_info, dict):
        extend_source_values(facts, "filetypes", [file_info.get("ext")])
    return finalize_source_facts(facts)


def lookup_moebooru(parsed, timeout):
    domain = normalize_domain(parsed.netloc)
    if not domain_matches(domain, ["yande.re", "konachan.com", "konachan.net"]):
        return None

    match = re.search(r"/post/show/(\d+)", parsed.path)
    if not match:
        return None

    response = fetch_json(f"https://{domain}/post.json?tags=id:{match.group(1)}", timeout)
    if not isinstance(response, list) or not response:
        return None

    post = response[0]
    artists = []
    author = str(post.get("author", "")).strip()
    if author and author.lower() != "none":
        artists.append(author)

    facts = make_source_facts()
    set_source_site(facts, domain)
    facts["artists"] = artists
    extend_source_group(facts, "general", split_space_tags(post.get("tags")))
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    extend_source_values(facts, "filetypes", [post.get("file_ext")])
    return finalize_source_facts(facts)


def lookup_gelbooru(parsed, timeout):
    domain = normalize_domain(parsed.netloc)
    if domain == RULE34_API_HOST:
        domain = "rule34.xxx"
    query = parse_qs(parsed.query)
    post_id = None

    if query.get("page", [""])[0] == "post" and query.get("s", [""])[0] == "view":
        post_id = query.get("id", [""])[0]
    else:
        match = re.search(r"/posts?/(\d+)", parsed.path)
        if match:
            post_id = match.group(1)

    if not post_id or not post_id.isdigit():
        return None

    api_url = build_gelbooru_post_api_url(domain, post_id)
    response = fetch_json(api_url, timeout)

    post = None
    if isinstance(response, list) and response:
        post = response[0]
    elif isinstance(response, dict):
        maybe_post = response.get("post")
        if isinstance(maybe_post, list) and maybe_post:
            post = maybe_post[0]
        elif isinstance(maybe_post, dict):
            post = maybe_post

    if not isinstance(post, dict):
        return None

    artists = []
    owner = str(post.get("owner", "")).strip()
    if owner:
        artists.append(owner)

    facts = make_source_facts()
    set_source_site(facts, domain)
    facts["artists"] = artists
    extend_source_group(facts, "general", split_space_tags(post.get("tags")))
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    extend_source_values(facts, "filetypes", [post.get("file_ext")])
    return finalize_source_facts(facts)


def lookup_danbooru_by_md5(md5_hash, timeout):
    response = fetch_json_or_none(
        f"https://danbooru.donmai.us/posts.json?tags=md5:{md5_hash}",
        timeout,
    )
    if not isinstance(response, list) or not response:
        return None

    post = response[0]
    facts = make_source_facts()
    set_source_site(facts, "danbooru")
    facts["artists"] = split_space_tags(post.get("tag_string_artist"))
    extend_source_group(facts, "general", split_space_tags(post.get("tag_string_general")))
    extend_source_group(facts, "character", split_space_tags(post.get("tag_string_character")))
    extend_source_group(facts, "series", split_space_tags(post.get("tag_string_copyright")))
    extend_source_group(facts, "meta", split_space_tags(post.get("tag_string_meta")))
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    extend_source_values(facts, "filetypes", [post.get("file_ext")])
    return finalize_source_facts(facts)


def lookup_e621_by_md5(domain, md5_hash, timeout):
    response = fetch_json_or_none(
        f"https://{domain}/posts.json?tags=md5:{md5_hash}",
        timeout,
    )
    if not isinstance(response, dict):
        return None

    posts = response.get("posts", [])
    if not isinstance(posts, list) or not posts:
        return None

    post = posts[0]
    tag_groups = post.get("tags", {})
    facts = make_source_facts()
    set_source_site(facts, domain)
    if isinstance(tag_groups, dict):
        for key, value in tag_groups.items():
            if key == "artist":
                continue
            if key == "copyright":
                extend_source_group(facts, "series", split_space_tags(value))
            elif key == "species":
                extend_source_group(facts, "species", split_space_tags(value))
            elif key == "character":
                extend_source_group(facts, "character", split_space_tags(value))
            elif key == "meta":
                extend_source_group(facts, "meta", split_space_tags(value))
            elif key in {"general", "lore"}:
                extend_source_group(facts, key, split_space_tags(value))
            else:
                extend_source_group(facts, "general", split_space_tags(value))

    facts["artists"] = split_space_tags(tag_groups.get("artist", [])) if isinstance(tag_groups, dict) else []
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    file_info = post.get("file", {})
    if isinstance(file_info, dict):
        extend_source_values(facts, "filetypes", [file_info.get("ext")])
    return finalize_source_facts(facts)


def lookup_moebooru_by_md5(domain, md5_hash, timeout):
    response = fetch_json_or_none(
        f"https://{domain}/post.json?tags=md5:{md5_hash}",
        timeout,
    )
    if not isinstance(response, list) or not response:
        return None

    post = response[0]
    artists = []
    author = str(post.get("author", "")).strip()
    if author and author.lower() != "none":
        artists.append(author)

    facts = make_source_facts()
    set_source_site(facts, domain)
    facts["artists"] = artists
    extend_source_group(facts, "general", split_space_tags(post.get("tags")))
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    extend_source_values(facts, "filetypes", [post.get("file_ext")])
    return finalize_source_facts(facts)


def lookup_gelbooru_by_md5(domain, md5_hash, timeout):
    response = fetch_json_or_none(
        build_gelbooru_md5_api_url(domain, md5_hash),
        timeout,
    )

    post = None
    if isinstance(response, list) and response:
        post = response[0]
    elif isinstance(response, dict):
        maybe_post = response.get("post")
        if isinstance(maybe_post, list) and maybe_post:
            post = maybe_post[0]
        elif isinstance(maybe_post, dict):
            post = maybe_post

    if not isinstance(post, dict):
        return None

    artists = []
    owner = str(post.get("owner", "")).strip()
    if owner:
        artists.append(owner)

    facts = make_source_facts()
    set_source_site(facts, domain)
    facts["artists"] = artists
    extend_source_group(facts, "general", split_space_tags(post.get("tags")))
    facts["years"] = [coerce_year(post.get("created_at"))]
    extend_source_values(facts, "ratings", [normalize_source_rating(post.get("rating"))])
    extend_source_values(facts, "filetypes", [post.get("file_ext")])
    return finalize_source_facts(facts)


def extract_artist_from_url(parsed):
    domain = normalize_domain(parsed.netloc)
    parts = [part for part in parsed.path.split("/") if part]

    if domain_matches(domain, ["twitter.com", "x.com"]) and len(parts) >= 3 and parts[1] == "status":
        return parts[0]

    if domain == "bsky.app" and len(parts) >= 4 and parts[0] == "profile" and parts[2] == "post":
        return parts[1]

    if domain_matches(domain, ["deviantart.com"]) and len(parts) >= 3 and parts[1] == "art":
        return parts[0]

    if domain == "newgrounds.com" and len(parts) >= 4 and parts[0] == "art" and parts[1] == "view":
        return parts[2]

    if domain.endswith(".tumblr.com") and parts and parts[0] == "post":
        return domain.split(".", 1)[0]

    if domain == "instagram.com" and len(parts) >= 2 and parts[1] in {"p", "reel"}:
        return parts[0]

    return None


def extract_years_from_url(parsed):
    years = []
    for match in YEAR_RE.finditer(parsed.path):
        year = int(match.group(0))
        if 1900 <= year <= 2100:
            years.append(year)
    return dedupe_keep_order(years)


def lookup_source_url(url, timeout, cache):
    def build_lookup():
        parsed = urlparse(url)
        site = normalize_domain(parsed.netloc)
        canonical_site = "rule34.xxx" if site == RULE34_API_HOST else site
        result = make_source_facts()
        set_source_site(result, canonical_site)

        try:
            api_result = (
                lookup_e621(parsed, timeout)
                or lookup_danbooru(parsed, timeout)
                or lookup_moebooru(parsed, timeout)
                or lookup_gelbooru(parsed, timeout)
            )
            if api_result:
                merge_source_facts_into(result, api_result)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            result["notes"].append(f"{site}: {exc}")
            auth_hint = get_booru_auth_hint(canonical_site)
            if auth_hint:
                result["notes"].append(auth_hint)

        heuristic_artist = extract_artist_from_url(parsed)
        if heuristic_artist:
            result["artists"].append(heuristic_artist)

        result["years"].extend(extract_years_from_url(parsed))
        result["artists"] = dedupe_keep_order(result["artists"])
        finalize_source_facts(result)
        result["years"] = [year for year in result["years"] if year]
        return result

    return get_singleflight_cached(cache, url, build_lookup)


def collect_source_facts(urls, timeout, cache, allow_parallel=True, stop_when=None):
    facts = make_source_facts()
    ordered_urls = dedupe_keep_order(urls)
    if not ordered_urls:
        return facts

    if stop_when is not None and len(ordered_urls) > 1:
        local_urls = []
        prioritized_network_groups = []
        remaining_network_urls = []
        grouped_network_urls = {site: [] for group in SOURCE_URL_PRIORITY_GROUPS for site in group}

        for url in ordered_urls:
            lookup_kind, canonical_site = classify_lookup_source_url(url)
            if lookup_kind == "local":
                local_urls.append(url)
            elif canonical_site in grouped_network_urls:
                grouped_network_urls[canonical_site].append(url)
            else:
                remaining_network_urls.append(url)

        for group in SOURCE_URL_PRIORITY_GROUPS:
            group_urls = []
            for site in group:
                group_urls.extend(grouped_network_urls[site])
            if group_urls:
                prioritized_network_groups.append(group_urls)

        if remaining_network_urls:
            prioritized_network_groups.append(remaining_network_urls)

        for url in local_urls:
            lookup = lookup_source_url(url, timeout, cache)
            merge_source_facts_into(facts, lookup)
            finalize_source_facts(facts)
            if stop_when(facts):
                return facts

        for lookup_group in prioritized_network_groups:
            if len(lookup_group) == 1 or not allow_parallel:
                lookup_results = [lookup_source_url(url, timeout, cache) for url in lookup_group]
            else:
                max_workers = min(SOURCE_LOOKUP_MAX_WORKERS, len(lookup_group))
                indexed_results = {}
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_index = {
                        executor.submit(lookup_source_url, url, timeout, cache): index
                        for index, url in enumerate(lookup_group)
                    }
                    for future in as_completed(future_to_index):
                        indexed_results[future_to_index[future]] = future.result()
                lookup_results = [indexed_results[index] for index in range(len(lookup_group))]

            for lookup in lookup_results:
                merge_source_facts_into(facts, lookup)
                finalize_source_facts(facts)
                if stop_when(facts):
                    return facts
        return facts

    if stop_when is not None and (len(ordered_urls) == 1 or not allow_parallel):
        for url in ordered_urls:
            lookup = lookup_source_url(url, timeout, cache)
            merge_source_facts_into(facts, lookup)
            finalize_source_facts(facts)
            if stop_when(facts):
                return facts
        return facts
    if len(ordered_urls) == 1 or not allow_parallel:
        lookup_results = [lookup_source_url(url, timeout, cache) for url in ordered_urls]
    else:
        max_workers = min(SOURCE_LOOKUP_MAX_WORKERS, len(ordered_urls))
        indexed_results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(lookup_source_url, url, timeout, cache): index
                for index, url in enumerate(ordered_urls)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                indexed_results[index] = future.result()
                if stop_when is not None:
                    lookup = indexed_results[index]
                    merge_source_facts_into(facts, lookup)
                    finalize_source_facts(facts)
                    if stop_when(facts):
                        for pending_future in future_to_index:
                            if pending_future is not future:
                                pending_future.cancel()
                        return facts
        if stop_when is not None:
            return finalize_source_facts(facts)
        lookup_results = [indexed_results[index] for index in range(len(ordered_urls))]

    for lookup in lookup_results:
        merge_source_facts_into(facts, lookup)

    return finalize_source_facts(facts)


def resolve_alt_hash_to_sha256(client, hash_value, hash_type):
    response = client._api_request(
        "GET",
        "/get_files/file_hashes",
        params={
            "hash": hash_value,
            "source_hash_type": hash_type,
            "desired_hash_type": "sha256",
        },
    ).json()

    hashes = response.get("hashes", {})
    if isinstance(hashes, dict):
        for key, value in hashes.items():
            if str(key).lower() == hash_value.lower():
                if isinstance(value, str):
                    return value
                if isinstance(value, list) and value:
                    return value[0]

        for value in hashes.values():
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value:
                return value[0]

    if isinstance(hashes, list) and hashes:
        return str(hashes[0])

    return None


def resolve_hashes(client, hash_values, source_hash_type, desired_hash_type):
    remaining = [value.lower() for value in hash_values if value]
    resolved = {}

    for batch in chunked(remaining, 250):
        response = client._api_request(
            "GET",
            "/get_files/file_hashes",
            params={
                "hashes": json.dumps(batch),
                "source_hash_type": source_hash_type,
                "desired_hash_type": desired_hash_type,
            },
        ).json()

        hashes = response.get("hashes", {})
        if isinstance(hashes, dict):
            for key, value in hashes.items():
                key_text = str(key).lower()
                if isinstance(value, str):
                    resolved[key_text] = value.lower()
                elif isinstance(value, list) and value:
                    resolved[key_text] = str(value[0]).lower()

    return resolved


def resolve_url_to_hashes(client, url, doublecheck_file_system):
    params = {"url": url}
    if doublecheck_file_system is not None:
        params["doublecheck_file_system"] = json.dumps(bool(doublecheck_file_system))

    response = client._api_request("GET", "/add_urls/get_url_files", params=params).json()
    hashes = []
    for status in response.get("url_file_statuses", []):
        if isinstance(status, dict):
            file_hash = status.get("hash")
            if file_hash:
                hashes.append(file_hash)
    return dedupe_keep_order(hashes)


def resolve_lookup_urls(client, url_values, doublecheck_file_system):
    ordered_urls = dedupe_keep_order(url_values)
    if not ordered_urls:
        return {}

    if len(ordered_urls) == 1:
        url = ordered_urls[0]
        return {url: resolve_url_to_hashes(client, url, doublecheck_file_system)}

    resolved = {}
    max_workers = min(LOOKUP_URL_RESOLVE_MAX_WORKERS, len(ordered_urls))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(resolve_url_to_hashes, client, url, doublecheck_file_system): url
            for url in ordered_urls
        }
        for future in as_completed(future_to_url):
            resolved[future_to_url[future]] = future.result()
    return resolved


def resolve_lookup_file(client, lookupfile, doublecheck_file_system):
    hash_inputs = defaultdict(list)
    file_id_inputs = defaultdict(list)
    unresolved = []

    alt_hash_entries_by_type = defaultdict(list)
    url_entries_by_value = defaultdict(list)

    for line_number, raw_value in read_lookup_lines(lookupfile):
        lookup_type, lookup_value = parse_lookup_entry(raw_value)

        if lookup_type == "sha256":
            hash_inputs[lookup_value.lower()].append(raw_value)
        elif lookup_type == "file_id":
            file_id_inputs[int(lookup_value)].append(raw_value)
        elif lookup_type == "url":
            url_entries_by_value[lookup_value].append((line_number, raw_value))
        else:
            alt_hash_entries_by_type[lookup_type].append((line_number, raw_value, lookup_value.lower()))

    for lookup_type, entries in alt_hash_entries_by_type.items():
        unique_values = dedupe_keep_order([lookup_value for _, _, lookup_value in entries])
        try:
            resolved_by_input = resolve_hashes(client, unique_values, lookup_type, "sha256")
        except hydrus_api.InsufficientAccess:
            for line_number, raw_value, _ in entries:
                unresolved.append(f"line {line_number}: {raw_value} -> Hydrus access denied")
            continue
        except hydrus_api.APIError as exc:
            for line_number, raw_value, _ in entries:
                unresolved.append(f"line {line_number}: {raw_value} -> Hydrus API error: {exc}")
            continue

        for line_number, raw_value, lookup_value in entries:
            resolved_hash = resolved_by_input.get(lookup_value)
            if not resolved_hash:
                unresolved.append(f"line {line_number}: {raw_value} -> no matching sha256 in Hydrus")
            else:
                hash_inputs[resolved_hash.lower()].append(raw_value)

    if url_entries_by_value:
        unique_urls = list(url_entries_by_value.keys())
        try:
            resolved_hashes_by_url = resolve_lookup_urls(client, unique_urls, doublecheck_file_system)
        except hydrus_api.InsufficientAccess:
            for url, entries in url_entries_by_value.items():
                for line_number, raw_value in entries:
                    unresolved.append(
                        f"line {line_number}: {raw_value} -> access key is missing "
                        "permission 0 (Import and Edit URLs) for direct URL lookups"
                    )
        except hydrus_api.APIError as exc:
            for url, entries in url_entries_by_value.items():
                for line_number, raw_value in entries:
                    unresolved.append(f"line {line_number}: {raw_value} -> Hydrus API error: {exc}")
        else:
            for url, entries in url_entries_by_value.items():
                resolved_hashes = resolved_hashes_by_url.get(url, [])
                if not resolved_hashes:
                    for line_number, raw_value in entries:
                        unresolved.append(f"line {line_number}: {raw_value} -> nothing matched in Hydrus")
                    continue
                for _, raw_value in entries:
                    for resolved_hash in resolved_hashes:
                        hash_inputs[resolved_hash.lower()].append(raw_value)

    return hash_inputs, file_id_inputs, unresolved


def fetch_metadata_records(client, hashes, file_ids):
    metadata_records = []

    for batch_hashes in chunked(hashes, 250):
        metadata_records.extend(
            client.get_file_metadata(hashes=batch_hashes, detailed_url_information=True)
        )

    for batch_file_ids in chunked(file_ids, 250):
        metadata_records.extend(
            client.get_file_metadata(file_ids=batch_file_ids, detailed_url_information=True)
        )

    deduped_records = []
    seen_hashes = set()
    for record in metadata_records:
        record_hash = record.get("hash")
        if not record_hash:
            continue
        record_hash = str(record_hash).lower()
        if record_hash in seen_hashes:
            continue
        seen_hashes.add(record_hash)
        deduped_records.append(record)
    return deduped_records


def collect_metadata_hashes(metadata_records):
    record_hashes = []
    for record in metadata_records:
        record_hash = record.get("hash")
        if record_hash:
            record_hashes.append(str(record_hash).lower())
    return dedupe_keep_order(record_hashes)


def lookup_sources_by_md5(
    md5_hash,
    timeout,
    cache,
    requested_sites,
    allow_parallel=True,
    stop_when=None,
):
    md5_key = md5_hash.lower()
    cache_key = (
        md5_key,
        tuple(requested_sites),
        "early" if stop_when is not None else "full",
    )

    def build_lookup():
        facts = make_source_facts()

        lookups = {
            "danbooru": ("danbooru", lambda: lookup_danbooru_by_md5(md5_key, timeout)),
            "e621": ("e621.net", lambda: lookup_e621_by_md5("e621.net", md5_key, timeout)),
            "e926": ("e926.net", lambda: lookup_e621_by_md5("e926.net", md5_key, timeout)),
            "yandere": ("yande.re", lambda: lookup_moebooru_by_md5("yande.re", md5_key, timeout)),
            "konachan": ("konachan.com", lambda: lookup_moebooru_by_md5("konachan.com", md5_key, timeout)),
            "gelbooru": ("gelbooru.com", lambda: lookup_gelbooru_by_md5("gelbooru.com", md5_key, timeout)),
            "rule34": ("rule34.xxx", lambda: lookup_gelbooru_by_md5("rule34.xxx", md5_key, timeout)),
            "safebooru": ("safebooru.org", lambda: lookup_gelbooru_by_md5("safebooru.org", md5_key, timeout)),
        }

        requested_lookup_items = [(site, lookups[site]) for site in requested_sites]
        if stop_when is not None and allow_parallel and len(requested_lookup_items) > 1:
            requested_lookup_map = dict(requested_lookup_items)
            grouped_lookup_items = []
            seen_sites = set()

            for group in SOURCE_MD5_PRIORITY_GROUPS:
                group_items = []
                for site in group:
                    if site in requested_lookup_map:
                        group_items.append((site, requested_lookup_map[site]))
                        seen_sites.add(site)
                if group_items:
                    grouped_lookup_items.append(group_items)

            remaining_items = [
                (site, lookup)
                for site, lookup in requested_lookup_items
                if site not in seen_sites
            ]
            if remaining_items:
                grouped_lookup_items.append(remaining_items)

            for lookup_group in grouped_lookup_items:
                max_workers = min(SOURCE_LOOKUP_MAX_WORKERS, len(lookup_group))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_site = {
                        executor.submit(lookup): (canonical_site, site)
                        for canonical_site, (site, lookup) in lookup_group
                    }
                    for future in as_completed(future_to_site):
                        canonical_site, site = future_to_site[future]
                        try:
                            result = future.result()
                            if result:
                                merge_source_facts_into(facts, result)
                                finalize_source_facts(facts)
                                if stop_when(facts):
                                    for pending_future in future_to_site:
                                        if pending_future is not future:
                                            pending_future.cancel()
                                    return facts
                        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                            facts["notes"].append(f"{site}: {exc}")
                            auth_hint = get_booru_auth_hint(site)
                            if auth_hint:
                                facts["notes"].append(auth_hint)
            return finalize_source_facts(facts)

        if len(requested_lookup_items) == 1 or not allow_parallel:
            lookup_results = []
            for canonical_site, (site, lookup) in requested_lookup_items:
                try:
                    result = lookup()
                    lookup_results.append((canonical_site, site, result))
                    if result:
                        merge_source_facts_into(facts, result)
                        finalize_source_facts(facts)
                        if stop_when is not None and stop_when(facts):
                            return facts
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                    facts["notes"].append(f"{site}: {exc}")
                    auth_hint = get_booru_auth_hint(site)
                    if auth_hint:
                        facts["notes"].append(auth_hint)
        else:
            max_workers = min(SOURCE_LOOKUP_MAX_WORKERS, len(requested_lookup_items))
            lookup_results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_site = {
                    executor.submit(lookup): (canonical_site, site)
                    for canonical_site, (site, lookup) in requested_lookup_items
                }
                for future in as_completed(future_to_site):
                    canonical_site, site = future_to_site[future]
                    try:
                        result = future.result()
                        lookup_results.append((canonical_site, site, result))
                        if result:
                            merge_source_facts_into(facts, result)
                            finalize_source_facts(facts)
                            if stop_when is not None and stop_when(facts):
                                for pending_future in future_to_site:
                                    if pending_future is not future:
                                        pending_future.cancel()
                                return facts
                    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                        facts["notes"].append(f"{site}: {exc}")
                        auth_hint = get_booru_auth_hint(site)
                        if auth_hint:
                            facts["notes"].append(auth_hint)
            if stop_when is not None:
                return finalize_source_facts(facts)

            requested_order = {site_name: index for index, site_name in enumerate(requested_sites)}
            lookup_results.sort(key=lambda item: requested_order[item[0]])

        for _, site, result in lookup_results:
            if result:
                merge_source_facts_into(facts, result)

        return finalize_source_facts(facts)

    return get_singleflight_cached(cache, cache_key, build_lookup)
