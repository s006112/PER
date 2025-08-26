#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal single-file Web UI for extracting PDF text from raw bytes.
Python 3.10.  Features:
- Upload a PDF -> Submit -> Display per-page text on the same page
- Store the original PDF into ./archived/
- Prefer PyMuPDF (fitz); fallback to pypdf when fitz is unavailable

Recommended (one of):
    pip install pymupdf flask
    # optional fallback:
    pip install pypdf

Run:
    python app.py
Open:
    http://127.0.0.1:5000/
"""

from __future__ import annotations
import datetime
import hashlib
import io
import os
from typing import Dict

from flask import Flask, request, render_template_string, flash
from werkzeug.utils import secure_filename

# ---------------------------
# Optional backends
# ---------------------------
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None  # type: ignore

try:
    from pypdf import PdfReader  # Fallback
except Exception:
    PdfReader = None  # type: ignore

# ---------------------------
# Config
# ---------------------------
ALLOWED_EXT = {".pdf"}
ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "archived")
MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20 MiB

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.secret_key = os.environ.get("APP_SECRET_KEY", "dev-key")  # for flash()

# ---------------------------
# HTML (inline template)
# ---------------------------
HTML = r"""
<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <title>PDF 原始内容提取（单文件）</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root { --fg:#222; --bg:#fff; --muted:#666; --line:#e5e7eb; --accent:#2563eb;}
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans CJK", "PingFang SC", "Microsoft YaHei", sans-serif;
           color:var(--fg); background:var(--bg); margin:0; }
    .wrap { max-width: 940px; margin: 32px auto 80px; padding: 0 16px; }
    h1 { font-size: 22px; margin: 0 0 12px; }
    p.note { color: var(--muted); margin-top: 4px; }
    form { margin: 20px 0; padding: 16px; border:1px solid var(--line); border-radius: 8px; background:#fafafa;}
    input[type=file] { display:block; margin: 8px 0 12px; }
    button { background: var(--accent); color:white; border:0; padding:8px 14px; border-radius:6px; cursor:pointer; }
    button:hover { filter:brightness(0.95); }
    .meta { margin:16px 0; font-size: 14px; color: #333; }
    .meta code { background:#f3f4f6; padding:2px 6px; border-radius:4px; }
    .result { margin-top: 16px; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    .result header { padding:10px 12px; background:#f8fafc; border-bottom:1px solid var(--line); font-weight:600; }
    .result pre { margin:0; padding:12px; white-space:pre-wrap; word-wrap:break-word; background:white; max-height:70vh; overflow:auto; }
    .flash { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; padding:8px 12px; border-radius:6px; }
    footer { margin-top:24px; font-size:12px; color:var(--muted); }
    .kv { display:inline-grid; grid-template-columns: auto auto; gap:8px 12px; background:#f9fafb; padding:10px; border-radius:8px; border:1px solid var(--line); }
    .kv span.k { color:#555; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>PDF 原始内容提取</h1>
    <p class="note">上传 PDF → 点击提交 → 在同一页面显示提取结果。文件将保存在 <code>./archived/</code>。</p>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="flash">
          {% for m in messages %}<div>{{ m }}</div>{% endfor %}
        </div>
      {% endif %}
    {% endwith %}

    <form method="post" enctype="multipart/form-data">
      <label for="file">选择 PDF 文件：</label>
      <input id="file" name="file" type="file" accept="application/pdf" required>
      <button type="submit">提交 / Submit</button>
      <div style="margin-top:8px; font-size:12px; color:#666;">最大 {{ max_mb }} MiB；若未安装 PyMuPDF 将回退到 pypdf。</div>
    </form>

    {% if meta and meta.filename %}
    <div class="meta">
      <div class="kv">
        <span class="k">文件名</span><span>{{ meta.filename }}</span>
        <span class="k">保存路径</span><span><code>{{ meta.saved_path }}</code></span>
        <span class="k">大小</span><span>{{ meta.size }}</span>
        <span class="k">解析引擎</span><span>{{ meta.engine }}</span>
        <span class="k">Python</span><span>{{ versions.python }}</span>
        <span class="k">Flask</span><span>{{ versions.flask }}</span>
        <span class="k">PyMuPDF</span><span>{{ versions.pymupdf }}</span>
        <span class="k">pypdf</span><span>{{ versions.pypdf }}</span>
      </div>
    </div>
    {% endif %}

    {% if result is not none %}
      <div class="result">
        <header>提取结果（未清洗的原始文本）</header>
        <pre>{{ result }}</pre>
      </div>
    {% endif %}

    <footer>© Single-file PDF extractor</footer>
  </div>
</body>
</html>
"""

# ---------------------------
# Helpers
# ---------------------------
def _allowed(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXT

def _archive_save(filename: str, data: bytes) -> str:
    """Save original upload into ./archived/ with timestamp + short hash."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    base = secure_filename(filename) or "upload.pdf"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    short = hashlib.sha256(data).hexdigest()[:8]
    path = os.path.join(ARCHIVE_DIR, f"{stamp}_{short}_{base}")
    with open(path, "wb") as f:
        f.write(data)
    return path

def _extract_pdf_pages_text(data: bytes) -> Dict[int, str]:
    """
    Extract text per page from raw PDF bytes.
    Priority: PyMuPDF (fitz) -> pypdf fallback.
    Returns: {page_number: text}
    """
    pages: Dict[int, str] = {}

    # Preferred: PyMuPDF
    if fitz is not None:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            for i, page in enumerate(doc, 1):
                # sort=True approximates natural reading order; keep as "raw text" (no sanitize)
                txt = page.get_text("text", sort=True)
                if txt and txt.strip():
                    pages[i] = txt
            doc.close()
            return pages
        except Exception as e:
            # fall through to pypdf
            pass

    # Fallback: pypdf
    if PdfReader is not None:
        reader = PdfReader(io.BytesIO(data))
        # Handle encrypted PDFs if possible
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # try empty password
            except Exception:
                pass
        for i, page in enumerate(reader.pages, 1):
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt.strip():
                pages[i] = txt
        return pages

    raise RuntimeError("Neither PyMuPDF (fitz) nor pypdf is available. Please install one of them.")

def _get_versions() -> Dict[str, str]:
    ver: Dict[str, str] = {}
    import sys
    ver["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    try:
        import flask as _fl; ver["flask"] = getattr(_fl, "__version__", "")
    except Exception:
        ver["flask"] = ""
    ver["pymupdf"] = getattr(fitz, "__version__", "not installed") if fitz is not None else "not installed"
    try:
        if PdfReader is not None:
            import pypdf as _pp
            ver["pypdf"] = getattr(_pp, "__version__", "installed")
        else:
            ver["pypdf"] = "not installed"
    except Exception:
        ver["pypdf"] = "installed"
    return ver

# ---------------------------
# Routes
# ---------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    result: str | None = None
    meta: Dict[str, str] = {}
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("请选择要上传的 PDF 文件。")
            return render_template_string(HTML, result=None, meta={}, versions=_get_versions(), max_mb=MAX_CONTENT_LENGTH // (1024*1024))

        if not _allowed(f.filename):
            flash("仅支持 PDF 文件。")
            return render_template_string(HTML, result=None, meta={}, versions=_get_versions(), max_mb=MAX_CONTENT_LENGTH // (1024*1024))

        data = f.read()
        if not data:
            flash("文件内容为空。")
            return render_template_string(HTML, result=None, meta={}, versions=_get_versions(), max_mb=MAX_CONTENT_LENGTH // (1024*1024))

        saved_path = _archive_save(f.filename, data)

        try:
            pages = _extract_pdf_pages_text(data)
            if not pages:
                result = "[该 PDF 无可提取文本或页面为空]"
            else:
                # Join pages with headers; keep raw text (no sanitization)
                chunks = [f"=== Page {i} ===\n{pages[i]}" for i in sorted(pages)]
                result = "\n\n".join(chunks)
        except Exception as e:
            result = f"[解析失败] {e}"

        meta = {
            "filename": f.filename,
            "saved_path": saved_path,
            "size": f"{len(data)} bytes",
            "engine": "PyMuPDF" if fitz is not None else ("pypdf" if PdfReader is not None else "None"),
        }

    return render_template_string(
        HTML,
        result=result,
        meta=meta,
        versions=_get_versions(),
        max_mb=MAX_CONTENT_LENGTH // (1024*1024),
    )

# ---------------------------
# Entrypoint
# ---------------------------
if __name__ == "__main__":
    # Create archive dir on startup to avoid race on first upload.
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True)
