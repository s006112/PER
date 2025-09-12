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
    timeout = int(os.getenv("FTP_CONNECT_TIMEOUT", "30"))
    local_path = local_path or os.getenv("FTP_LOCAL_PATH", "dummy.png")
    # If uploading from in-memory data, caller should pass remote_name explicitly or rely on env
    if remote_name is None:
        if data is None:
            remote_name = os.getenv("FTP_REMOTE_FILENAME", os.path.basename(local_path))
        else:
            remote_name = os.getenv("FTP_REMOTE_FILENAME", "upload.png")

    logger.info(
        "Preflight: host=%s port=%s timeout=%ss data=%s remote_name=%s",
        host,
        port,
        timeout,
        "yes" if data is not None else "no",
        remote_name,
    )

    # Optionally log the Space's public egress IP to aid firewall allowlisting
    if os.getenv("FTP_LOG_PUBLIC_IP", "false").lower() in ("1", "true", "yes", "on"): 
        try:
            import urllib.request
            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
                ip = r.read().decode().strip()
            logger.info("Public egress IP detected: %s", ip or "<empty>")
        except Exception as e:
            logger.info("Public egress IP detection failed: %s", e)

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
        try:
            gai = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            addrs = []
            for ai in gai[:6]:
                fam = "AF_INET6" if ai[0] == socket.AF_INET6 else "AF_INET" if ai[0] == socket.AF_INET else str(ai[0])
                try:
                    ip = ai[4][0]
                except Exception:
                    ip = "<unknown>"
                addrs.append(f"{fam}:{ip}")
            logger.info("DNS resolved: %s", ", ".join(addrs) if addrs else "<none>")
        except Exception as _e:
            logger.info("DNS resolution failed: %s", _e)
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            ftps = ReuseFTPS(context=context)
            ftps.set_debuglevel(1)

            # Decide address family (decoupled from EPSV). Allow override by env.
            af_env = os.getenv("FTP_AF", "").lower()
            prefer_af: socket.AddressFamily
            if af_env in ("ipv4", "inet", "4"):
                prefer_af = socket.AF_INET
            elif af_env in ("ipv6", "inet6", "6"):
                prefer_af = socket.AF_INET6
            else:
                try:
                    families = {ai[0] for ai in gai}  # type: ignore[name-defined]
                except Exception:
                    families = set()
                if socket.AF_INET in families and socket.AF_INET6 not in families:
                    prefer_af = socket.AF_INET
                elif socket.AF_INET6 in families and socket.AF_INET not in families:
                    prefer_af = socket.AF_INET6
                else:
                    prefer_af = socket.AF_INET

            ftps.af = prefer_af
            logger.info("Address family selected: %s", "AF_INET6" if ftps.af == socket.AF_INET6 else "AF_INET")
            ftps.set_pasv(passive)
            logger.info("PASV set to: %s", passive)

            # Quick connectivity probe (non-fatal), 3s timeout
            try:
                fam = ftps.af
                s = socket.socket(fam, socket.SOCK_STREAM)
                s.settimeout(float(os.getenv("FTP_PROBE_TIMEOUT", "3")))
                logger.info("Connectivity probe to %s:%s (family=%s)", host, port, "AF_INET6" if fam == socket.AF_INET6 else "AF_INET")
                s.connect((host, port))
                try:
                    peer = s.getpeername()
                except Exception:
                    peer = None
                logger.info("Probe success: peer=%s", peer)
            except Exception as pe:
                logger.info("Probe failed: %s", pe)
            finally:
                try:
                    s.close()
                except Exception:
                    pass

            logger.info("Connecting to %s:%s with timeout=%s", host, port, timeout)
            ftps.connect(host=host, port=port, timeout=timeout)
            logger.info("Connected. Logging in as %s", "<provided>" if username else "<anonymous>")
            ftps.login(user=username, passwd=password)
            logger.info("Login successful. Upgrading to PROT P")
            ftps.prot_p()
            logger.info("PROT P acknowledged. Preparing STOR command")
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
