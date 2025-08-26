#!/usr/bin/env python3
# -*- coding: utf-8 -*- 
"""
Gradio 单文件：上传 PDF -> 同页显示原始文本
Python 3.10；优先 PyMuPDF，回退 pypdf
"""

from __future__ import annotations
import os, io
from typing import Dict, Tuple
from dotenv import load_dotenv
import openai  # Import OpenAI to use API for querying
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
    尝试使用 PyMuPDF (fitz) 解析；若失败，则回退 pypdf
    返回: (引擎, 提取的文本)
    """
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join([page.get_text("text") for page in doc])
        return "PyMuPDF", text
    except Exception:
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join([page.extract_text() or "" for page in reader.pages])
        return "pypdf", text
    except Exception as e:
        return "error", f"PDF parsing failed: {e}"

def query_openai_with_prompt(text: str) -> str:
    """
    使用 Prompt_md.txt
    """
    try:
        template_path = Path(__file__).parent / "Prompt_md.txt"
        template_str = template_path.read_text("utf-8")

        if "{context}" not in template_str:
            template_str += (
                "\n\n----------------\n"
                "【Original Content / Context】\n"
                "{context}\n"
            )
        if "{question}" not in template_str:
            template_str += (
                "\n\n【Task / Instruction】\n"
                "{question}\n"
            )

        required_vars = [v for v in ("context", "question") if f"{{{v}}}" in template_str]
        prompt = PromptTemplate(input_variables=required_vars, template=template_str)
        chain = LLMChain(llm=ChatOpenAI(model_name="gpt-4.1-mini", temperature=0.0), prompt=prompt)

        full_payload = {
            "context": text,
            "question": "Follow the above instruction and return a clean, markdown-formatted result."
        }
        payload = {k: full_payload[k] for k in required_vars}

        result = chain.invoke(payload)
        return (result.get("text") if isinstance(result, dict) else str(result)).strip()
    except Exception as e:
        return f"Error querying OpenAI: {e}"

def query_openai_with_prompt_summary(text: str) -> str:
    """
    使用 Prompt_summary.txt
    """
    try:
        template_path = Path(__file__).parent / "Prompt_summary.txt"
        template_str = template_path.read_text("utf-8")

        if "{context}" not in template_str:
            template_str += (
                "\n\n----------------\n"
                "【Original Content / Context】\n"
                "{context}\n"
            )
        if "{question}" not in template_str:
            template_str += (
                "\n\n【Task / Instruction】\n"
                "{question}\n"
            )

        required_vars = [v for v in ("context", "question") if f"{{{v}}}" in template_str]
        prompt = PromptTemplate(input_variables=required_vars, template=template_str)
        chain = LLMChain(llm=ChatOpenAI(model_name="gpt-4.1-mini", temperature=0.0), prompt=prompt)

        full_payload = {
            "context": text,
            "question": "Follow the above guidance strictly and produce a concise, technical, bullet-point summary."
        }
        payload = {k: full_payload[k] for k in required_vars}

        result = chain.invoke(payload)
        return (result.get("text") if isinstance(result, dict) else str(result)).strip()
    except Exception as e:
        return f"Error querying OpenAI (summary): {e}"

def handle_upload(file_path: str) -> Tuple[str, str, str, str]:
    """
    Gradio 回调：接收文件路径，读取 bytes，解析并查询 OpenAI
    """
    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", "", "", ""

    with open(file_path, "rb") as f:
        data = f.read()

    engine, text = extract_pdf_text_from_bytes(data)
    
    openai_md_response = query_openai_with_prompt(text)
    openai_summary_response = query_openai_with_prompt_summary(text)
    
#    meta = (
#        f"**文件**：{os.path.basename(file_path)}  \n"
#        f"**大小**：{len(data)} bytes  \n"
#        f"**解析引擎**：{engine}"
#    )
    
    return meta, text, openai_md_response, openai_summary_response

with gr.Blocks(title="PDF 原始内容提取（Gradio 单文件）") as demo:
#    gr.Markdown("上传 PDF → 点击提交 → 同页显示**未清洗的原始文本**，并显示 OpenAI 的摘要。")
    
    with gr.Row():
        inp = gr.File(label="选择 PDF 文件", file_types=[".pdf"], type="filepath")
    
    btn = gr.Button("提交 / Submit")
    
    meta = gr.Markdown()
    summary_points_box  = gr.Textbox(label="Photometric summary", lines=10, show_copy_button=True)
    markdown_summary_box = gr.Textbox(label="Markdown summary", lines=10, show_copy_button=True)
    original_text_box   = gr.Textbox(label="Sphere PDF extraction", lines=10, show_copy_button=True)

    btn.click(handle_upload, inputs=inp, outputs=[meta, original_text_box, markdown_summary_box, summary_points_box])

if __name__ == "__main__":
    demo.launch()
