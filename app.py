from __future__ import annotations

import os
import re
import json
import base64
import io
import logging
from datetime import datetime
from typing import Tuple, List
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr
from openai import OpenAI
from cie1931 import get_canvas_html, get_drawing_javascript

load_dotenv()

# Basic logging setup (tune via LOG_LEVEL env; default INFO)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
log = logging.getLogger("app")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# PNG upload target (must be provided via env/.env/Space Secrets)
PNG_UPLOAD_URL = os.getenv("PNG_UPLOAD_URL")
PNG_UPLOAD_FIELD = os.getenv("PNG_UPLOAD_FIELD")

# ----------------------------
# PDF parsing
# ----------------------------
def extract_pdf_text_from_bytes(data: bytes) -> Tuple[str, str]:
    """
    Parse PDF bytes into plain text using PyMuPDF when available.
    Returns: (engine_name, text)
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join([page.get_text("text") for page in doc])
        return "PyMuPDF", text
    except Exception as e:
        # Keep behavior minimal: signal parsing failure by empty text
        return f"Error: {e}", ""

# ----------------------------
# OpenAI helper
# ----------------------------
def query_openai_with_prompt(prompt_content: str, text: str) -> str:
    try:
        if "{context}" in prompt_content:
            final_prompt = prompt_content.replace("{context}", text)
        else:
            final_prompt = f"{prompt_content}\n\n{text}"

        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": final_prompt}],
            temperature=0.0,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"Error querying OpenAI: {e}"

# ----------------------------
# Upload handler
# ----------------------------

def handle_upload(file_path: str) -> Tuple[str, List[List[float | str]], str]:
    """
    Gradio callback
    - Read PDF path, extract text
    - Query OpenAI twice (details + summary)
    - Parse CIE 1931 (x,y) from the details markdown table

    Returns:
      combined_summary (str), cct_xy ([[label, x, y], ...]), original_text (str)
    """

    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", [], ""

    with open(file_path, "rb") as f:
        data = f.read()

    _engine, text = extract_pdf_text_from_bytes(data)
    if not text:
        return "Error: PDF parsing failed.", [], ""

    base_dir = Path(__file__).parent
    try:
        prompt_md_str = (base_dir / "Prompt_md.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_md.txt: {e}", [], ""
    try:
        prompt_summary_str = (base_dir / "Prompt_summary.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_summary.txt: {e}", [], ""

    openai_md_response = query_openai_with_prompt(prompt_md_str, text)
    openai_summary_response = query_openai_with_prompt(prompt_summary_str, openai_md_response)

    # Standard header for combined summary
    header_block = (
        "## XXXXXX photometry result summary and analysis\n"
        "![](https://baltech-industry.com/PER/ampco.png)\n"
    )

    # Generate PNG filename timestamp to align with frontend-rendered PNG
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    png_filename = f"CIE_{ts}.png"
    footer_block = (
        "### ANSI C78.377-2015 chromaticity quadrangles on CIE 1931 (x,y)\n"
        f"![](https://baltech-industry.com/PER/CIE/{png_filename})\n\n"
    )

    combined_summary = (
        f"{header_block}"
        "## Overall summary\n"
        f"{openai_summary_response}\n\n"
        "---\n"
        f"{openai_md_response}\n\n"
        f"{footer_block}"
    )

    # Extract CIE 1931 (x,y) rows from the "### Spectral Parameters" table
    # - Find the section by heading text
    # - Locate the table header containing "CIE 1931"
    # - Interpret the first column as a label, but prefer a header named "Product Number" if present
    # - Only include rows with numeric x,y values
    def _extract_cct_xy(md: str):
        try:
            lines = md.splitlines()

            # Locate the "### Spectral Parameters" section
            start = None
            for i, ln in enumerate(lines):
                s = ln.strip()
                if s.startswith("###") and "Product category" in s:
                    start = i
                    break
            if start is None:
                return []

            # End at next "### " or EOF
            end = len(lines)
            for j in range(start + 1, len(lines)):
                ss = lines[j].strip()
                if ss.startswith("### "):
                    end = j
                    break
            section = lines[start:end]

            # Collect markdown table lines
            table_lines = [ln for ln in section if "|" in ln]
            if not table_lines:
                return []

            rows = []
            for ln in table_lines:
                s = ln.strip()
                cells = [c.strip() for c in s.strip("|").split("|")]
                # Skip separator rows like | --- | --- |
                if all(re.fullmatch(r"-{3,}", c or "") for c in cells):
                    continue
                rows.append(cells)
            if not rows:
                return []

            # Find header row (must contain "CIE 1931")
            header_idx = None
            for idx, r in enumerate(rows):
                if any("CIE 1931" in c for c in r):
                    header_idx = idx
                    break
            if header_idx is None:
                return []

            header = rows[header_idx]

            # Determine column indices
            try:
                xy_col = next(i for i, c in enumerate(header) if "CIE 1931" in c)
            except StopIteration:
                return []

            # Prefer a "Product Number" header as label; otherwise use column 0
            param_col = 0
            for i, c in enumerate(header):
                if "Product Number" in c:
                    param_col = i
                    break

            # Only keep rows with enough cells
            required_cols = max(param_col, xy_col) + 1
            data_rows = [r for r in rows[header_idx + 1:] if len(r) >= required_cols]
            if not data_rows:
                return []

            out = []
            for r in data_rows:
                xy_text = r[xy_col]
                # Match "0.3191, 0.2190" with optional spaces
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*,\s*([0-9]+(?:\.[0-9]+)?)", xy_text)
                if not m:
                    continue
                try:
                    x = float(m.group(1))
                    y = float(m.group(2))
                except Exception:
                    continue

                # Label from chosen column, fallback to row index
                param = r[param_col] if len(r) > param_col and r[param_col] else f"行{len(out)+1}"
                out.append([param, x, y])

            return out
        except Exception:
            return []

    cct_xy = _extract_cct_xy(openai_md_response)

    return combined_summary, cct_xy, text, png_filename

# ----------------------------
# CIE PNG upload bridge (DataURL -> remote upload)
# ----------------------------
def _post_png_bytes(filename: str, data: bytes):
    """Upload PNG bytes to remote host per upload_test.py algorithm.
    Returns (status_code, body_head) on success, or raises on error.
    """
    import requests
    files = {PNG_UPLOAD_FIELD: (filename, io.BytesIO(data), "image/png")}
    resp = requests.post(PNG_UPLOAD_URL, files=files, timeout=30)
    return resp.status_code, (resp.text or "")[:500]


def upload_cie_png(payload: str) -> None:
    """Gradio change-callback: receive JSON {filename, data_url} and upload PNG.
    No UI output is produced; uses backend logging only.
    """
    try:
        if not payload:
            log.warning("No payload received from frontend.")
            return None

        # Ensure remote upload is configured via environment (no hardcoded defaults)
        if not PNG_UPLOAD_URL or not PNG_UPLOAD_FIELD:
            missing = []
            if not PNG_UPLOAD_URL:
                missing.append("PNG_UPLOAD_URL")
            if not PNG_UPLOAD_FIELD:
                missing.append("PNG_UPLOAD_FIELD")
            log.warning("Upload disabled; missing env: %s", ", ".join(missing))
            return None

        log.debug("CIE upload payload length=%d", len(payload))

        try:
            obj = json.loads(payload)
        except Exception as e:
            log.error("JSON parse error: %s", e)
            return None

        fname = (obj.get("filename") or "cie.png").strip() or "cie.png"
        data_url = obj.get("data_url") or ""
        if not data_url.startswith("data:image/png;base64,"):
            log.error("Invalid data URL prefix (expect data:image/png;base64,).")
            return None

        b64 = data_url.split(",", 1)[1]
        try:
            png_bytes = base64.b64decode(b64)
        except Exception as e:
            log.error("Base64 decode failed: %s", e)
            return None

        log.info("Decoded PNG bytes: %d", len(png_bytes))

        try:
            log.info("POSTing PNG to %s as field '%s' filename '%s'", PNG_UPLOAD_URL, PNG_UPLOAD_FIELD, fname)
            code, body = _post_png_bytes(fname, png_bytes)
            log.info("Upload response: %s", code)
            if code >= 400:
                snippet = (body or "").replace("\n", " ")[:160]
                log.error("Upload error: %s %s", code, snippet)
            return None
        except Exception as e:
            log.exception("Upload exception: %s: %s", e.__class__.__name__, e)
            return None
    except Exception as e:
        log.exception("Unexpected error: %s", e)
        return None

# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Photometric extraction") as demo:
    # Minimal visible controls: Upload, Submit, Summary, CIE canvas
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
    btn = gr.Button("Submit")

    combined_summary_box = gr.Textbox(label="Summary", lines=14, show_copy_button=True)

    # CIE chart
    gr.HTML(get_canvas_html(), elem_id="cie_box")

    # Hidden (but present in DOM): original text, parsed CIE x,y table, and CIE PNG upload bridge
    # Keep DOM so the JS can read values for plotting.
    original_text_box = gr.Textbox(
        label="Sphere PDF extraction",
        lines=10,
        show_copy_button=True,
        elem_id="original_text_box",
    )

    cct_xy_box = gr.Dataframe(
        label="CIE x,y (parsed from Spectral Parameters)",
        headers=["参数", "x", "y"],
        interactive=False,
        elem_id="cct_xy_df",
    )

    # Hidden textbox used by JS to signal a PNG data URL to backend for upload
    cie_png_upload_box = gr.Textbox(
        label="CIE PNG upload payload",
        lines=1,
        elem_id="cie_png_upload",
    )

    # Hidden textbox to pass the planned PNG filename from backend to frontend
    cie_png_name_box = gr.Textbox(
        label="CIE PNG filename",
        lines=1,
        elem_id="cie_png_name",
    )

    gr.HTML(
        """
        <style>
          #cct_xy_df { display: none !important; }
          #original_text_box { display: none !important; }
          #cie_png_upload { display: none !important; }
          #cie_png_name { display: none !important; }
        </style>
        """
    )

    # Wire outputs: summary (visible), dataframe + original text (hidden), and PNG filename (hidden)
    btn.click(
        handle_upload,
        inputs=inp,
        outputs=[combined_summary_box, cct_xy_box, original_text_box, cie_png_name_box],
    )

    # Load JS for CIE canvas drawing
    demo.load(fn=lambda: None, inputs=[], outputs=[], js=get_drawing_javascript())

    # Wire hidden upload bridge: when JS writes JSON into the hidden textbox, upload PNG
    cie_png_upload_box.change(upload_cie_png, inputs=[cie_png_upload_box], outputs=[])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
