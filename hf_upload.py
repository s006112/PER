import base64
import logging
import os
import socket
import ssl
from typing import Optional
from datetime import datetime, timezone


def _get_logger() -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger("ftps_upload")


def _png_bytes() -> bytes:
    # 1x1 transparent PNG
    b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    return base64.b64decode(b64)


def _fallback_upload_bytes(
    data: bytes,
    *,
    remote_name: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    import ftplib
    from ftplib import FTP_TLS

    class ReuseFTPS(FTP_TLS):
        def ntransfercmd(self, cmd, rest=None):  # type: ignore[override]
            conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
            if self._prot_p:
                try:
                    session = self.sock.session if isinstance(self.sock, ssl.SSLSocket) else None
                except Exception:
                    session = None
                try:
                    conn = self.context.wrap_socket(conn, server_hostname=self.host, session=session)
                except TypeError:
                    conn = self.context.wrap_socket(conn, server_hostname=self.host)
            return conn, size

    if logger is None:
        logger = _get_logger()

    host = os.getenv("FTP_HOST", "ftp.baltech-industry.com")
    port = int(os.getenv("FTP_PORT", "21"))
    username = os.getenv("FTP_USER")
    password = os.getenv("FTP_PASS")
    if remote_name is None:
        remote_name = os.getenv("FTP_REMOTE_FILENAME", "upload.png")

    attempts = [
        {"passive": True, "use_epsv": True},
        {"passive": True, "use_epsv": False},
        {"passive": False, "use_epsv": False},
    ]

    last_err: Optional[Exception] = None
    for idx, opts in enumerate(attempts, start=1):
        passive = opts["passive"]
        use_epsv = opts["use_epsv"]
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            ftps = ReuseFTPS(context=context)
            ftps.set_debuglevel(1)
            ftps.af = socket.AF_INET6 if use_epsv else socket.AF_INET
            ftps.set_pasv(passive)
            ftps.connect(host=host, port=port, timeout=30)
            ftps.login(user=username, passwd=password)
            ftps.prot_p()
            from io import BytesIO

            cmd = f"STOR /public_html/PER/CIE/{remote_name}"
            ftps.storbinary(cmd, BytesIO(data))
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

    raise last_err if last_err else RuntimeError("FTPS upload failed without specific error")


def _timestamped_remote_name(base: Optional[str]) -> str:
    base_name = base or os.getenv("FTP_REMOTE_FILENAME", "upload.png")
    # Preserve optional subdirectory components in the provided base name
    if "/" in base_name:
        dir_part, file_part = base_name.rsplit("/", 1)
    else:
        dir_part, file_part = "", base_name

    if "." in file_part:
        stem, ext = file_part.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = file_part, ".png"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stamped = f"{stem}_{ts}{ext}"
    return f"{dir_part}/{stamped}" if dir_part else stamped


def main() -> None:
    logger = _get_logger()
    png = _png_bytes()

    try:
        import ftp as ftp_module  # type: ignore
    except Exception:
        ftp_module = None

    # Build timestamped remote filename using the configured base name
    base = os.getenv("FTP_REMOTE_FILENAME")
    remote_name = _timestamped_remote_name(base)

    if ftp_module and hasattr(ftp_module, "upload_file"):
        ftp_module.upload_file(data=png, remote_name=remote_name, logger=logger)
        logger.info("Upload completed via ftp.upload_file")
    else:
        _fallback_upload_bytes(png, remote_name=remote_name, logger=logger)
        logger.info("Upload completed via internal FTPS fallback")


if __name__ == "__main__":
    main()
