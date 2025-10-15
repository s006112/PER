from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Tuple

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

from app_odoo import attach_pdf_to_sale_order, create_sale_order_from_text
from chunk_pdf import _extract_text_with_pymupdf

load_dotenv()

def _env_flag(name: str, default: bool) -> bool:
    """Interpret environment variable `name` as boolean: only 'true' (case-insensitive) is treated as True."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() == "true"


# Basic logging setup (tune via LOG_LEVEL env; default INFO)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
log = logging.getLogger("app")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_SHOW_PO_TEXTBOXES = _env_flag("DEBUG_TEXTBOXES", False)
_ODOO_IMPORT_ENABLED = _env_flag("ODOO_IMPORT", False)

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

    pdf_pages = _extract_text_with_pymupdf(data)
    if not pdf_pages:
        return "Error: PDF parsing failed.", "", ""
    pdf_parsing_text = "\n\n".join(pdf_pages.values())

    base_dir = Path(__file__).parent
    try:
        prompt_po_str = (base_dir / "Prompt_po.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_po.txt: {e}", "", ""
    openai_po_response = query_openai_with_prompt(prompt_po_str, pdf_parsing_text)
    import_messages: list[str] = []
    if openai_po_response and not openai_po_response.startswith("Error"):
        lines_without_salesperson = [
            line for line in openai_po_response.splitlines() if not line.strip().startswith("self.salesperson")
        ]
        sanitized_response = "\n".join(line for line in lines_without_salesperson if line.strip())
        salesperson_literal = json.dumps(salesperson_value)
        header_line = f"self.salesperson = {salesperson_literal}"
        openai_po_response = f"{header_line}\n{sanitized_response}" if sanitized_response else header_line
        if _ODOO_IMPORT_ENABLED:
            try:
                order_id, order_data = create_sale_order_from_text(openai_po_response)
                creation_message = f"Created Odoo sale order ID: {order_id}"
                import_messages.append(creation_message)

                order_name = ""
                if isinstance(order_data, dict):
                    order_name = str(order_data.get("name") or "").strip()
                if not order_name:
                    log.error("Missing sale order name for order %s; skipping attachment.", order_id)
                    import_messages.append("Attachment skipped: missing sale order name from Odoo response.")
                else:
                    try:
                        attachment_id = attach_pdf_to_sale_order(
                            sale_order_identifier=order_name,
                            pdf_path=file_path,
                            note_body="Attached customer PO",
                        )
                        import_messages.append(f"Attached PDF as ir.attachment {attachment_id}")
                    except Exception as attach_exc:
                        log.exception(
                            "Failed to attach PDF '%s' to sale order %s: %s",
                            file_path,
                            order_name,
                            attach_exc,
                        )
                        import_messages.append(f"Attachment failed: {attach_exc}")
            except Exception as exc:
                log.exception("Odoo sale order creation failed: %s", exc)
                import_messages.append(f"Odoo sale order creation failed: {exc}")
        else:
            import_messages.append("Odoo import skipped: ODOO_IMPORT flag is not set to true.")

    import_log_message = "\n".join(import_messages)
    if import_log_message:
        pdf_output = f"{pdf_parsing_text}\n\n{import_log_message}" if pdf_parsing_text else import_log_message
    else:
        pdf_output = pdf_parsing_text

    return openai_po_response, pdf_output, import_log_message

# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="SO importer") as demo:
    # Minimal visible controls: Upload, Submit, PO response
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
        salesperson_input = gr.Textbox(label="Sales person", lines=1, placeholder="Enter sales person name")
    btn = gr.Button("Submit")

    import_log_box = gr.Textbox(label="Import Log", lines=2, interactive=False)

    po_response_box = gr.Textbox(label="PO response", lines=14, show_copy_button=True, visible=_SHOW_PO_TEXTBOXES)

    pdf_parsing_box = gr.Textbox(
        label="PDF parsing",
        lines=10,
        show_copy_button=True,
        elem_id="pdf_parsing_box",
        visible=_SHOW_PO_TEXTBOXES,
    )

    # Wire outputs: PO response (visible), raw PDF parsing text (hidden but copyable), and import log
    btn.click(
        handle_upload,
        inputs=[inp, salesperson_input],
        outputs=[po_response_box, pdf_parsing_box, import_log_box],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7960)
