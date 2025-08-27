#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gradio 单文件：上传 PDF -> 同页显示原始文本 + 合并后的摘要
Python 3.10；优先 PyMuPDF
"""

from __future__ import annotations
import os
from typing import Tuple
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr

from openai import OpenAI
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def extract_pdf_text_from_bytes(data: bytes) -> Tuple[str, str]:
    """
    使用 PyMuPDF (fitz) 解析；
    """
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join([page.get_text("text") for page in doc])
        return "PyMuPDF", text
    except Exception:
        pass

# ----------------------------
# Generalized single entrypoint
# ----------------------------
def query_openai_with_prompt(prompt_content: str, text: str) -> str:
    """
    不使用 LangChain；直接以“prompt 文字 + context 文本”呼叫 OpenAI。
    """
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
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Error querying OpenAI: {e}"

def handle_upload(file_path: str):
    """
    Gradio 回调：接收文件路径，读取 bytes，解析并查询 OpenAI
    返回：meta（Markdown）、原始文本、合并后的摘要
    """
    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", "", ""

    with open(file_path, "rb") as f:
        data = f.read()

    result = extract_pdf_text_from_bytes(data)
    if not result:
        return "Error: PDF parsing failed.", "", ""
    engine, text = result

    # 读取两个 prompt 文件的“内容字符串”，并用通用函数调用
    base_dir = Path(__file__).parent
    prompt_md_str = (base_dir / "Prompt_md.txt").read_text("utf-8")
    prompt_summary_str = (base_dir / "Prompt_summary.txt").read_text("utf-8")

    openai_md_response = query_openai_with_prompt(prompt_md_str, text)
    openai_summary_response = query_openai_with_prompt(prompt_summary_str, openai_md_response)

    # 合并：一个 textarea 展示
    combined_summary = (
        "## Overall summary\n"
        f"{openai_summary_response}\n\n"
        "---\n"
        f"{openai_md_response}"
    )

    meta_md = f"**Parser**: {engine} · **Chars**: {len(text)}"
    return meta_md, text, combined_summary

with gr.Blocks(title="Photometric extraction") as demo:
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")

    btn = gr.Button("Submit")

    meta = gr.Markdown()
    combined_summary_box = gr.Textbox(label="Summary", lines=14, show_copy_button=True)
    original_text_box   = gr.Textbox(label="Sphere PDF extraction", lines=10, show_copy_button=True)

    # 注意输出顺序与 handle_upload 的返回顺序匹配
    btn.click(handle_upload, inputs=inp, outputs=[meta, original_text_box, combined_summary_box])

if __name__ == "__main__":
    demo.launch()
