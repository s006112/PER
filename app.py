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
from pathlib import Path  # 你已在上一版加了就忽略，否則記得保留

# 解析后端优先级：PyMuPDF -> pypdf
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

import gradio as gr

ALLOWED_EXT = {".pdf"}
load_dotenv()

# Ensure OpenAI API key is set in your environment
openai.api_key = os.getenv('OPENAI_API_KEY')

def extract_pdf_text_from_bytes(data: bytes) -> Tuple[str, str]:
    """
    从 PDF 原始字节中提取文本（逐页合并），返回 (engine, text)
    """
    pages: Dict[int, str] = {}
    engine = "None"

    # 1) PyMuPDF
    if fitz is not None:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            for i, page in enumerate(doc, 1):
                txt = page.get_text("text", sort=True)  # 尽量保持“原始”顺序
                if txt and txt.strip():
                    pages[i] = txt
            doc.close()
            engine = "PyMuPDF"
        except Exception as e:
            pages = {}
            print(f"Error with PyMuPDF: {e}")

    # 2) pypdf fallback
    if not pages and PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    pass
            for i, page in enumerate(reader.pages, 1):
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                if txt.strip():
                    pages[i] = txt
            engine = "pypdf"
        except Exception as e:
            pages = {}
            print(f"Error with pypdf: {e}")

    # 拼接
    if not pages:
        return engine, "[该 PDF 无可提取文本或解析失败]"
    
    # Join text from all pages
    chunks = [f"=== Page {i} ===\n{pages[i]}" for i in sorted(pages)]
    text = "\n\n".join(chunks)

    return engine, text

def query_openai_with_prompt(text: str) -> str:
    """
    用 Prompt_md.txt 模板 + 抽取出的原文，透過 ChatOpenAI 產出結果。
    - 若模板已含 {context}/{question} 佔位符，直接用。
    - 若模板不含佔位符，則把原始文本與任務說明自動補齊。
    """
    try:
        template_path = Path(__file__).parent / "Prompt_md.txt"
        template_str = template_path.read_text("utf-8")

        # 1) 若缺少佔位符，補齊段落
        if "{context}" not in template_str:
            template_str += (
                "\n\n----------------\n"
                "【原始文本 / Context】\n"
                "{context}\n"
            )
        if "{question}" not in template_str:
            template_str += (
                "\n\n【任務 / Instruction】\n"
                "{question}\n"
            )

        # 2) 以「最終模板」為準，統一決定需要哪些變數
        required_vars = [v for v in ("context", "question") if f"{{{v}}}" in template_str]

        prompt = PromptTemplate(input_variables=required_vars, template=template_str)
        chain = LLMChain(llm=ChatOpenAI(model_name="gpt-4.1-mini", temperature=0.0), prompt=prompt)

        # 3) 只填充模板真正需要的鍵
        full_payload = {
            "context": text,
            "question": "請根據以上 Context，嚴格依照模板格式完整輸出結果。"
        }
        payload = {k: full_payload[k] for k in required_vars}

        result = chain.invoke(payload)
        return (result.get("text") if isinstance(result, dict) else str(result)).strip()

    except Exception as e:
        return f"Error querying OpenAI: {e}"

def handle_upload(file_path: str) -> Tuple[str, str, str]:
    """
    Gradio 回调：接收文件路径，读取 bytes，解析并查询 OpenAI
    """
    if not file_path or not os.path.isfile(file_path):
        return "**错误**：未选择文件或文件不存在。", "", ""

    ext = os.path.splitext(file_path.lower())[1]
    if ext not in ALLOWED_EXT:
        return "**错误**：仅支持 PDF。", "", ""

    with open(file_path, "rb") as f:
        data = f.read()
    if not data:
        return "**错误**：文件内容为空。", "", ""

    engine, text = extract_pdf_text_from_bytes(data)
    
    # Query OpenAI for the extracted text
    openai_response = query_openai_with_prompt(text)
    
    meta = (
        f"**文件**：{os.path.basename(file_path)}  \n"
        f"**大小**：{len(data)} bytes  \n"
        f"**解析引擎**：{engine}"
    )
    
    return meta, text, openai_response

with gr.Blocks(title="PDF 原始内容提取（Gradio 单文件）") as demo:
    gr.Markdown("上传 PDF → 点击提交 → 同页显示**未清洗的原始文本**，并显示 OpenAI 的摘要。")
    
    with gr.Row():
        inp = gr.File(label="选择 PDF 文件", file_types=[".pdf"], type="filepath")
    
    btn = gr.Button("提交 / Submit")
    
    meta = gr.Markdown()
    openai_response_box = gr.Textbox(label="AI 摘要", lines=10, show_copy_button=True)
    original_text_box   = gr.Textbox(label="提取结果（原始文本）", lines=10, show_copy_button=True)


    btn.click(handle_upload, inputs=inp, outputs=[meta, original_text_box, openai_response_box])

if __name__ == "__main__":
    demo.launch()
