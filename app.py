#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gradio 单文件：上传 PDF -> 同页显示原始文本 + 合并后的摘要
Python 3.10；优先 PyMuPDF，回退 pypdf
"""

from __future__ import annotations
import os
from typing import Tuple
from dotenv import load_dotenv
import openai  # keep for compatibility; uses OPENAI_API_KEY env var
from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from pathlib import Path
import gradio as gr

# 加载环境变量
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

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
    以“prompt 内容字符串 + 上下文文本”作为输入，调用 LLM。
    - 不再自动插入 {question}；如果模板里包含 {question}，传入空串占位。
    - 保持 {context} 兜底：若模板缺少 {context}，自动追加一段以确保上下文被注入。
    """
    try:
        template_str = prompt_content

        # 保留 {context} 的安全兜底
        if "{context}" not in template_str:
            template_str += (
                "\n\n----------------\n"
                "【Original Content / Context】\n"
                "{context}\n"
            )

        # 不自动添加 {question}；模板为唯一真实信息来源
        required_vars = [v for v in ("context", "question") if f"{{{v}}}" in template_str]
        prompt = PromptTemplate(input_variables=required_vars, template=template_str)
        chain = LLMChain(llm=ChatOpenAI(model_name="gpt-4.1-mini", temperature=0.0), prompt=prompt)

        payload = {}
        if "context" in required_vars:
            payload["context"] = text
        if "question" in required_vars:
            payload["question"] = ""  # 中性占位；如需动态任务可在外层传入

        result = chain.invoke(payload)
        return (result.get("text") if isinstance(result, dict) else str(result)).strip()
    except Exception as e:
        return f"Error querying OpenAI: {e}"

def handle_upload(file_path: str) -> Tuple[str, str, str]:
    """
    Gradio 回调：接收文件路径，读取 bytes，解析并查询 OpenAI
    返回：meta（Markdown）、原始文本、合并后的摘要
    """
    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", "", ""

    with open(file_path, "rb") as f:
        data = f.read()

    engine, text = extract_pdf_text_from_bytes(data)

    # 读取两个 prompt 文件的“内容字符串”，并用通用函数调用
    base_dir = Path(__file__).parent
    prompt_md_str = (base_dir / "Prompt_md.txt").read_text("utf-8")
    prompt_summary_str = (base_dir / "Prompt_summary.txt").read_text("utf-8")

    openai_md_response = query_openai_with_prompt(prompt_md_str, text)
    openai_summary_response = query_openai_with_prompt(prompt_summary_str, text)

    # 合并：一个 textarea 展示
    combined_summary = (
        "## Overall summary\n"
        f"{openai_summary_response}\n\n"
        "---\n"
        f"{openai_md_response}"
    )

    # 返回 meta（修正：返回真实字符串而非未定义变量）
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
