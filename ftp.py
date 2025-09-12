import logging
import os
import socket
import ssl
import ftplib
from ftplib import FTP_TLS
import io
import urllib.request
import urllib.error
import mimetypes
import random
import string


LOGGER_NAME = "ftps_upload"


class ReuseFTPS(FTP_TLS):
    """FTP_TLS variant that reuses the TLS session for data connections.

    Some FTPS servers (e.g., ProFTPD with TLSOptions RequireSessionReuse) require
    that the TLS session used for the control connection is reused for the data
    connection. ftplib doesn't pass the session by default; this class adds it.
    """

    def ntransfercmd(self, cmd, rest=None):  # type: ignore[override]
        # Get a plain data socket using base FTP behavior (no implicit TLS wrap)
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                session = self.sock.session if isinstance(self.sock, ssl.SSLSocket) else None
            except Exception:
                session = None
            # Wrap once with TLS, reusing control session if supported
            try:
                conn = self.context.wrap_socket(conn, server_hostname=self.host, session=session)
            except TypeError:
                conn = self.context.wrap_socket(conn, server_hostname=self.host)
        return conn, size


def _get_logger() -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(LOGGER_NAME)


def _resolve_ipv4(host: str, port: int) -> str:
    """Resolve to an IPv4 address to avoid IPv6 stalls on some PaaS."""
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return host


def _http_upload(remote_name: str, content: bytes, *, url: str, token: str | None, timeout: int) -> None:
    """Minimal dependency-free HTTPS multipart upload.

    Sends fields: remote_name (text) and file (binary) as multipart/form-data.
    Optional Authorization: Bearer <token> header if provided.
    """
    boundary = "----WebKitFormBoundary" + "".join(random.choices(string.ascii_letters + string.digits, k=16))
    lines: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        lines.extend([
            f"--{boundary}\r\n".encode(),
            f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(),
            value.encode(), b"\r\n",
        ])

    def add_file(name: str, filename: str, data: bytes, content_type: str | None = None) -> None:
        ctype = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        lines.extend([
            f"--{boundary}\r\n".encode(),
            f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n".encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            data, b"\r\n",
        ])

    add_field("remote_name", remote_name)
    add_file("file", remote_name, content, "image/png")
    lines.append(f"--{boundary}--\r\n".encode())
    body = b"".join(lines)

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, 'status', 200)
        if status < 200 or status >= 300:
            raise urllib.error.HTTPError(url, status, "Upload failed", resp.headers, None)


def upload_file(
    *,
    local_path: str | None = None,
    remote_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    data: bytes | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Upload a file to the configured FTPS server.

    Configuration and defaults mirror the previous CLI behavior exactly:
    - `FTP_HOST` (default: "ftp.baltech-industry.com")
    - `FTP_PORT` (default: 21)
    - `FTP_USER`, `FTP_PASS`
    - `FTP_LOCAL_PATH` (default: "dummy.png")
    - `FTP_REMOTE_FILENAME` (default: basename of local path)

    The remote destination path remains: /public_html/PER/CIE/{remote_name}
    """
    if logger is None:
        logger = _get_logger()

    host = host or os.getenv("FTP_HOST", "ftp.baltech-industry.com")
    port = int(port if port is not None else os.getenv("FTP_PORT", "21"))
    username = username or os.getenv("FTP_USER")
    password = password or os.getenv("FTP_PASS")
    local_path = local_path or os.getenv("FTP_LOCAL_PATH", "dummy.png")
    # If uploading from in-memory data, caller should pass remote_name explicitly or rely on env
    if remote_name is None:
        if data is None:
            remote_name = os.getenv("FTP_REMOTE_FILENAME", os.path.basename(local_path))
        else:
            remote_name = os.getenv("FTP_REMOTE_FILENAME", "upload.png")

    # If HTTPS fallback URL is provided, prefer it on Spaces (or always if set)
    http_url = os.getenv("FTP_HTTP_UPLOAD_URL")
    http_token = os.getenv("FTP_HTTP_UPLOAD_TOKEN")
    http_timeout = int(os.getenv("FTP_CONNECT_TIMEOUT", "8" if os.getenv("SPACE_ID") else "30"))

    content_bytes: bytes | None = None
    if http_url:
        try:
            if data is not None:
                content_bytes = data
            else:
                with open(local_path, "rb") as f:
                    content_bytes = f.read()
            _http_upload(remote_name, content_bytes, url=http_url, token=http_token, timeout=http_timeout)
            return
        except Exception as e:
            logger.warning("HTTPS upload failed: %s; falling back to FTPS", e)

    # Reorder attempts for PaaS (IPv4 PASV first); reduce retries on Spaces
    in_spaces = bool(os.getenv("SPACE_ID"))
    attempts = [
        {"passive": True},  # PASV over IPv4
    ]
    if not in_spaces:
        attempts.append({"passive": False})  # Active only off-PaaS

    # Try configured port; extend with optional alternates and 990 on Spaces
    port_list = [port]
    alt = os.getenv("FTP_ALT_PORTS", "").strip()
    if alt:
        for p in alt.split(','):
            p = p.strip()
            if p.isdigit():
                iv = int(p)
                if iv not in port_list:
                    port_list.append(iv)
    if in_spaces and 990 not in port_list:
        port_list.append(990)

    last_err = None
    attempt_no = 0
    for port_try in port_list:
        for opts in attempts:
            attempt_no += 1
            passive = opts["passive"]
            try:
                # Shorter default timeout on Spaces
                timeout = int(os.getenv("FTP_CONNECT_TIMEOUT", "8" if in_spaces else "30"))

                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

                ftps = ReuseFTPS(context=context)
                ftps.set_debuglevel(1)

                # Force IPv4 for data connections
                ftps.af = socket.AF_INET

                ftps.set_pasv(passive)
                # Resolve control connection to IPv4 literal to avoid AAAA routes
                connect_host = _resolve_ipv4(host, port_try)
                ftps.connect(host=connect_host, port=port_try, timeout=timeout)
                ftps.login(user=username, passwd=password)
                ftps.prot_p()
                # Choose source: in-memory bytes or local file path
                if data is not None:
                    fobj = io.BytesIO(data)
                    cmd = f"STOR /public_html/PER/CIE/{remote_name}"
                    ftps.storbinary(cmd, fobj)
                else:
                    with open(local_path, "rb") as f:
                        cmd = f"STOR /public_html/PER/CIE/{remote_name}"
                        ftps.storbinary(cmd, f)
                try:
                    ftps.quit()
                finally:
                    try:
                        ftps.close()
                    except Exception:
                        pass
                return
            except Exception as e:
                last_err = e
                logger.warning(
                    "Attempt %d failed (port=%s, passive=%s): %s",
                    attempt_no, port_try, passive, e,
                )
                continue

    # If we got here, all attempts failed
    raise last_err if last_err else RuntimeError("FTPS upload failed without specific error")
