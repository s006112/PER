#!/usr/bin/env python3
"""PDF text extraction utilities using PyMuPDF (fitz)."""

from __future__ import annotations
import inspect
import json
import logging
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

import fitz  # PyMuPDF：用於處理 PDF 文件的主要函式庫
import ocrmypdf  # OCR fallback for image-based PDFs
from chunk_san import sanitize_text  # 自訂的文字清洗函數，用來淨化提取出的 PDF 文字

logger = logging.getLogger(__name__)  # 初始化日誌記錄器

PDF_EXTS = {".pdf"}  # 支援的副檔名（目前僅限 PDF）

# -------------------------------------------------------------------------------------
# 輔助工具函數：從呼叫堆疊中自動推測 filename（若未在參數中顯式提供）
# -------------------------------------------------------------------------------------
def _infer_filename_from_stack() -> str | None:
    """從呼叫堆疊中尋找名為 'fn' 的變數，回傳其字串值（多用於自動日誌標記）。"""
    for frame in inspect.stack()[1:]:
        fn = frame.frame.f_locals.get("fn")
        if isinstance(fn, str):
            return fn
    return None

# -------------------------------------------------------------------------------------
# 核心 PDF 解析器（使用 PyMuPDF）
# -------------------------------------------------------------------------------------
def _extract_text_with_pymupdf(data: bytes) -> dict[int, str]:
    """使用 PyMuPDF 將 PDF 二進位資料轉換為 {頁碼: 淨化後文字} 的字典。"""

    def _run_extraction(pdf_bytes: bytes) -> dict[int, str]:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return {
                i: sanitize_text(t)
                for i, t in (
                    (n, p.get_text("text", sort=True).strip())
                    for n, p in enumerate(doc, 1)
                )
                if t
            }

    try:
        pages = _run_extraction(data)
        if pages:
            return pages
        # PyMuPDF returned no text; fall back to OCR once.
        logger.info("PyMuPDF extracted no text, attempting OCR fallback.")
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
    return _extract_text_with_ocr_fallback(data, _run_extraction)


def _extract_text_with_ocr_fallback(
    data: bytes,
    extractor: Callable[[bytes], dict[int, str]],
) -> dict[int, str]:
    """使用 OCR 產生可搜尋 PDF 後重新提取文字。"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir, "source.pdf")
            ocr_path = Path(tmpdir, "ocr.pdf")
            src_path.write_bytes(data)
            # Run OCR to produce a searchable PDF; force OCR to avoid Ghostscript regression with skip_text.
            ocrmypdf.ocr(
                str(src_path),
                str(ocr_path),
                output_type="pdf",
                force_ocr=True,
            )
            ocr_bytes = ocr_path.read_bytes()
        pages = extractor(ocr_bytes)
        if pages:
            logger.info("OCR fallback succeeded.")
            return pages
        logger.warning("OCR fallback produced no text.")
    except Exception as exc:
        logger.error("OCR fallback failed: %s", exc)
    return {}

# -------------------------------------------------------------------------------------
# 封裝提取流程：對外公開的頁面文字提取接口
# -------------------------------------------------------------------------------------
def extract_text_from_pdf_bytes(data: bytes, filename: str | None = None) -> dict[int, str]:
    """封裝 PyMuPDF 的文字擷取函式，並自動記錄頁數與檔名資訊。"""
    filename = filename or _infer_filename_from_stack()  # 若未提供 filename，則自動嘗試推測
    pages = _extract_text_with_pymupdf(data)  # 調用實際的 PDF 擷取器
    ctx = f" ({filename})" if filename else ""
    logger.info("Extraction complete: %d pages%s", len(pages), ctx)
    return pages  # 回傳每頁清洗後文字的字典

# -------------------------------------------------------------------------------------
# 高階共用函數：回傳合併後的完整 PDF 文字（不含分頁結構）
# 供 05_dvt.py 使用，用於將整份 PDF 匯出為 .txt 檔案
# -------------------------------------------------------------------------------------
def get_pdf_full_text(data: bytes, filename: str | None = None) -> str:
    """將所有頁面文字合併為單一段文字（用於全文分析或匯出）。"""
    pages = extract_text_from_pdf_bytes(data, filename)  # 提取每頁文字
    return "\n\n".join(
        text for _, text in sorted(pages.items())  # 依頁碼排序並合併
        if text.strip()
    )

# -------------------------------------------------------------------------------------
# 結構化 chunk 任務輸出（供 01_email.py 等模組使用）
# -------------------------------------------------------------------------------------
def extract_pdf_attachment_tasks(
    data: bytes,
    filename: str,
    base_meta: dict,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """
    根據輸入 PDF，產生分段（chunked）任務清單：
    每段固定長度文字搭配其 metadata，格式為 (chunk_text, metadata)。
    適用於 email 附件、語料切割等應用。
    """
    full_text = get_pdf_full_text(data, filename)  # 拿到整份 PDF 的合併文字

    chunks = [
        full_text[i:i + max_len]
        for i in range(0, len(full_text), max_len)
    ]  # 將全文以 max_len 字符為單位切割（固定長度，無語義分析）

    return [
        (
            chunk,
            {
                **base_meta,  # 合併外部提供的 metadata
                "part": "attachment",  # 標記此為附件來源
                "file_type": "pdf",
                "attachment": filename,
                "seq": i + 1,  # chunk 序號（從 1 開始）
            },
        )
        for i, chunk in enumerate(chunks)
        if chunk.strip()
    ]
