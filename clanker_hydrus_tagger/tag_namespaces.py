from .source_lookup_common import KAOMOJIS, NO_NAMESPACE_VALUES, SKIP_NAMESPACE_VALUES, dedupe_keep_order

MODEL_TAG_NAMESPACE_FIELDS = (
    "general",
    "character",
    "copyright",
    "artist",
    "meta",
    "species",
    "lore",
    "rating",
    "year",
)

MODEL_TAG_NAMESPACE_ALIASES = {
    "creator": "artist",
    "series": "copyright",
}

DEFAULT_MODEL_NAMESPACE_CONFIG = {
    "general": "",
    "character": "character",
    "copyright": "copyright",
    "artist": "creator",
    "meta": "meta",
    "species": "species",
    "lore": "lore",
    "rating": "rating",
    "year": "year",
}

DANBOORU_NUMERIC_CATEGORIES = {
    "0": "general",
    "1": "artist",
    "3": "copyright",
    "4": "character",
    "5": "meta",
    "9": "rating",
}

E621_NUMERIC_CATEGORIES = {
    "0": "general",
    "1": "artist",
    "3": "copyright",
    "4": "character",
    "5": "species",
    "7": "meta",
}


def normalize_model_category(category):
    clean_category = str(category or "").strip().lower()
    return MODEL_TAG_NAMESPACE_ALIASES.get(clean_category, clean_category)


def build_model_category_aliases(tag_to_category):
    raw_categories = {normalize_model_category(value) for value in tag_to_category.values()}
    if "7" in raw_categories:
        return E621_NUMERIC_CATEGORIES
    if "9" in raw_categories:
        return DANBOORU_NUMERIC_CATEGORIES
    return {}


def resolve_model_category(category, category_aliases):
    return category_aliases.get(normalize_model_category(category), normalize_model_category(category))


def get_model_tag_category(tag, tag_to_category):
    category_aliases = build_model_category_aliases(tag_to_category)
    return resolve_model_category(tag_to_category.get(tag, ""), category_aliases)


def get_model_tag_namespace(category, namespace_config):
    return namespace_config.get(normalize_model_category(category), "")


def format_model_tag_value(tag, category=""):
    value = str(tag)
    category = normalize_model_category(category)

    if category == "rating" and value.startswith("rating_"):
        value = value[len("rating_"):]
    elif category == "year" and value.startswith("year_"):
        value = value[len("year_"):]

    if value not in KAOMOJIS:
        value = value.replace("_", " ")
    return value


def build_namespaced_model_tag(namespace, tag, category=""):
    value = format_model_tag_value(tag, category)
    clean_namespace = str(namespace or "").strip().strip(":")
    if clean_namespace.lower() in NO_NAMESPACE_VALUES:
        clean_namespace = ""
    if clean_namespace:
        return f"{clean_namespace}:{value}"
    return value


def parse_model_namespace_config(raw_value):
    config = dict(DEFAULT_MODEL_NAMESPACE_CONFIG)

    if raw_value is None:
        return config

    raw_value = str(raw_value).strip()
    if not raw_value or raw_value.lower() == "auto":
        return config

    if "=" not in raw_value:
        return {field: raw_value for field in MODEL_TAG_NAMESPACE_FIELDS}

    parts = [part.strip() for part in raw_value.split(",") if part.strip()]
    for part in parts:
        if "=" not in part:
            raise ValueError(f"Invalid namespace config entry: {part}")

        key, value = part.split("=", 1)
        key = normalize_model_category(key)
        value = value.strip()

        if key == "all":
            for field in MODEL_TAG_NAMESPACE_FIELDS:
                config[field] = value
            continue

        if key not in MODEL_TAG_NAMESPACE_FIELDS:
            valid_groups = ", ".join(("all",) + MODEL_TAG_NAMESPACE_FIELDS)
            raise ValueError(
                f"Unknown model tag namespace key: {key}. Valid groups: {valid_groups}."
            )
        config[key] = value

    return config


def parse_model_skip_existing_namespaces(raw_value):
    if raw_value is None:
        return set()

    raw_value = str(raw_value).strip()
    if not raw_value or raw_value.lower() in NO_NAMESPACE_VALUES:
        return set()

    if raw_value.lower() == "all":
        return set(MODEL_TAG_NAMESPACE_FIELDS)

    categories = set()
    parts = [part.strip() for part in raw_value.split(",") if part.strip()]
    for part in parts:
        category = normalize_model_category(part)
        if category not in MODEL_TAG_NAMESPACE_FIELDS:
            valid_groups = ", ".join(("all",) + MODEL_TAG_NAMESPACE_FIELDS)
            raise ValueError(
                f"Unknown model tag skip-existing key: {category}. Valid groups: {valid_groups}."
            )
        categories.add(category)
    return categories


def filter_model_tags_by_existing_namespaces(
    clipped_tags,
    existing_tags,
    tag_to_category,
    namespace_config,
    skip_existing_categories,
):
    if not clipped_tags or not existing_tags or not skip_existing_categories:
        return list(clipped_tags)

    existing_prefixes = set()
    for existing_tag in existing_tags:
        if ":" not in str(existing_tag):
            continue
        prefix, _ = str(existing_tag).split(":", 1)
        existing_prefixes.add(prefix.strip().casefold())

    if not existing_prefixes:
        return list(clipped_tags)

    filtered_tags = []
    for tag in clipped_tags:
        category = get_model_tag_category(tag, tag_to_category)
        if category in skip_existing_categories:
            namespace = str(get_model_tag_namespace(category, namespace_config) or "").strip().strip(":")
            if namespace and namespace.casefold() in existing_prefixes:
                continue
        filtered_tags.append(tag)
    return filtered_tags


def format_model_output_tags(tags, tag_to_category, namespace_config):
    formatted_tags = []
    category_aliases = build_model_category_aliases(tag_to_category)

    for tag in tags:
        category = resolve_model_category(tag_to_category.get(tag, ""), category_aliases)
        namespace = namespace_config.get(category, "")
        if str(namespace).strip().strip(":").lower() in SKIP_NAMESPACE_VALUES:
            continue
        formatted_tags.append(build_namespaced_model_tag(namespace, tag, category))

    return dedupe_keep_order(formatted_tags)


def format_model_rating_tag(rating_tag, namespace_config):
    if not rating_tag:
        return None

    namespace = namespace_config.get("rating", "rating")
    if str(namespace).strip().strip(":").lower() in SKIP_NAMESPACE_VALUES:
        return None
    return build_namespaced_model_tag(namespace, rating_tag, "rating")
