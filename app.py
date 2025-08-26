#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gradio 单文件：上传 PDF -> 同页显示原始文本，同时保存到 ./archived/
Python 3.10；优先 PyMuPDF，回退 pypdf
"""

from __future__ import annotations
import os, io, hashlib, datetime
from typing import Dict, Tuple

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
ARCHIVE_DIR = "archived"

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

def handle_upload(file_path: str) -> Tuple[str, str]:
    """
    Gradio 回调：接收文件路径，读取 bytes，归档并解析
    """
    if not file_path or not os.path.isfile(file_path):
        return "**错误**：未选择文件或文件不存在。", ""

    ext = os.path.splitext(file_path.lower())[1]
    if ext not in ALLOWED_EXT:
        return "**错误**：仅支持 PDF。", ""

    with open(file_path, "rb") as f:
        data = f.read()
    if not data:
        return "**错误**：文件内容为空。", ""

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    base = os.path.basename(file_path) or "upload.pdf"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    short = hashlib.sha256(data).hexdigest()[:8]
    saved_path = os.path.join(ARCHIVE_DIR, f"{stamp}_{short}_{base}")
    with open(saved_path, "wb") as out:
        out.write(data)

    engine, text = extract_pdf_text_from_bytes(data)
    meta = (
        f"**文件**：{base}  \n"
        f"**保存路径**：`{saved_path}`  \n"
        f"**大小**：{len(data)} bytes  \n"
        f"**解析引擎**：{engine}"
    )
    return meta, text

with gr.Blocks(title="PDF 原始内容提取（Gradio 单文件）") as demo:
    gr.Markdown("上传 PDF → 点击提交 → 同页显示**未清洗的原始文本**。文件将保存到 `./archived/`。")
    with gr.Row():
        inp = gr.File(label="选择 PDF 文件", file_types=[".pdf"], type="filepath")
    btn = gr.Button("提交 / Submit")
    meta = gr.Markdown()
    out = gr.Textbox(label="提取结果（原始文本）", lines=28, show_copy_button=True)

    btn.click(handle_upload, inputs=inp, outputs=[meta, out])

if __name__ == "__main__":
    # Gradio Space 不需要手动指定端口/host，保持缺省即可
    demo.launch()
