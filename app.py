#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gradio 单文件：上传 PDF -> 同页显示原始文本
Python 3.10；优先 PyMuPDF，回退 pypdf
"""

from __future__ import annotations
import os, io, hashlib, datetime
from typing import Dict, Tuple
import re

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
        except Exception:
            pages = {}

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
        except Exception:
            pages = {}

    # 拼接
    if not pages:
        return engine, "[该 PDF 无可提取文本或解析失败]"
    chunks = [f"=== Page {i} ===\n{pages[i]}" for i in sorted(pages)]
    return engine, "\n\n".join(chunks)

def format_to_markdown(raw_text: str) -> str:
    """
    Converts raw text extracted from PDF into a markdown format similar to 提取结果 (Markdown format).txt
    """
    # Create a cleaner structure from raw text
    lines = raw_text.splitlines()
    markdown_output = ""

    for line in lines:
        # Extract the necessary parameters and convert to markdown format
        if 'x =' in line:
            markdown_output += f"x = {line.split('=')[1].strip()}\n"
        elif 'y =' in line:
            markdown_output += f"y = {line.split('=')[1].strip()}\n"
        elif 'CCT =' in line:
            markdown_output += f"CCT = {line.split('=')[1].strip()}\n"
        elif 'u\'' in line:
            markdown_output += f"u' = {line.split('=')[1].strip()}\n"
        elif 'v\'' in line:
            markdown_output += f"v' = {line.split('=')[1].strip()}\n"
        elif '色差Duv' in line:
            markdown_output += f"色差Duv = {line.split('=')[1].strip()}\n"
        elif '主波长' in line:
            markdown_output += f"主波长: λd = {line.split('=')[1].strip()}\n"
        elif '色纯度' in line:
            markdown_output += f"色纯度: Purity = {line.split('=')[1].strip()}\n"
        elif '峰值波长' in line:
            markdown_output += f"峰值波长: λp = {line.split('=')[1].strip()}\n"
        elif '显色指数' in line:
            markdown_output += f"显色指数: Ra = {line.split('=')[1].strip()}\n"
        elif '光通量' in line:
            markdown_output += f"光通量 Φ = {line.split('=')[1].strip()}\n"
        elif '光效' in line:
            markdown_output += f"光效 = {line.split('=')[1].strip()}\n"
        elif '辐射通量' in line:
            markdown_output += f"辐射通量 Φe = {line.split('=')[1].strip()}\n"
        elif '电压' in line:
            markdown_output += f"电压 V = {line.split('=')[1].strip()}\n"
        elif '电流' in line:
            markdown_output += f"电流 I = {line.split('=')[1].strip()}\n"
        elif '功率' in line:
            markdown_output += f"功率 P = {line.split('=')[1].strip()}\n"
        elif '功率因数' in line:
            markdown_output += f"功率因数 PF = {line.split('=')[1].strip()}\n"
        elif '白光分类' in line:
            markdown_output += f"白光分类: {line.split(':')[1].strip()}\n"
        elif '产品型号' in line:
            markdown_output += f"产品型号: {line.split(':')[1].strip()}\n"
        elif '产品编号' in line:
            markdown_output += f"产品编号: {line.split(':')[1].strip()}\n"
        elif '测试人员' in line:
            markdown_output += f"测试人员: {line.split(':')[1].strip()}\n"
        elif '测试日期' in line:
            markdown_output += f"测试日期: {line.split(':')[1].strip()}\n"
        elif '制造厂商' in line:
            markdown_output += f"制造厂商: {line.split(':')[1].strip()}\n"
        elif '备    注' in line:
            markdown_output += f"备注: {line.split(':')[1].strip()}\n"
        elif '环境温度' in line:
            markdown_output += f"环境温度: {line.split(':')[1].strip()}\n"
        elif '环境湿度' in line:
            markdown_output += f"环境湿度: {line.split(':')[1].strip()}\n"
    
    return markdown_output

def handle_upload_and_format(file_path: str) -> Tuple[str, str, str]:
    """
    Process the uploaded PDF and return both raw and formatted markdown text.
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

    engine, raw_text = extract_pdf_text_from_bytes(data)
    formatted_markdown = format_to_markdown(raw_text)

    meta = (
        f"**文件**：{os.path.basename(file_path)}  \n"
        f"**大小**：{len(data)} bytes  \n"
        f"**解析引擎**：{engine}"
    )

    return meta, raw_text, formatted_markdown

with gr.Blocks(title="PDF 原始内容提取（Gradio 单文件）") as demo:
    gr.Markdown("上传 PDF → 点击提交 → 同页显示**未清洗的原始文本**，并且转换为**Markdown 格式**，并进行结构化展示。")
    
    with gr.Row():
        inp = gr.File(label="选择 PDF 文件", file_types=[".pdf"], type="filepath")
        
    btn = gr.Button("提交 / Submit")
    meta = gr.Markdown()
    raw_out = gr.Textbox(label="提取结果（原始文本）", lines=5, show_copy_button=True)
    markdown_out = gr.Textbox(label="提取结果 (Markdown format)", lines=10, show_copy_button=True)
    
    btn.click(handle_upload_and_format, inputs=inp, outputs=[meta, raw_out, markdown_out])

if __name__ == "__main__":
    demo.launch()
