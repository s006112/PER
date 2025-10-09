from __future__ import annotations

import os
import logging
from typing import Tuple
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr
from openai import OpenAI

load_dotenv()

# Basic logging setup (tune via LOG_LEVEL env; default INFO)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
log = logging.getLogger("app")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

def handle_upload(file_path: str) -> Tuple[str, str]:
    """
    Gradio callback
    - Read PDF path, extract text
    - Query OpenAI for PO extraction details

    Returns:
      po_response_text (str), pdf_parsing_text (str)
    """

    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", ""

    with open(file_path, "rb") as f:
        data = f.read()

    _engine, pdf_parsing_text = extract_pdf_text_from_bytes(data)
    if not pdf_parsing_text:
        return "Error: PDF parsing failed.", ""

    base_dir = Path(__file__).parent
    try:
        prompt_po_str = (base_dir / "Prompt_po.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_po.txt: {e}", ""
    openai_po_response = query_openai_with_prompt(prompt_po_str, pdf_parsing_text)
    return openai_po_response, pdf_parsing_text

# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Photometric extraction") as demo:
    # Minimal visible controls: Upload, Submit, PO response
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
    btn = gr.Button("Submit")

    po_response_box = gr.Textbox(label="PO response", lines=14, show_copy_button=True)

    pdf_parsing_box = gr.Textbox(
        label="PDF parsing",
        lines=10,
        show_copy_button=True,
        elem_id="pdf_parsing_box",
    )

    # Wire outputs: PO response (visible) and raw PDF parsing text (hidden but copyable)
    btn.click(
        handle_upload,
        inputs=inp,
        outputs=[po_response_box, pdf_parsing_box],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7960)
