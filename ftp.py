import logging
import os
import sys
import socket
import ssl
import ftplib
from ftplib import FTP_TLS, all_errors


LOGGER_NAME = "ftps_upload"


def setup_logging() -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(LOGGER_NAME)

 


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


def _single_attempt(
    host: str,
    port: int,
    username: str,
    password: str,
    local_path: str,
    remote_filename: str,
    logger: logging.Logger,
    *,
    passive: bool,
    use_epsv: bool,
) -> None:
    mode = f"passive={'on' if passive else 'off'}, method={'EPSV' if use_epsv else 'PASV/PORT'}"
    logger.info("Preparing FTPS client (%s)", mode)

    # Context with session reuse to satisfy servers requiring it (e.g., ProFTPD)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False  # Optional; disable if server uses valid cert and hostname
    context.verify_mode = ssl.CERT_NONE  # Optional; set to CERT_REQUIRED with CA if desired

    ftps = ReuseFTPS(context=context)
    ftps.set_debuglevel(1)

    # Control EPSV/PASV behaviour: ftplib uses EPSV only if af != AF_INET
    if use_epsv:
        ftps.af = socket.AF_INET6  # trick ftplib into using EPSV on IPv4 servers too
    else:
        ftps.af = socket.AF_INET

    # Passive vs Active
    ftps.set_pasv(passive)

    logger.info("Connecting to host=%s port=%s", host, port)
    ftps.connect(host=host, port=port, timeout=30)

    logger.info("Logging in as user '%s'", username)
    ftps.login(user=username, passwd=password)

    logger.info("Enabling secure data connection (PROT P)")
    ftps.prot_p()

    try:
        cwd = ftps.pwd()
        logger.info("Remote working directory: %s", cwd)
    except Exception as e:
        logger.warning("Could not get remote working directory: %s", e)

    logger.info("Opening local file for upload: %s", local_path)
    with open(local_path, "rb") as f:
        cmd = f"STOR {remote_filename}"
        logger.info("Starting upload via '%s' (%s)", cmd, mode)
        ftps.storbinary(cmd, f)
    logger.info("Upload finished: %s -> %s", local_path, remote_filename)

    try:
        size = ftps.size(remote_filename)
        if size is not None:
            logger.info("Remote file size: %s bytes", size)
    except Exception as e:
        logger.warning("SIZE not supported or failed: %s", e)

    logger.info("Closing FTPS connection")
    try:
        ftps.quit()
    finally:
        try:
            ftps.close()
        except Exception:
            pass


def upload_via_ftps(
    host: str,
    port: int,
    username: str,
    password: str,
    local_path: str,
    remote_filename: str,
    logger: logging.Logger,
) -> None:
    attempts = [
        {"passive": True, "use_epsv": True},   # Try EPSV passive (often best behind NAT)
        {"passive": True, "use_epsv": False},  # Try PASV passive
        {"passive": False, "use_epsv": False}, # Try active (PORT)
    ]

    last_err = None
    for idx, opts in enumerate(attempts, start=1):
        try:
            logger.info("Attempt %d/%d with options: %s", idx, len(attempts), opts)
            _single_attempt(
                host=host,
                port=port,
                username=username,
                password=password,
                local_path=local_path,
                remote_filename=remote_filename,
                logger=logger,
                passive=opts["passive"],
                use_epsv=opts["use_epsv"],
            )
            logger.info("Upload succeeded on attempt %d", idx)
            return
        except all_errors as e:
            last_err = e
            logger.warning("Attempt %d failed: %s", idx, e)
            continue

    # If we got here, all attempts failed
    raise last_err if last_err else RuntimeError("FTPS upload failed without specific error")


def main() -> int:
    logger = setup_logging()

    logger.info("Reading environment variables")
    host = os.getenv("FTP_HOST", "ftp.baltech-industry.com")
    port_str = os.getenv("FTP_PORT", "21")  # 21 is the default FTPS (explicit) port
    user = os.getenv("FTP_USER")
    passwd = os.getenv("FTP_PASS")

    local_path = os.getenv("FTP_LOCAL_PATH", "dummy.png")
    remote_filename = os.getenv("FTP_REMOTE_FILENAME", os.path.basename(local_path))

    # Validate env vars
    missing = []
    if not user:
        missing.append("FTP_USER")
    if not passwd:
        missing.append("FTP_PASS")
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 2

    try:
        port = int(port_str)
    except ValueError:
        logger.error("Invalid FTP_PORT value: %r", port_str)
        return 2

    logger.info("Configured host=%s port=%s user=%s", host, port, user)

    # Verify local file exists
    if not os.path.exists(local_path):
        logger.error("Local file does not exist: %s", local_path)
        return 1
    else:
        logger.info("Local file exists: %s", local_path)

    # Perform upload
    try:
        upload_via_ftps(
            host=host,
            port=port,
            username=user,
            password=passwd,
            local_path=local_path,
            remote_filename=remote_filename,
            logger=logger,
        )
    except Exception:
        logger.error("Upload failed", exc_info=True)
        return 1

    logger.info("All done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
