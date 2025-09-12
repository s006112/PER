import base64
import logging
import os
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

    # Build timestamped remote filename using the configured base name
    base = os.getenv("FTP_REMOTE_FILENAME")
    remote_name = _timestamped_remote_name(base)

    import ftp as ftp_module  # type: ignore
    ftp_module.upload_file(data=png, remote_name=remote_name, logger=logger)
    logger.info("Upload completed via ftp.upload_file")


if __name__ == "__main__":
    main()
