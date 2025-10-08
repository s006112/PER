from __future__ import annotations

import os
import logging
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr
from openai import OpenAI

load_dotenv()

# Basic logging setup (tune via LOG_LEVEL env; default INFO)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(levelname)s: %(message)s")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ----------------------------
# OpenAI helper
# ----------------------------
def query_openai_with_prompt(prompt_content: str, input_text: str) -> str:
    try:
        final_prompt = f"{prompt_content}\n\n{input_text}"

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

def handle_upload(user_text: str) -> str:
    """
    Gradio callback
    - Accept manual text input
    - Query OpenAI to assemble the weekly summary

    Returns:
      weekly_summary (str)
    """

    if not user_text or not user_text.strip():
        return "Error: No text provided."

    base_dir = Path(__file__).parent
    try:
        prompt_text = (base_dir / "prompt_w.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading prompt_w.txt: {e}"
    return query_openai_with_prompt(prompt_text, user_text)

# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Weekly Summary") as demo:
    # Minimal visible controls: Text input, Submit, Weekly summary
    with gr.Row():
        inp = gr.Textbox(
            label="Paste Text",
            lines=8,
            placeholder="Paste the content you want to analyse...",
        )
    btn = gr.Button("Submit")

    weekly_summary_box = gr.Textbox(label="Weekly Summary", lines=14, show_copy_button=True)

    # Wire outputs: Weekly summary response from OpenAI
    btn.click(
        handle_upload,
        inputs=inp,
        outputs=weekly_summary_box,
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=1986)
