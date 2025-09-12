import logging
import os
import socket
import ssl
import ftplib
from ftplib import FTP_TLS
import io


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

    attempts = [
        {"passive": True, "use_epsv": True},   # Try EPSV passive (often best behind NAT)
        {"passive": True, "use_epsv": False},  # Try PASV passive
        {"passive": False, "use_epsv": False}, # Try active (PORT)
    ]

    last_err = None
    for idx, opts in enumerate(attempts, start=1):
        passive = opts["passive"]
        use_epsv = opts["use_epsv"]
        mode = f"passive={'on' if passive else 'off'}, method={'EPSV' if use_epsv else 'PASV/PORT'}"
        logger.info("Attempt %d starting: %s", idx, mode)
        logger.debug(
            "Config: host=%s port=%s user=%s local_path=%s remote_name=%s data=%s",
            host,
            port,
            bool(username),  # don't log actual username when DEBUG not needed
            local_path,
            remote_name,
            "yes" if data is not None else "no",
        )
        try:
            gai = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            addrs = [f"{ai[0].name}/{ai[4][0]}" for ai in gai[:5]]  # limit log size
            logger.debug("getaddrinfo: %s", ", ".join(addrs) or "<none>")
        except Exception as _e:
            logger.debug("getaddrinfo failed: %s", _e)
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            ftps = ReuseFTPS(context=context)
            ftps.set_debuglevel(1)

            if use_epsv:
                ftps.af = socket.AF_INET6
            else:
                ftps.af = socket.AF_INET

            logger.debug("Set address family: %s", "AF_INET6" if ftps.af == socket.AF_INET6 else "AF_INET")
            ftps.set_pasv(passive)
            logger.debug("PASV set to: %s", passive)
            logger.info("Connecting to %s:%s with timeout=%s", host, port, 30)
            ftps.connect(host=host, port=port, timeout=30)
            logger.info("Connected. Logging in as %s", "<provided>" if username else "<anonymous>")
            ftps.login(user=username, passwd=password)
            logger.debug("Login successful. Upgrading to PROT P")
            ftps.prot_p()
            logger.debug("PROT P acknowledged. Preparing STOR command")
            # Choose source: in-memory bytes or local file path
            if data is not None:
                fobj = io.BytesIO(data)
                cmd = f"STOR /public_html/PER/CIE/{remote_name}"
                logger.info("Uploading in-memory data to %s", cmd.split(" ", 1)[1])
                ftps.storbinary(cmd, fobj)
            else:
                with open(local_path, "rb") as f:
                    cmd = f"STOR /public_html/PER/CIE/{remote_name}"
                    logger.info("Uploading file '%s' to %s", local_path, cmd.split(" ", 1)[1])
                    ftps.storbinary(cmd, f)
            logger.info("STOR completed successfully")
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
            logger.warning("Attempt %d failed: %s", idx, e)
            continue

    # If we got here, all attempts failed
    raise last_err if last_err else RuntimeError("FTPS upload failed without specific error")
