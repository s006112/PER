Sales Order Importer
====================

`app_so_import.py` provides a Gradio-powered workflow that converts customer PO PDFs into structured sale orders for Odoo. The app extracts text from the upload, prompts OpenAI to generate assignment-style Python code, and (when enabled) pushes the cleaned data and source PDF into Odoo.

End-to-End Flow
---------------
- **File intake** – `handle_upload()` validates the uploaded PDF and salesperson name, loads `Prompt_po.txt`, and invokes `_extract_text_with_pymupdf()` to recover page-level text.
- **AI parsing** – `query_openai_with_prompt()` merges the prompt and parsed text, then asks GPT-4.1-mini for a `self.<field> = ...` style payload. The function sanitizes the response, injects the provided salesperson, and returns both the generated code and raw PDF text for inspection.
- **Odoo integration** – When `ODOO_IMPORT=true`, `handle_upload()` passes the AI output to `create_sale_order_from_text()`. Successful imports trigger `attach_pdf_to_sale_order()` to archive the original PDF on the created record, and all status messages are surfaced back to the UI.
- **Frontend** – A compact `gr.Blocks` layout exposes the PDF uploader, salesperson textbox, submit button, and read-only import log. Optional debugging textboxes (`DEBUG_TEXTBOXES=true`) show the AI response and extracted PDF text.

Module Highlights
-----------------
- `app_so_import.py`
  - `_env_flag()` interprets boolean environment flags.
  - `handle_upload()` orchestrates PDF parsing, OpenAI prompting, optional Odoo calls, and user feedback. It also handles error reporting and salesperson overrides.
  - `demo` defines the Gradio UI and wires `handle_upload()` to button clicks.
- `app_odoo.py`
  - `load_odoo_config()` and `get_odoo_client()` authenticate against the Odoo XML-RPC API and cache the client for reuse.
  - `find_id()` performs progressive record matching: exact lookups, prefix searches, wildcard searches, and normalized comparisons before selecting the best candidate deterministically.
  - `parse_po_response_text()` uses `ast` to safely interpret the AI-generated `self.field = value` statements, enforcing required fields and data types.
  - `create_sale_order()` builds the XML-RPC payload, normalizes dates, parses quantities, retries with a fallback company if necessary, and reads back the created order.
  - `attach_pdf_to_sale_order()` uploads the PDF as an `ir.attachment`, posts a note on the sale order, and returns the attachment ID.
- `chunk_pdf.py`
  - `_extract_text_with_pymupdf()` uses PyMuPDF plus a custom sanitizer to build a `{page: text}` mapping, with automatic fallback to OCR (`ocrmypdf`) if no text is returned.
  - `extract_text_from_pdf_bytes()` logs extraction context and exposes the primary API used by `app_so_import.py`.
  - Additional helpers such as `get_pdf_full_text()` and `extract_pdf_attachment_tasks()` support other services that need flattened or chunked PDF text.

Key Environment Variables
-------------------------
- `OPENAI_API_KEY` – OpenAI client authentication.
- `LOG_LEVEL` – Logging verbosity (default `INFO`).
- `DEBUG_TEXTBOXES` – Reveals debugging textboxes in the Gradio UI.
- `ODOO_IMPORT` – Enables sale order creation and PDF attachment.
- `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, `ODOO_DEFAULT_COMPANY_NAME` – Required for Odoo connectivity and optional company fallback.

Running the App
---------------
Activate the relevant environment variables, then launch the Gradio interface:

```bash
python app_so_import.py
```

By default the app listens on `0.0.0.0:7960`. Set `ODOO_IMPORT=true` only when the Odoo credentials are configured and OpenAI responses are trusted for import.
