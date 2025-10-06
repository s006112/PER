from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Tuple
from urllib.parse import quote

import requests
from dotenv import load_dotenv

_BASE_URL = "https://nextcloud.ampco.com.hk"
_TARGET_REMOTE_DIR = "/Documents/PER/Photometry Report"
_OCS_HEADERS = {
    "OCS-APIRequest": "true",
    "Accept": "application/json",
}


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


def _format_share_payload(share: Dict[str, object]) -> Dict[str, str]:
    url = share.get("url")
    share_id = share.get("id")
    if not url or not share_id:
        raise RuntimeError("Unexpected share payload from Nextcloud (missing url or id)")
    url_str = str(url)
    return {
        "page": url_str,
        "download": f"{url_str}/download",
        "id": str(share_id),
    }


def get_public_share_if_exists(base_url: str, auth: Tuple[str, str], remote_path: str) -> Dict[str, str] | None:
    base_url = base_url.rstrip("/")
    path_param = f"/{remote_path.lstrip('/')}"
    params = {"path": path_param, "format": "json"}
    url = f"{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    resp = requests.get(url, headers=_OCS_HEADERS, params=params, auth=auth, timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()
        snippet = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Failed to query share info ({resp.status_code}){snippet}")

    try:
        payload = resp.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("Nextcloud returned invalid JSON for share listing") from exc

    data = payload.get("ocs", {}).get("data")
    if not data:
        return None
    shares = data if isinstance(data, list) else [data]
    for share in shares:
        share_type = share.get("share_type")
        if str(share_type) == "3":
            return _format_share_payload(share)
    return None


def create_or_get_public_share(base_url: str, auth: Tuple[str, str], remote_path: str) -> Dict[str, str]:
    existing = get_public_share_if_exists(base_url, auth, remote_path)
    if existing:
        return existing

    base_url = base_url.rstrip("/")
    path_param = f"/{remote_path.lstrip('/')}"
    url = f"{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    data = {
        "shareType": "3",
        "path": path_param,
        "permissions": "1",
    }

    resp = requests.post(url, headers=_OCS_HEADERS, data=data, auth=auth, timeout=30)
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()
        snippet = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Failed to create public share ({resp.status_code}){snippet}")

    try:
        payload = resp.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("Nextcloud returned invalid JSON for share creation") from exc

    data = payload.get("ocs", {}).get("data")
    if not data:
        raise RuntimeError("Nextcloud did not return share details after creation")
    share = data if isinstance(data, dict) else data[0]
    return _format_share_payload(share)


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


def share(local_path: str) -> Dict[str, str]:
    """Upload a file and return its public share links."""
    username, password = load_env()
    auth = (username, password)

    remote_path = upload_to_nextcloud(local_path)
    if not remote_path:
        raise RuntimeError("Upload failed; cannot create share link")

    relative_path = remote_path.lstrip("/")
    share_payload = create_or_get_public_share(_BASE_URL, auth, relative_path)
    return {
        "remote_path": remote_path,
        **share_payload,
    }
