from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Dict, Iterable, Tuple
from urllib.parse import quote

import requests
from dotenv import load_dotenv

_BASE_URL = "https://nextcloud.ampco.com.hk"
_PEF_REMOTE_DIR = "/Documents/PER/Photometry Report"
_PO_REMOTE_DIR = "/Documents/SO_Backup"
_OCS_HEADERS = {
    "OCS-APIRequest": "true",
    "Accept": "application/json",
}

log = logging.getLogger("nextcloud_upload")


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


def upload_file(
    local_path: str,
    remote_dir: str,
    base_url: str,
    username: str,
    auth: Tuple[str, str],
) -> str:
    """Upload a file to Nextcloud and return the remote path."""
    local_file = Path(local_path).expanduser()
    if not local_file.is_file():
        raise FileNotFoundError(f"Local file not found: {local_file}")

    base_url = base_url.rstrip("/")
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


def share(local_path: str) -> Dict[str, str]:
    """Upload a file and return its public share links."""
    return share_file(local_path, _PEF_REMOTE_DIR)


def share_po(local_path: str) -> Dict[str, str]:
    """Upload a file to the SO backup directory and return share data."""
    return share_file(local_path, _PO_REMOTE_DIR)


def share_file(local_path: str, remote_dir: str) -> Dict[str, str]:
    """Uploads a file to Nextcloud and returns share URLs."""
    username, password = load_env()
    auth = (username, password)

    folders = [segment for segment in remote_dir.strip("/").split("/") if segment]
    if folders:
        mkcol_recursive(_BASE_URL, username, auth, folders)

    remote_path = upload_file(local_path, remote_dir, _BASE_URL, username, auth)
    relative_path = remote_path.lstrip("/")

    try:
        share_payload = create_or_get_public_share(_BASE_URL, auth, relative_path)
    except Exception as exc:  # noqa: BLE001 - surface but continue
        log.warning("Unable to create share link for %s: %s", remote_path, exc)
        return {"remote_path": remote_path}

    return {
        "remote_path": remote_path,
        **share_payload,
    }


def ushare(local_path: str, remote_dir: str) -> Dict[str, str]:
    """Convenience wrapper to upload and share a file to an arbitrary directory."""
    return share_file(local_path, remote_dir)
