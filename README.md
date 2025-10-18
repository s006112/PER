Photometric Extraction App
==========================

This project hosts a Gradio interface (`app_per.py`) that automates the extraction and summarisation of photometric PDF reports. Uploading a report triggers a multi-stage pipeline that produces a ready-to-share summary, extracts chromaticity coordinates, and syncs artefacts with Nextcloud.

Processing Pipeline
-------------------
- **PDF ingestion** – `handle_upload()` reads the uploaded file, uses `chunk_pdf.get_pdf_full_text()` to recover full text, and guards against empty or unreadable PDFs.
- **Prompt-driven analysis** – The raw text is fed to `query_openai_with_prompt()` twice: first with `Prompt_md.txt` to obtain a structured markdown analysis, then with `Prompt_summary.txt` to condense that analysis into an executive summary.
- **Report assembly** – A templated header/ footer wraps the AI responses with branding, placeholder checkboxes, and a link back to the shared PDF on Nextcloud (`nextcloud_upload.share()`).
- **CIE PNG coordination** – A deterministic timestamp defines the expected PNG artefact name so the frontend can later upload the rendered chart.
- **Chromaticity parsing** – `_extract_cct_xy()` walks the generated markdown, isolates the “Product category” table, and uses regex matching to pull numeric CIE 1931 x,y pairs for display in a Gradio dataframe.
- **State caching** – `LATEST_SUMMARY` keeps the combined report available for incremental updates once the CIE chart arrives.

Hidden Upload Bridge
--------------------
`upload_cie_png()` receives a base64 payload emitted by custom frontend JavaScript, writes a temporary PNG, and calls `nextcloud_upload.ushare()` to place it under `/Documents/PER/CIE Chart`. When the share link returns, the function swaps the placeholder URI embedded in the cached summary with the public preview URL.

User Interface
--------------
The Gradio layout, built inside `gr.Blocks`, exposes only the essential controls (file upload, submit button, summary box, and a CIE canvas created with `cie1931.get_canvas_html()` / `get_drawing_javascript()`). Diagnostic elements such as the raw PDF text, the parsed x,y table, and the hidden PNG bridge textbox are suppressed unless `DEBUG_TEXTBOXES=true`. Launch configuration binds the app to `0.0.0.0:7860`.

Key Environment Inputs
----------------------
- `OPENAI_API_KEY` – required for the GPT-4.1-mini calls.
- `LOG_LEVEL` – adjusts logging verbosity (default `INFO`).
- `DEBUG_TEXTBOXES` – toggles visibility of debugging widgets.

Supporting Files
----------------
`Prompt_md.txt` and `Prompt_summary.txt` craft the AI prompts; `nextcloud_upload.py` provides sharing helpers; `cie1931.py` generates the interactive colour space canvas. Together they enable a mostly hands-off workflow for turning raw photometric PDFs into polished reports.










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
