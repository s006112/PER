from __future__ import annotations

import os
import re
import json
import base64
import logging
import tempfile
from datetime import datetime
from typing import Tuple, List
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr
from openai import OpenAI
from cie1931 import get_canvas_html, get_drawing_javascript
from nextcloud_upload import share, ushare
from chunk_pdf import get_pdf_full_text
from clipboard_polyfill import CLIPBOARD_POLYFILL

load_dotenv()

# Basic logging setup (tune via LOG_LEVEL env; default INFO)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
log = logging.getLogger("app")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PNG_REMOTE_DIR = "/Documents/PER/CIE Chart"
PNG_PLACEHOLDER_PREFIX = "cie-share://"

LATEST_SUMMARY: str = ""
DEBUG_TEXTBOXES = os.getenv("DEBUG_TEXTBOXES", "false").strip().lower() == "true"

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


def insert_stats_rows(md: str) -> str:
    """Insert Min/Max/Average rows into the first Product category table."""

    if not md:
        return md

    lines = md.splitlines()
    trailing_newline = md.endswith("\n")

    # Locate the first "### Product category" heading.
    heading_idx = None
    for idx, line in enumerate(lines):
        if line.strip().lower().startswith("###") and "product category" in line.lower():
            heading_idx = idx
            break
    if heading_idx is None:
        return md

    # Find the first pipe table following the heading.
    table_start = heading_idx + 1
    while table_start < len(lines) and ("|" not in lines[table_start] or not lines[table_start].strip()):
        table_start += 1
    if table_start >= len(lines) or "|" not in lines[table_start]:
        return md

    table_end = table_start
    while table_end < len(lines) and "|" in lines[table_end]:
        table_end += 1

    table_lines = lines[table_start:table_end]
    if len(table_lines) < 2:
        return md

    header_line = table_lines[0]
    separator_line = table_lines[1] if len(table_lines) > 1 else ""

    def split_row(raw_line: str) -> List[str]:
        cells = [cell.strip() for cell in raw_line.strip().strip("|").split("|")]
        return cells

    header_cells = split_row(header_line)
    expected_headers = [
        "Product Model",
        "Product Number",
        "Remarks",
        "CCT (K)",
        "Luminous Flux (lm)",
        "Luminous Efficacy (lm/W)",
        "Power (W)",
        "Current (A)",
        "Power Factor",
        "Ra",
        "R9",
        "CIE 1931 (x, y)",
    ]

    if [cell.strip() for cell in header_cells] != expected_headers:
        return md

    def normalize_cells(cells: List[str]) -> List[str]:
        if len(cells) < len(header_cells):
            cells = cells + [""] * (len(header_cells) - len(cells))
        elif len(cells) > len(header_cells):
            cells = cells[: len(header_cells)]
        return cells

    data_lines = table_lines[2:]
    data_rows: List[List[str]] = []
    for raw in data_lines:
        cells = normalize_cells(split_row(raw))
        if all(re.fullmatch(r"-+", c or "") for c in cells):
            continue
        data_rows.append(cells)

    if not data_rows:
        return md

    stats_labels = {"min", "max", "average"}
    filtered_rows: List[List[str]] = []
    for row in data_rows:
        label = row[0].strip().lower()
        if label in stats_labels:
            continue
        filtered_rows.append(row)

    product_number_idx = header_cells.index("Product Number")
    remarks_idx = header_cells.index("Remarks")

    for idx, row in enumerate(filtered_rows, start=1):
        row[product_number_idx] = f"#{idx}"

    numeric_specs = {
        "CCT (K)": {"idx": header_cells.index("CCT (K)"), "fmt": "int"},
        "Luminous Flux (lm)": {"idx": header_cells.index("Luminous Flux (lm)"), "fmt": "int"},
        "Luminous Efficacy (lm/W)": {"idx": header_cells.index("Luminous Efficacy (lm/W)"), "fmt": ".2f"},
        "Power (W)": {"idx": header_cells.index("Power (W)"), "fmt": ".2f"},
        "Current (A)": {"idx": header_cells.index("Current (A)"), "fmt": ".4f"},
        "Power Factor": {"idx": header_cells.index("Power Factor"), "fmt": "pf"},
        "Ra": {"idx": header_cells.index("Ra"), "fmt": ".1f"},
        "R9": {"idx": header_cells.index("R9"), "fmt": ".1f"},
    }

    cie_idx = header_cells.index("CIE 1931 (x, y)")

    numeric_values: dict[int, List[float]] = {spec["idx"]: [] for spec in numeric_specs.values()}
    cie_values: List[Tuple[float, float]] = []

    number_pattern = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
    cie_pattern = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)")

    def parse_numeric(cell: str) -> float | None:
        if not cell:
            return None
        cleaned = cell.replace("K", "").replace("k", "").strip()
        match = number_pattern.search(cleaned)
        if not match:
            return None
        try:
            return float(match.group().replace(",", ""))
        except ValueError:
            return None

    def parse_cie(cell: str) -> Tuple[float, float] | None:
        if not cell:
            return None
        match = cie_pattern.search(cell)
        if not match:
            return None
        try:
            return float(match.group(1)), float(match.group(2))
        except ValueError:
            return None

    for row in filtered_rows:
        for spec in numeric_specs.values():
            idx = spec["idx"]
            if idx >= len(row):
                continue
            parsed = parse_numeric(row[idx])
            if parsed is not None:
                numeric_values[idx].append(parsed)
        if cie_idx < len(row):
            cie_pair = parse_cie(row[cie_idx])
            if cie_pair is not None:
                cie_values.append(cie_pair)

    def format_stat(values: List[float], fmt: str, mode: str) -> str:
        if not values:
            return ""
        if mode == "min":
            result = min(values)
        elif mode == "max":
            result = max(values)
        else:
            result = sum(values) / len(values)
        if fmt == "int":
            return str(int(round(result)))
        if fmt == "pf":
            return f"{result:.4f}"
        if isinstance(fmt, str) and fmt.startswith(".") and fmt.endswith("f") and fmt[1:-1].isdigit():
            decimals = int(fmt[1:-1])
            return f"{result:.{decimals}f}"
        return f"{result:.2f}"

    def format_cie(values: List[Tuple[float, float]], mode: str) -> str:
        if not values:
            return ""
        xs = [pair[0] for pair in values]
        ys = [pair[1] for pair in values]
        if mode == "min":
            x_val = min(xs)
            y_val = min(ys)
        elif mode == "max":
            x_val = max(xs)
            y_val = max(ys)
        else:
            x_val = sum(xs) / len(xs)
            y_val = sum(ys) / len(ys)
        return f"{x_val:.4f}, {y_val:.4f}"

    def build_stat_row(label: str, mode: str) -> List[str]:
        row = ["" for _ in header_cells]
        row[0] = label
        row[product_number_idx] = ""
        row[remarks_idx] = ""
        for spec in numeric_specs.values():
            idx = spec["idx"]
            row[idx] = format_stat(numeric_values[idx], spec["fmt"], mode)
        row[cie_idx] = format_cie(cie_values, mode)
        return row

    stat_rows = [
        build_stat_row("Min", "min"),
        build_stat_row("Max", "max"),
        build_stat_row("Average", "avg"),
    ]

    def join_row(cells: List[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    rebuilt_lines = [header_line, separator_line]
    for row in filtered_rows:
        rebuilt_lines.append(join_row(row))
    for row in stat_rows:
        rebuilt_lines.append(join_row(row))

    updated_lines = lines[:table_start] + rebuilt_lines + lines[table_end:]
    result = "\n".join(updated_lines)
    if trailing_newline:
        return result + "\n"
    return result
# ----------------------------
# Upload handler
# ----------------------------

def handle_upload(file_path: str) -> Tuple[str, List[List[float | str]], str, str]:
    """
    Gradio callback
    - Read PDF path, extract text
    - Query OpenAI twice (details + summary)
    - Parse CIE 1931 (x,y) from the details markdown table

    Returns:
      combined_summary (str), cct_xy ([[label, x, y], ...]), original_text (str), png_filename (str)
    """

    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", [], ""

    with open(file_path, "rb") as f:
        data = f.read()

    try:
        text = get_pdf_full_text(data, filename=Path(file_path).name)
    except Exception as exc:
        log.error("PDF parsing failed: %s", exc)
        return "Error: PDF parsing failed.", [], ""

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
    updated_md = insert_stats_rows(openai_md_response)
    openai_summary_response = query_openai_with_prompt(prompt_summary_str, updated_md)

    # Standard header for combined summary
    now = datetime.now()
    report_date = now.strftime("%Y-%m-%d")
    header_block = (
        "## XXXXXX photometric summary and analysis\n"
        f"- Report generated on {report_date} \n"
        "<p align=\"right\">\n"
        "  <img src=\"https://nextcloud.ampco.com.hk/index.php/s/24JX6rAGgS5QKNE/preview\" alt=\"Company logo\"/>\n"
        "</p>\n"
    )

    # Generate PNG filename timestamp to align with frontend-rendered PNG
    ts = now.strftime("%Y-%m-%d_%H-%M-%S")
    png_filename = f"CIE_{ts}.png"

    share_info: dict[str, str] = {}
    try:
        share_info = share(file_path) or {}
        log.info("Nextcloud share available: %s", share_info.get("page"))
    except Exception as exc:  # noqa: BLE001 - logging for visibility
        log.error("Failed to create Nextcloud share link: %s", exc)

    log.info("Uploaded processed PDF to Nextcloud: %s", share_info.get("remote_path"))

    placeholder_url = f"{PNG_PLACEHOLDER_PREFIX}{png_filename}"

    filename = Path(file_path).name
    footer_lines = [
        "### ANSI C78.377-2015 chromaticity quadrangles on CIE 1931 (x,y)",
        f"![]({placeholder_url})",
        "",
        f"- Photometric report: [{filename}]({share_info.get('page', '')})",
    ]
    footer_block = "\n".join(footer_lines) + "\n"

    combined_summary = (
        f"{header_block}"
        "## Overall summary\n"
        f"{openai_summary_response}\n\n"
        "### Conclusion & Follow-up Actions\n"
        "- [ ] \n"
        "- [ ] \n"
        "- [ ] \n"
        "---\n"
        f"{updated_md}\n\n"
        f"{footer_block}"
    )

    global LATEST_SUMMARY
    LATEST_SUMMARY = combined_summary

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

    cct_xy = _extract_cct_xy(updated_md)

    return combined_summary, cct_xy, text, png_filename


def upload_cie_png(payload: str) -> str:
    """Receive base64 PNG payload from frontend, upload to Nextcloud, and refresh summary."""
    global LATEST_SUMMARY

    if not payload:
        log.warning("No payload received from frontend for CIE PNG upload.")
        return LATEST_SUMMARY

    obj = json.loads(payload)
    fname = (obj.get("filename") or "cie.png").strip() or "cie.png"
    data_url = obj.get("data_url") or ""
    if not data_url.startswith("data:image/png;base64,"):
        log.error("Unexpected CIE PNG data URL prefix: %s", data_url[:32])
        return LATEST_SUMMARY

    b64_data = data_url.split(",", 1)[1]
    png_bytes = base64.b64decode(b64_data)

    temp_path = Path(tempfile.gettempdir()) / fname
    temp_path.write_bytes(png_bytes)

    cie_share_info = ushare(str(temp_path), PNG_REMOTE_DIR)
    log.info("Uploaded CIE PNG to Nextcloud: %s", cie_share_info.get("remote_path"))
    share_url = cie_share_info.get("page", "")
    temp_path.unlink(missing_ok=True)

    if share_url:
        placeholder = f"{PNG_PLACEHOLDER_PREFIX}{fname}"
        preview_url = share_url.rstrip("/") + "/preview"
        if placeholder in LATEST_SUMMARY:
            LATEST_SUMMARY = LATEST_SUMMARY.replace(placeholder, preview_url)
    else:
        log.warning("CIE PNG share link unavailable; retaining placeholder link in summary.")

    return LATEST_SUMMARY

# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Photometric extraction", head=CLIPBOARD_POLYFILL) as demo:
    # Minimal visible controls: Upload, Submit, Summary, CIE canvas
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
    btn = gr.Button("Submit")

    combined_summary_box = gr.Textbox(label="Summary", lines=14, show_copy_button=True)

    # CIE chart
    gr.HTML(get_canvas_html(), elem_id="cie_box")

    # Expose raw PDF text and parsed x,y table; hide them via CSS unless debugging is enabled.
    original_text_box = gr.Textbox(
        label="Raw PDF text",
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

    # Hidden textbox used by JS to signal a PNG data URL to backend
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

    hidden_rules = [
        "#cie_png_upload { display: none !important; }",
        "#cie_png_name { display: none !important; }",
    ]
    if not DEBUG_TEXTBOXES:
        hidden_rules.extend(
            [
                "#cct_xy_df { display: none !important; }",
                "#original_text_box { display: none !important; }",
            ]
        )

    gr.HTML(
        "<style>\n  " + "\n  ".join(hidden_rules) + "\n</style>"
    )

    # Wire outputs: summary, parsed table, raw text, and planned PNG filename
    btn.click(
        handle_upload,
        inputs=inp,
        outputs=[combined_summary_box, cct_xy_box, original_text_box, cie_png_name_box],
    )

    # Load JS for CIE canvas drawing
    demo.load(fn=lambda: None, inputs=[], outputs=[], js=get_drawing_javascript())

    # Wire hidden upload bridge: when JS writes JSON into the hidden textbox, upload PNG
    cie_png_upload_box.change(upload_cie_png, inputs=[cie_png_upload_box], outputs=[combined_summary_box])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
