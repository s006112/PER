from __future__ import annotations

import os
import re
from typing import Tuple, List
import base64
import json
import logging
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr
from openai import OpenAI
from cie1931 import get_canvas_html, get_drawing_javascript, upload_saved_plot

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        "![](https://baltech-industry.com/PER/ampco.png)\n\n"
    )

    combined_summary = (
        f"{header_block}"
        "## Overall summary\n"
        f"{openai_summary_response}\n\n"
        "---\n"
        f"{openai_md_response}"
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

    return combined_summary, cct_xy, text


# ----------------------------
# CIE PNG upload bridge (from JS)
# ----------------------------
def handle_cie_png_upload(payload: str) -> str:
    """Receive a JSON payload {filename, data_url}, save PNG, then FTP upload.

    Minimal error handling; logs but does not raise.
    """
    if not payload:
        return ""
    try:
        data = json.loads(payload)
        fname = str(data.get("filename") or "").strip()
        data_url = str(data.get("data_url") or "")
        if not fname or not data_url.startswith("data:image/png;base64,"):
            return ""
        b64 = data_url.split("base64,", 1)[-1]
        raw = base64.b64decode(b64)
        out_path = Path(__file__).parent / fname
        out_path.write_bytes(raw)
        try:
            upload_saved_plot(str(out_path))
            return f"Uploaded {fname}"
        except Exception as e:
            logging.getLogger("ftps_upload").error("Upload trigger failed: %s", e)
            return f"Upload failed for {fname}"
    except Exception as e:
        logging.getLogger("ftps_upload").error("PNG receive failed: %s", e)
        return ""

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

    # Hidden (but present in DOM): original text and parsed CIE x,y table
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

    # Hide the two components via CSS so they remain in the DOM
    cie_png_upload_box = gr.Textbox(label="CIE PNG Upload", elem_id="cie_png_upload")
    cie_upload_status = gr.Markdown(value="", elem_id="cie_upload_status")

    gr.HTML(
        """
        <style>
          #cct_xy_df { display: none !important; }
          #original_text_box { display: none !important; }
          #cie_png_upload { display: none !important; }
        </style>
        """
    )

    # Wire outputs: summary (visible), dataframe + original text (hidden but functional)
    btn.click(handle_upload, inputs=inp, outputs=[combined_summary_box, cct_xy_box, original_text_box])
    cie_png_upload_box.change(handle_cie_png_upload, inputs=cie_png_upload_box, outputs=[cie_upload_status])

    # Load JS for CIE canvas drawing
    demo.load(fn=lambda: None, inputs=[], outputs=[], js=get_drawing_javascript())

if __name__ == "__main__":
    demo.launch()
