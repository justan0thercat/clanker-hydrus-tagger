import importlib
import sys
import subprocess
import os
import shlex
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

skip_install = False
index_url = os.environ.get('INDEX_URL', "")
python = sys.executable
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def is_installed(package):
    try:
        spec = importlib.util.find_spec(package)
    except ModuleNotFoundError:
        return False

    return spec is not None


def run_pip(args, desc=None):
    if skip_install:
        return

    command = [python, "-m", "pip", *_normalize_command(args), "--prefer-binary"]
    if index_url:
        command.extend(["--index-url", index_url])
    return run(command, desc=f"Installing {desc}", errdesc=f"Couldn't install {desc}")


def run(command, desc=None, errdesc=None, custom_env=None, live=False):
    if desc is not None:
        print(desc)

    normalized_command = _normalize_command(command)
    rendered_command = subprocess.list2cmdline(normalized_command)

    if live:
        result = subprocess.run(
            normalized_command,
            shell=False,
            env=os.environ if custom_env is None else custom_env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"""{errdesc or 'Error running command'}.
Command: {rendered_command}
Error code: {result.returncode}""")

        return ""

    result = subprocess.run(
        normalized_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=os.environ if custom_env is None else custom_env,
    )

    if result.returncode != 0:

        message = f"""{errdesc or 'Error running command'}.
Command: {rendered_command}
Error code: {result.returncode}
stdout: {result.stdout.decode(encoding="utf8", errors="ignore") if len(result.stdout)>0 else '<empty>'}
stderr: {result.stderr.decode(encoding="utf8", errors="ignore") if len(result.stderr)>0 else '<empty>'}
"""
        raise RuntimeError(message)

    return result.stdout.decode(encoding="utf8", errors="ignore")


def _normalize_command(command):
    if isinstance(command, str):
        return shlex.split(command, posix=False)
    return [str(part) for part in command]


def _get_hf_token():
    for env_name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        token = os.environ.get(env_name)
        if token:
            return token

    try:
        result = subprocess.run(
            ["hf", "auth", "token"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return None

    if result.returncode == 0:
        token = result.stdout.strip()
        if token.startswith("hf_"):
            return token

    return None


def _build_hf_resolve_url(repo_id, filename, revision="main"):
    quoted_revision = quote(revision, safe="")
    quoted_filename = quote(filename.replace("\\", "/"), safe="/")
    return f"https://huggingface.co/{repo_id}/resolve/{quoted_revision}/{quoted_filename}?download=true"


def _download_file(url, destination, token=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_suffix(destination.suffix + ".part")

    headers = {"User-Agent": "clanker-hydrus-tagger/0.0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)

    try:
        with urlopen(request) as response, open(temp_destination, "wb") as output_file:
            total_bytes = response.headers.get("Content-Length")
            total_bytes = int(total_bytes) if total_bytes else None
            downloaded = 0
            next_progress_mark = 0.1

            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break

                output_file.write(chunk)
                downloaded += len(chunk)

                if total_bytes:
                    progress = downloaded / total_bytes
                    if progress >= next_progress_mark or downloaded == total_bytes:
                        print(f"  {destination.name}: {progress * 100:.0f}%")
                        next_progress_mark += 0.1
                elif downloaded and downloaded % (100 * DOWNLOAD_CHUNK_SIZE) == 0:
                    print(f"  {destination.name}: {downloaded / (1024 * 1024):.0f} MB downloaded")
    except HTTPError as exc:
        if temp_destination.exists():
            temp_destination.unlink()
        raise RuntimeError(
            f"Couldn't download {destination.name} from Hugging Face ({exc.code} {exc.reason}). "
            "If the repo is private or gated, set HF_TOKEN first."
        ) from exc
    except URLError as exc:
        if temp_destination.exists():
            temp_destination.unlink()
        raise RuntimeError(f"Couldn't reach Hugging Face while downloading {destination.name}: {exc.reason}") from exc
    except Exception:
        if temp_destination.exists():
            temp_destination.unlink()
        raise

    os.replace(temp_destination, destination)


def ensure_huggingface_files(model_dir, repo_id, filenames, revision="main", model_name=None):
    model_dir = Path(model_dir)
    missing_files = [filename for filename in filenames if not (model_dir / filename).exists()]

    if not missing_files:
        return

    if not repo_id or "/" not in repo_id:
        missing_list = ", ".join(missing_files)
        raise FileNotFoundError(
            f"Missing required model files ({missing_list}) in {model_dir}, and no valid Hugging Face repo_id is configured."
        )

    label = model_name or model_dir.name
    print(f"Missing files for {label}. Downloading from Hugging Face repo {repo_id}...")
    token = _get_hf_token()

    for filename in missing_files:
        destination = model_dir / filename
        url = _build_hf_resolve_url(repo_id, filename, revision=revision)
        print(f"Downloading {filename}...")
        _download_file(url, destination, token=token)

    print(f"Finished downloading missing files for {label}.")
