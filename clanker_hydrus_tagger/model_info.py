import json
from pathlib import Path

from . import onnx_loader

BOOTSTRAP_MODEL_REPOS = {
    "JTP-3": {
        "repo_id": "0xk1ru/jtp-3-onnx",
        "repo_revision": "main",
    },
    "Z3D-E621-Convnext": {
        "repo_id": "toynya/Z3D-E621-Convnext",
        "repo_revision": "main",
    },
    "wd-eva02-large-tagger-v3": {
        "repo_id": "SmilingWolf/wd-eva02-large-tagger-v3",
        "repo_revision": "main",
    },
    "camie-tagger": {
        "repo_id": "0xk1ru/camie-tagger-onnx",
        "repo_revision": "main",
    },
}


def _model_dir(model):
    return Path("model") / model


def _info_path(model):
    return _model_dir(model) / "info.json"


def _is_valid_repo_id(repo_id):
    return isinstance(repo_id, str) and "/" in repo_id


def _apply_bootstrap_defaults(model, info):
    bootstrap_config = BOOTSTRAP_MODEL_REPOS.get(model)
    if bootstrap_config is None:
        return info

    normalized = dict(info)
    if not _is_valid_repo_id(normalized.get("repo_id")):
        normalized["repo_id"] = bootstrap_config["repo_id"]

    return normalized


def ensure_model_info(model):
    info_path = _info_path(model)
    if info_path.is_file():
        return info_path

    bootstrap_config = BOOTSTRAP_MODEL_REPOS.get(model)
    if bootstrap_config is None:
        raise FileNotFoundError(
            f"info.json not found for model '{model}', and no bootstrap Hugging Face repo is configured for it."
        )

    onnx_loader.ensure_huggingface_files(
        _model_dir(model),
        bootstrap_config["repo_id"],
        ["info.json"],
        revision=bootstrap_config.get("repo_revision", "main"),
        model_name=model,
    )

    if not info_path.is_file():
        raise FileNotFoundError(f"info.json is still missing after download attempt for model '{model}'.")

    return info_path


def load_model_info(model):
    info_path = ensure_model_info(model)
    with info_path.open(encoding="utf-8") as json_f:
        info = json.load(json_f)
    return _apply_bootstrap_defaults(model, info)
