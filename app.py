#!/usr/bin/env python3
# -*- coding: utf-8 -*- 
"""
Gradio 单文件：上传 PDF -> 同页显示原始文本
Python 3.10；优先 PyMuPDF，回退 pypdf
"""

from __future__ import annotations
import os, io, hashlib, datetime
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
    - 若模板不含佔位符，則把原始文本拼接到模板後再發送。
    """
    try:
        prompt_path = Path(__file__).parent / "Prompt_md.txt"
        if prompt_path.exists():
            template_str = prompt_path.read_text("utf-8")
        else:
            template_str = "Context:\n{context}\n\nQuestion:\n{question}"

        # 判斷模板是否已有佔位符
        has_context = "{context}" in template_str
        has_question = "{question}" in template_str

        # 若模板沒有 {context}，自動把原文附在模板後
        if not has_context:
            # 保持模板內容原樣，僅在末尾附上“原始文本”區塊
            template_str = (
                f"{template_str}\n\n"
                "----------------\n"
                "【原始文本 / Context】\n"
                "{context}\n"
            )
            has_context = True

        # 若模板沒有 {question}，補上一句通用任務說明
        if not has_question:
            template_str = (
                f"{template_str}\n\n"
                "【任務 / Instruction】\n"
                "{question}\n"
            )
            has_question = True

        # 構建 PromptTemplate（只放實際存在的變數）
        input_vars = []
        if has_context:
            input_vars.append("context")
        if has_question:
            input_vars.append("question")

        prompt = PromptTemplate(
            input_variables=input_vars,
            template=template_str
        )

        # 構建 LLMChain（使用新版 ChatOpenAI）
        llm = ChatOpenAI(model_name="gpt-4.1-mini", temperature=0.0)
        chain = LLMChain(llm=llm, prompt=prompt)

        # 準備 payload（僅提供存在的鍵）
        payload = {}
        if has_context:
            payload["context"] = text
        if has_question:
            payload["question"] = "請根據以上 Context，嚴格依照模板格式完整輸出結果。"

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
    original_text_box = gr.Textbox(label="提取结果（原始文本）", lines=10, show_copy_button=True)
    openai_response_box = gr.Textbox(label="OpenAI 摘要", lines=10, show_copy_button=True)

    btn.click(handle_upload, inputs=inp, outputs=[meta, original_text_box, openai_response_box])

if __name__ == "__main__":
    demo.launch()
