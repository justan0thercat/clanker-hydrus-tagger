import os.path
from io import BytesIO

import click
import hydrus_api
from PIL import Image, ImageFile

from . import interrogate
from .model_info import load_model_info as read_model_info
from .source_lookup import extract_service_storage_tags, parse_namespace_config, run_lookup
from .tag_namespaces import (
    filter_model_tags_by_existing_namespaces,
    format_model_output_tags,
    format_model_rating_tag,
    parse_model_namespace_config,
    parse_model_skip_existing_namespaces,
)

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

kaomojis = [
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
]


def get_rating(modelinfo, ratings):
    rating = "none"
    if modelinfo["ratingsflag"]:
        ratings["none"] = 0.0
        for key in ratings.keys():
            if ratings[key] > ratings[rating]:
                rating = key
    return rating


def resolve_runtime_options(modelinfo, threshold, max_tags):
    if threshold is None:
        threshold = modelinfo.get("default_threshold", 0.35)
    if max_tags is None:
        max_tags = modelinfo.get("default_max_tags", 200)
    return float(threshold), int(max_tags)


def clip_tags(tags_dict, threshold, max_tags):
    tags = [(tag, prob) for tag, prob in tags_dict.items() if prob > threshold]
    tags.sort(key=lambda item: item[1], reverse=True)
    if max_tags and max_tags > 0:
        tags = tags[:max_tags]
    return [tag for tag, _ in tags]


def format_tags(clipped_tags, tag_to_category=None, namespace_config=None):
    if tag_to_category is not None and namespace_config is not None:
        return format_model_output_tags(clipped_tags, tag_to_category, namespace_config)

    formatted_tags = []
    for tag in clipped_tags:
        if tag not in kaomojis:
            tag = tag.replace("_", " ")
        formatted_tags.append(tag)
    return formatted_tags


def load_metadata_records_by_hash(client, hashes):
    metadata_records = client.get_file_metadata(hashes=hashes)
    records_by_hash = {}
    for record in metadata_records:
        record_hash = str(record.get("hash") or "").lower()
        if record_hash:
            records_by_hash[record_hash] = record
    return records_by_hash


def extract_service_names(services_response):
    service_names = set()

    services_v2 = services_response.get("services_v2")
    if isinstance(services_v2, list):
        for service in services_v2:
            name = service.get("name")
            if name:
                service_names.add(name)

    services = services_response.get("services")
    if isinstance(services, dict):
        for service in services.values():
            if isinstance(service, dict):
                name = service.get("name")
                if name:
                    service_names.add(name)

    return sorted(service_names)


def create_hydrus_client(token, host):
    if not token or token == "REPLACE_WITH_HYDRUS_API_KEY":
        raise click.ClickException(
            "Hydrus API key is missing. Set HYDRUS_TOKEN in the launcher or pass --token."
        )

    client = hydrus_api.Client(token, host)

    try:
        client.verify_access_key()
    except hydrus_api.ConnectionError as exc:
        raise click.ClickException(
            "Could not connect to the Hydrus Client API at "
            f"{host}. Make sure Hydrus is open, Client API is enabled, "
            "and the host/port are correct."
        ) from exc
    except hydrus_api.InsufficientAccess as exc:
        raise click.ClickException(
            "Connected to Hydrus, but the API key was rejected or lacks permissions. "
            "Create an access key on this machine and allow file access/search and add tags."
        ) from exc
    except hydrus_api.APIError as exc:
        raise click.ClickException(f"Hydrus API error while verifying access: {exc}") from exc

    return client


def load_model_info(model):
    try:
        return read_model_info(model)
    except (FileNotFoundError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


def build_interrogator(model, modelinfo):
    normalization = modelinfo.get("normalization", None)
    color_order = modelinfo.get("color_order", "BGR")
    score_activation = modelinfo.get("score_activation", "auto")

    return interrogate.WaifuDiffusionInterrogator(
        modelinfo["modelname"],
        modelinfo["modelfile"],
        modelinfo["tagsfile"],
        model,
        modelinfo["ratingsflag"],
        modelinfo["numberofratings"],
        normalization=normalization,
        color_order=color_order,
        score_activation=score_activation,
        repo_id=modelinfo.get("repo_id"),
        repo_revision=modelinfo.get("repo_revision", "main"),
    )


def format_model_load_error(model, cpu, exc):
    raw_message = str(exc).strip() or exc.__class__.__name__
    message = raw_message.lower()

    details = [
        f'Model "{model}" failed to initialize.',
        f"Original error: {raw_message}",
    ]

    if (
        isinstance(exc, FileNotFoundError)
        or "model not found" in message
        or "missing required model files" in message
        or "no tag metadata found" in message
    ):
        details.append(
            f"Model files are missing or incomplete under model\\{model}. "
            "Restore that folder from a working install or let the app redownload the missing files."
        )
    elif "hugging face" in message or "couldn't download" in message or "couldn't reach" in message:
        details.append(
            "The launcher could not fetch a required model file from Hugging Face. "
            "Check internet access on that machine, or copy the full model folder from a working install."
        )
    elif (
        "cuda" in message
        or "cudnn" in message
        or "cublas" in message
        or "executionprovider" in message
        or "loadlibrary failed" in message
    ):
        details.append(
            "The GPU runtime failed to start. Run install_cpu.bat to force CPU mode, "
            "or rerun install_gpu.bat if this machine should use NVIDIA acceleration."
        )
    elif "onnxruntime" in message or "dll load failed" in message:
        details.append(
            "The ONNX Runtime installation looks broken or incomplete. "
            "Rerun install_cpu.bat or install_gpu.bat in this portable folder."
        )
    else:
        details.append(
            "This usually means broken runtime dependencies or an incomplete portable update. "
            "Rerun install_cpu.bat or install_gpu.bat and verify the model folder is present."
        )

    if not cpu:
        details.append("This launcher started in GPU mode. If the machine is unstable after the update, CPU mode is the fastest sanity check.")

    return "\n".join(details)


def load_interrogator(model, modelinfo, cpu):
    interrogator = build_interrogator(model, modelinfo)
    try:
        interrogator.load(cpu)
    except Exception as exc:
        raise click.ClickException(format_model_load_error(model, cpu, exc)) from exc
    return interrogator


def run_source_lookup_command(
    mode,
    lookupfile,
    token,
    host,
    tag_service,
    namespace,
    sites,
    privacy,
    timeout,
    report,
    doublecheck_file_system,
):
    client = create_hydrus_client(token, host)
    run_lookup(
        client,
        mode,
        lookupfile,
        tag_service,
        namespace,
        privacy,
        timeout,
        report,
        doublecheck_file_system,
        sites,
    )


@click.command(name="check-api")
@click.option("--token", help="The API token for your Hydrus server")
@click.option("--host", default="http://127.0.0.1:45869", help="The URL for your Hydrus server ")
@click.option("--tag-service", default="A.I. Tags", help="The Hydrus tag service to add tags to")
def check_api(token, host, tag_service):
    client = create_hydrus_client(token, host)

    api_info = client.get_api_version()
    access_info = client.verify_access_key()

    click.echo(f"Hydrus host: {host}")
    click.echo(f"Hydrus API version: {api_info.get('version', 'unknown')}")
    click.echo(f"Hydrus client version: {api_info.get('hydrus_version', 'unknown')}")
    click.echo(f"Access key name: {access_info.get('name', 'unknown')}")

    permits_everything = bool(access_info.get("permits_everything"))
    permissions = set(access_info.get("basic_permissions", []))

    if permits_everything:
        click.echo("Permissions: full access")
    else:
        click.echo("Permissions: " + ", ".join(str(permission) for permission in sorted(permissions)))
        missing_permissions = []
        if 2 not in permissions:
            missing_permissions.append("2 (Edit File Tags)")
        if 3 not in permissions:
            missing_permissions.append("3 (Search for and Fetch Files)")
        if missing_permissions:
            raise click.ClickException(
                "Access key is missing required permissions: " + ", ".join(missing_permissions)
            )

        if 0 in permissions:
            click.echo("Direct URL lookup: enabled")
        else:
            click.echo(
                "Direct URL lookup: disabled (sha256/md5/sha1/sha512/file_id still work, "
                "but raw URL lines in the search text files need permission 0)"
            )
    if permits_everything:
        click.echo("Direct URL lookup: enabled")

    try:
        services_response = client.get_services()
    except hydrus_api.InsufficientAccess as exc:
        raise click.ClickException(
            "Access key is valid, but Hydrus refused the service list request. "
            "Grant at least Add Tags or Search for and Fetch Files."
        ) from exc

    service_names = extract_service_names(services_response)
    if tag_service not in service_names:
        available = ", ".join(service_names) if service_names else "none returned"
        raise click.ClickException(
            f'Tag service "{tag_service}" was not found. Available services: {available}'
        )

    click.echo(f'Tag service "{tag_service}" found.')
    click.echo("Hydrus API check passed.")


@click.group()
def cli():
    pass


@click.command()
@click.argument("filename")
@click.option("--cpu", default=False, help="Use CPU instead of GPU")
@click.option("--model", default="wd-v1-4-vit-tagger-v2", help="The tagging model to use")
@click.option("--threshold", default=None, type=float, help="The threshold to drop tags below")
@click.option("--max-tags", default=None, type=int, help="Maximum content tags to output; set 0 to disable")
def evaluate(filename, cpu, model, threshold, max_tags):
    modelinfo = load_model_info(model)

    threshold, max_tags = resolve_runtime_options(modelinfo, threshold, max_tags)
    interrogator = load_interrogator(model, modelinfo, cpu)
    image = Image.open(filename)
    ratings, tags_dict = interrogator.interrogate(image)

    clipped_tags = clip_tags(tags_dict, threshold, max_tags)
    rating = get_rating(modelinfo, ratings)
    formatted_tags = format_tags(clipped_tags)

    click.echo("rating: " + rating)
    click.echo("tags: " + ", ".join(formatted_tags))


@click.command()
@click.argument("hash")
@click.option("--token", help="The API token for your Hydrus server")
@click.option("--cpu", default=False, help="Use CPU instead of GPU")
@click.option("--model", default="SmilingWolf/wd-v1-4-vit-tagger-v2", help="The tagging model to use")
@click.option("--threshold", default=None, type=float, help="The threshold to drop tags below")
@click.option("--host", default="http://127.0.0.1:45869", help="The URL for your Hydrus server ")
@click.option("--tag-service", default="A.I. Tags", help="The Hydrus tag service to add tags to")
@click.option("--ratings-only", default=False, help="Strip all tags except for content rating")
@click.option("--privacy", default=True, help="hides the tag output from the cli")
@click.option("--max-tags", default=None, type=int, help="Maximum content tags to send; set 0 to disable")
@click.option(
    "--namespace",
    default="auto",
    help="Namespace config for model tag categories. Use auto for category-aware Hydrus tags, or all=/plain for flat tags.",
)
@click.option(
    "--skip-existing",
    default="",
    help="Comma-separated model categories to suppress if the target tag service already has that namespace, for example artist,character.",
)
def evaluate_api(hash, token, cpu, model, threshold, host, tag_service, ratings_only, privacy, max_tags, namespace, skip_existing):
    modelinfo = load_model_info(model)

    if ratings_only and not modelinfo["ratingsflag"]:
        raise ValueError("--ratings-only set, but model does not support ratings!")

    threshold, max_tags = resolve_runtime_options(modelinfo, threshold, max_tags)
    namespace_config = parse_model_namespace_config(namespace)
    skip_existing_categories = parse_model_skip_existing_namespaces(skip_existing)

    client = create_hydrus_client(token, host)
    metadata_record = load_metadata_records_by_hash(client, [hash]).get(hash.lower())

    interrogator = load_interrogator(model, modelinfo, cpu)
    image_bytes = BytesIO(client.get_file(hash).content)
    image = Image.open(image_bytes)
    ratings, tags_dict = interrogator.interrogate(image)

    clipped_tags = clip_tags(tags_dict, threshold, max_tags)
    rating = get_rating(modelinfo, ratings)

    formatted_tags = []
    if not ratings_only:
        clipped_tags = filter_model_tags_by_existing_namespaces(
            clipped_tags,
            extract_service_storage_tags(metadata_record, tag_service) if metadata_record else set(),
            interrogator.tag_to_category,
            namespace_config,
            skip_existing_categories,
        )
        formatted_tags = format_tags(clipped_tags, interrogator.tag_to_category, namespace_config)

    if not privacy:
        click.echo("rating: " + rating)
        click.echo("tags: " + ", ".join(formatted_tags))

    if modelinfo["ratingsflag"]:
        hydrus_rating_tag = format_model_rating_tag(rating, namespace_config)
        if hydrus_rating_tag:
            formatted_tags.append(hydrus_rating_tag)
    if ratings_only:
        formatted_tags.append("ratings only " + modelinfo["modelname"] + " ai generated tags")
    else:
        formatted_tags.append(modelinfo["modelname"] + " ai generated tags")

    client.add_tags(hashes=[hash], service_names_to_tags={tag_service: formatted_tags})


@click.command()
@click.argument("hashfile")
@click.option("--token", help="The API token for your Hydrus server")
@click.option("--cpu", default=False, help="Use CPU instead of GPU")
@click.option("--model", default="wd-v1-4-vit-tagger-v2", help="The tagging model to use")
@click.option("--threshold", default=None, type=float, help="The threshold to drop tags below")
@click.option("--host", default="http://127.0.0.1:45869", help="The URL for your Hydrus server ")
@click.option("--tag-service", default="A.I. Tags", help="The Hydrus tag service to add tags to")
@click.option("--ratings-only", default=False, help="Strip all tags except for content rating")
@click.option("--privacy", default=True, help="hides the tag output from the cli")
@click.option("--batch-size", default=1, type=int, help="Process multiple hashes at once (batch size). Set higher to fully load GPU.")
@click.option("--batch-inference/--no-batch-inference", default=True, help="Use batched inference for multiple images (faster on GPU)")
@click.option("--max-tags", default=None, type=int, help="Maximum content tags per file to send; set 0 to disable")
@click.option(
    "--namespace",
    default="auto",
    help="Namespace config for model tag categories. Use auto for category-aware Hydrus tags, or all=/plain for flat tags.",
)
@click.option(
    "--skip-existing",
    default="",
    help="Comma-separated model categories to suppress if the target tag service already has that namespace, for example artist,character.",
)
def evaluate_api_batch(hashfile, token, cpu, model, threshold, host, tag_service, ratings_only, privacy, batch_size, batch_inference, max_tags, namespace, skip_existing):
    if not os.path.isfile(hashfile):
        raise ValueError("hashfile not found!")
    modelinfo = load_model_info(model)

    if ratings_only and not modelinfo["ratingsflag"]:
        raise ValueError("--ratings-only set, but model does not support ratings!")

    threshold, max_tags = resolve_runtime_options(modelinfo, threshold, max_tags)
    namespace_config = parse_model_namespace_config(namespace)
    skip_existing_categories = parse_model_skip_existing_namespaces(skip_existing)

    client = create_hydrus_client(token, host)

    interrogator = load_interrogator(model, modelinfo, cpu)

    with open(hashfile, encoding="utf-8") as hashfile_f:
        all_hashes = [line.strip() for line in hashfile_f if line.strip()]

    total = len(all_hashes)
    click.echo(f"Total files to process: {total}")

    for index in range(0, total, batch_size):
        batch_hashes = all_hashes[index:index + batch_size]
        batch_num = index // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        click.echo(f"--- Batch {batch_num}/{total_batches} (size {len(batch_hashes)}) ---")

        images = []
        valid_hashes = []
        for file_hash in batch_hashes:
            try:
                image_bytes = BytesIO(client.get_file(file_hash).content)
                image = Image.open(image_bytes)
                images.append(image)
                valid_hashes.append(file_hash)
            except Exception as exc:
                click.echo(f"  Error downloading {file_hash}: {exc}", err=True)

        if not images:
            click.echo("  No valid images in this batch, skipping.")
            continue

        metadata_records_by_hash = load_metadata_records_by_hash(client, valid_hashes)

        if batch_inference and len(images) > 1:
            results = interrogator.interrogate_batch(images)
        else:
            results = [interrogator.interrogate(img) for img in images]

        for file_hash, (ratings, tags_dict) in zip(valid_hashes, results):
            clipped_tags = clip_tags(tags_dict, threshold, max_tags)
            rating = get_rating(modelinfo, ratings)

            formatted_tags = []
            if not ratings_only:
                metadata_record = metadata_records_by_hash.get(file_hash.lower())
                clipped_tags = filter_model_tags_by_existing_namespaces(
                    clipped_tags,
                    extract_service_storage_tags(metadata_record, tag_service) if metadata_record else set(),
                    interrogator.tag_to_category,
                    namespace_config,
                    skip_existing_categories,
                )
                formatted_tags = format_tags(clipped_tags, interrogator.tag_to_category, namespace_config)

            if not privacy:
                click.echo(f"  {file_hash}: rating={rating}, tags={len(formatted_tags)}")

            if modelinfo["ratingsflag"]:
                hydrus_rating_tag = format_model_rating_tag(rating, namespace_config)
                if hydrus_rating_tag:
                    formatted_tags.append(hydrus_rating_tag)
            if ratings_only:
                formatted_tags.append(f"ratings only {modelinfo['modelname']} ai generated tags")
            else:
                formatted_tags.append(f"{modelinfo['modelname']} ai generated tags")

            client.add_tags(hashes=[file_hash], service_names_to_tags={tag_service: formatted_tags})

    click.echo("All batches processed.")


@click.command(name="search-artist")
@click.argument("lookupfile")
@click.option("--token", help="The API token for your Hydrus server")
@click.option("--host", default="http://127.0.0.1:45869", help="The URL for your Hydrus server ")
@click.option("--tag-service", default="A.I. Tags", help="The Hydrus tag service to add tags to")
@click.option("--namespace", default="creator", help="Namespace for found creator tags; use artist for artist:name if preferred")
@click.option("--sites", default="all", help="Comma-separated source sites to check, or all")
@click.option("--privacy", default=True, help="hides the tag output from the cli")
@click.option("--timeout", default=8, type=int, help="Timeout in seconds for source site requests")
@click.option("--report", default=None, help="Optional report file to write")
@click.option("--doublecheck-file-system", default=False, help="Ask Hydrus to recheck URL matches on disk")
def search_artist(lookupfile, token, host, tag_service, namespace, sites, privacy, timeout, report, doublecheck_file_system):
    run_source_lookup_command(
        "artist",
        lookupfile,
        token,
        host,
        tag_service,
        namespace,
        sites,
        privacy,
        timeout,
        report,
        doublecheck_file_system,
    )


@click.command(name="search-all")
@click.argument("lookupfile")
@click.option("--token", help="The API token for your Hydrus server")
@click.option("--host", default="http://127.0.0.1:45869", help="The URL for your Hydrus server ")
@click.option("--tag-service", default="A.I. Tags", help="The Hydrus tag service to add tags to")
@click.option(
    "--namespace",
    default="general=,copyright=copyright,character=character,meta=meta,species=species,lore=lore,rating=rating,year=year,site=source,filetype=filetype,artist=skip",
    help="Tag namespace config. Examples: all=, all=source, rating=rating, year=skip, artist=creator",
)
@click.option("--sites", default="all", help="Comma-separated source sites to check, or all")
@click.option("--privacy", default=True, help="hides the tag output from the cli")
@click.option("--timeout", default=8, type=int, help="Timeout in seconds for source site requests")
@click.option("--report", default=None, help="Optional report file to write")
@click.option("--doublecheck-file-system", default=False, help="Ask Hydrus to recheck URL matches on disk")
def search_all(lookupfile, token, host, tag_service, namespace, sites, privacy, timeout, report, doublecheck_file_system):
    run_source_lookup_command(
        "all",
        lookupfile,
        token,
        host,
        tag_service,
        parse_namespace_config(namespace),
        sites,
        privacy,
        timeout,
        report,
        doublecheck_file_system,
    )


@click.command(name="search-year")
@click.argument("lookupfile")
@click.option("--token", help="The API token for your Hydrus server")
@click.option("--host", default="http://127.0.0.1:45869", help="The URL for your Hydrus server ")
@click.option("--tag-service", default="A.I. Tags", help="The Hydrus tag service to add tags to")
@click.option("--namespace", default="year", help="Namespace for found year tags")
@click.option("--sites", default="all", help="Comma-separated source sites to check, or all")
@click.option("--privacy", default=True, help="hides the tag output from the cli")
@click.option("--timeout", default=8, type=int, help="Timeout in seconds for source site requests")
@click.option("--report", default=None, help="Optional report file to write")
@click.option("--doublecheck-file-system", default=False, help="Ask Hydrus to recheck URL matches on disk")
def search_year(lookupfile, token, host, tag_service, namespace, sites, privacy, timeout, report, doublecheck_file_system):
    run_source_lookup_command(
        "year",
        lookupfile,
        token,
        host,
        tag_service,
        namespace,
        sites,
        privacy,
        timeout,
        report,
        doublecheck_file_system,
    )


cli.add_command(check_api)
cli.add_command(evaluate)
cli.add_command(evaluate_api)
cli.add_command(evaluate_api_batch)
cli.add_command(search_artist)
cli.add_command(search_all)
cli.add_command(search_year)


if __name__ == "__main__":
    Image.init()
    cli()
