from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Tuple

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

from app_odoo import create_sale_order_from_text

load_dotenv()

# Basic logging setup (tune via LOG_LEVEL env; default INFO)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
log = logging.getLogger("app")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_SHOW_PO_TEXTBOXES = os.getenv("PO_SHOW_TEXTBOXES", "true").strip().lower() not in {"0", "false", "no", "off"}

# ----------------------------
# PDF parsing
# ----------------------------
def extract_pdf_text_from_bytes(data: bytes) -> Tuple[str, str]:
    """
    Parse PDF bytes into plain text using PyMuPDF when available.
    Returns: (engine_name, pdf_parsing_text)
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        pdf_parsing_text = "\n".join([page.get_text("text") for page in doc])
        return "PyMuPDF", pdf_parsing_text
    except Exception as e:
        # Keep behavior minimal: signal parsing failure by empty text
        return f"Error: {e}", ""

# ----------------------------
# OpenAI helper
# ----------------------------
def query_openai_with_prompt(prompt_content: str, pdf_parsing_text: str) -> str:
    try:
        if "{context}" in prompt_content:
            final_prompt = prompt_content.replace("{context}", pdf_parsing_text)
        else:
            final_prompt = f"{prompt_content}\n\n{pdf_parsing_text}"

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

def handle_upload(file_path: str, salesperson: str) -> Tuple[str, str, str]:
    """
    Gradio callback
    - Read PDF path, extract text
    - Query OpenAI for PO extraction details
    - Inject the manually provided salesperson name into the response

    Returns:
      po_response_text (str), pdf_parsing_text (str), import_log (str)
    """

    if not salesperson or not salesperson.strip():
        return "Error: Sales person is required.", "", ""

    salesperson_value = salesperson.strip()
    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", "", ""

    with open(file_path, "rb") as f:
        data = f.read()

    _engine, pdf_parsing_text = extract_pdf_text_from_bytes(data)
    if not pdf_parsing_text:
        return "Error: PDF parsing failed.", "", ""

    base_dir = Path(__file__).parent
    try:
        prompt_po_str = (base_dir / "Prompt_po.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_po.txt: {e}", "", ""
    openai_po_response = query_openai_with_prompt(prompt_po_str, pdf_parsing_text)
    sale_order_message = ""
    if openai_po_response and not openai_po_response.startswith("Error"):
        lines_without_salesperson = [
            line for line in openai_po_response.splitlines() if not line.strip().startswith("self.salesperson")
        ]
        sanitized_response = "\n".join(line for line in lines_without_salesperson if line.strip())
        salesperson_literal = json.dumps(salesperson_value)
        header_line = f"self.salesperson = {salesperson_literal}"
        openai_po_response = f"{header_line}\n{sanitized_response}" if sanitized_response else header_line
        try:
            order_id, _ = create_sale_order_from_text(openai_po_response)
            sale_order_message = f"Created Odoo sale order ID: {order_id}"
        except Exception as exc:
            log.exception("Odoo sale order creation failed: %s", exc)
            sale_order_message = f"Odoo sale order creation failed: {exc}"

    if sale_order_message:
        pdf_output = f"{pdf_parsing_text}\n\n{sale_order_message}" if pdf_parsing_text else sale_order_message
    else:
        pdf_output = pdf_parsing_text

    return openai_po_response, pdf_output, sale_order_message

# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Photometric extraction") as demo:
    # Minimal visible controls: Upload, Submit, PO response
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
        salesperson_input = gr.Textbox(label="Sales person", lines=1, placeholder="Enter sales person name")
    btn = gr.Button("Submit")

    po_response_box = gr.Textbox(label="PO response", lines=14, show_copy_button=True, visible=_SHOW_PO_TEXTBOXES)

    pdf_parsing_box = gr.Textbox(
        label="PDF parsing",
        lines=10,
        show_copy_button=True,
        elem_id="pdf_parsing_box",
        visible=_SHOW_PO_TEXTBOXES,
    )

    import_log_box = gr.Textbox(label="Import Log", lines=2, interactive=False)

    # Wire outputs: PO response (visible), raw PDF parsing text (hidden but copyable), and import log
    btn.click(
        handle_upload,
        inputs=[inp, salesperson_input],
        outputs=[po_response_box, pdf_parsing_box, import_log_box],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7960)
