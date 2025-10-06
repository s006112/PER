from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Tuple
from urllib.parse import quote

import requests
from dotenv import load_dotenv

_BASE_URL = "https://nextcloud.ampco.com.hk"
_TARGET_REMOTE_DIR = "/Documents/PER/Photometry Report"


def load_env() -> Tuple[str, str]:
    """Load Nextcloud credentials from environment or .env."""
    load_dotenv()  # Ensures .env is read when running locally
    username = os.getenv("NEXTCLOUD_USERNAME")
    password = os.getenv("NEXTCLOUD_PASSWORD")
    pairs = [("NEXTCLOUD_USERNAME", username), ("NEXTCLOUD_PASSWORD", password)]
    missing = [name for name, value in pairs if not value]
    if missing:
        raise RuntimeError(f"Missing required Nextcloud env vars: {', '.join(missing)}")
    assert username is not None and password is not None
    return username, password


def _encode_path_segments(segments: Iterable[str]) -> str:
    return "/".join(quote(seg, safe="") for seg in segments if seg)


def mkcol_recursive(base_url: str, username: str, auth: Tuple[str, str], folders: Iterable[str]) -> None:
    """Ensure each folder in the list exists by issuing MKCOL requests."""
    encoded_username = quote(username, safe="")
    base_url = base_url.rstrip("/")
    root = f"{base_url}/remote.php/dav/files/{encoded_username}"

    session = requests.Session()
    session.auth = auth

    current = root
    for folder in folders:
        if not folder:
            continue
        current = f"{current}/{quote(folder, safe='')}"
        resp = session.request("MKCOL", current, timeout=30)
        if resp.status_code in (200, 201):
            continue
        if resp.status_code == 405:  # Already exists
            continue
        if resp.status_code == 409:
            raise RuntimeError(f"Cannot create folder '{folder}': parent does not exist (409)")
        if resp.status_code == 401:
            raise RuntimeError("Nextcloud authentication failed while creating folders (401)")
        if resp.status_code >= 400:
            detail = (resp.text or "").strip()
            snippet = f": {detail[:200]}" if detail else ""
            raise RuntimeError(f"Failed to create folder '{folder}' ({resp.status_code}){snippet}")


def upload_file(local_path: str, remote_dir: str) -> str:
    """Upload a file to Nextcloud and return the remote path."""
    local_file = Path(local_path).expanduser()
    if not local_file.is_file():
        raise FileNotFoundError(f"Local file not found: {local_file}")

    username, password = load_env()
    auth = (username, password)

    base_url = _BASE_URL.rstrip("/")
    encoded_username = quote(username, safe="")
    remote_root = f"{base_url}/remote.php/dav/files/{encoded_username}"

    remote_segments = [segment for segment in remote_dir.strip("/").split("/") if segment]
    encoded_dir = _encode_path_segments(remote_segments)
    if encoded_dir:
        remote_root = f"{remote_root}/{encoded_dir}"

    remote_url = f"{remote_root}/{quote(local_file.name, safe='')}"

    with local_file.open("rb") as handle:
        resp = requests.put(remote_url, data=handle, auth=auth, timeout=60)
    if resp.status_code not in (200, 201, 204):
        detail = (resp.text or "").strip()
        snippet = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Failed to upload '{local_file.name}' ({resp.status_code}){snippet}")

    remote_path = "/" + "/".join(remote_segments + [local_file.name])
    return remote_path


def upload_to_nextcloud(local_path: str) -> str | None:
    """High-level upload helper that ensures directories exist and handles errors."""
    try:
        username, password = load_env()
        auth = (username, password)
        folders = [segment for segment in _TARGET_REMOTE_DIR.strip("/").split("/") if segment]
        mkcol_recursive(_BASE_URL, username, auth, folders)
        remote_path = upload_file(local_path, _TARGET_REMOTE_DIR)
        print(f"Uploaded '{Path(local_path).name}' to Nextcloud at {remote_path}")
        return remote_path
    except Exception as exc:  # noqa: BLE001 - report any issue up the stack
        print(f"Nextcloud upload failed: {exc}")
        return None
