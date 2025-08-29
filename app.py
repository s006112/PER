#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gradio single-file app:
- Upload PDF -> parse text
- Show meta, Summary, CIE chart, and original text (Sphere PDF extraction)
"""
from __future__ import annotations

import os
import re
from typing import Tuple
from dotenv import load_dotenv
from pathlib import Path
import gradio as gr
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ----------------------------
# PDF parsing
# ----------------------------
def extract_pdf_text_from_bytes(data: bytes) -> Tuple[str, str]:
    """
    Parse PDF into plain text. Prefer PyMuPDF.
    Returns: (engine_name, text)
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join([page.get_text("text") for page in doc])
        return "PyMuPDF", text
    except Exception as e:
        # Keep behavior minimal: signal parsing failure by empty text
        return f"Error: {e}", ""

# ----------------------------
# OpenAI helper
# ----------------------------
def query_openai_with_prompt(prompt_content: str, text: str) -> str:
    """
    Directly call OpenAI chat completion: prompt + context
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
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"Error querying OpenAI: {e}"

# ----------------------------
# Upload handler
# ----------------------------

def handle_upload(file_path: str):
    """
    Gradio callback: read file path, parse PDF, call OpenAI with two prompts
    Returns: combined_summary (str), cct_xy (list[list])  # [[参数, x, y], ...]
    """
    import os
    import re
    from pathlib import Path

    if not file_path or not os.path.isfile(file_path):
        return "Error: No file found.", []

    with open(file_path, "rb") as f:
        data = f.read()

    engine, text = extract_pdf_text_from_bytes(data)
    if not text:
        return "Error: PDF parsing failed.", []

    base_dir = Path(__file__).parent
    try:
        prompt_md_str = (base_dir / "Prompt_md.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_md.txt: {e}", []
    try:
        prompt_summary_str = (base_dir / "Prompt_summary.txt").read_text("utf-8")
    except Exception as e:
        return f"Error reading Prompt_summary.txt: {e}", []

    openai_md_response = query_openai_with_prompt(prompt_md_str, text)
    openai_summary_response = query_openai_with_prompt(prompt_summary_str, openai_md_response)

    combined_summary = (
        "## Overall summary\n"
        f"{openai_summary_response}\n\n"
        "---\n"
        f"{openai_md_response}"
    )

    # --- Parse "### 光谱参数" table for 色坐标 (x, y) into cct_xy matrix
    # Requirements:
    # - cct_xy item count reflects the number of data rows (single or multiple).
    # - 参数 content is column 1 of the table (1-based), i.e., the first column (index 0).
    # - Count row cell numbers first and only build entries from rows with enough cells.
    def _extract_cct_xy(md: str):
        try:
            lines = md.splitlines()

            # Locate the "### 光谱参数" section
            start = None
            for i, ln in enumerate(lines):
                s = ln.strip()
                if s.startswith("###") and "光谱参数" in s:
                    start = i
                    break
            if start is None:
                return []

            # End at next "### " or EOF
            end = len(lines)
            for j in range(start + 1, len(lines)):
                ss = lines[j].strip()
                if ss.startswith("### "):
                    end = j
                    break
            section = lines[start:end]

            # Collect markdown table lines
            table_lines = [ln for ln in section if "|" in ln]
            if not table_lines:
                return []

            rows = []
            for ln in table_lines:
                s = ln.strip()
                cells = [c.strip() for c in s.strip("|").split("|")]
                # Skip separator rows like | --- | --- |
                if all(re.fullmatch(r"-{3,}", c or "") for c in cells):
                    continue
                rows.append(cells)
            if not rows:
                return []

            # Find header row (contains column names including 色坐标 and likely 参数)
            header_idx = None
            for idx, r in enumerate(rows):
                if any("色坐标" in c for c in r):
                    header_idx = idx
                    break
            if header_idx is None:
                return []

            header = rows[header_idx]

            # Determine indices with robustness
            try:
                xy_col = next(i for i, c in enumerate(header) if "色坐标" in c)
            except StopIteration:
                return []

            # 参数 is specified as column 1 (1-based) => index 0; but if a header named 参数 exists elsewhere, prefer that.
            param_col = 0
            for i, c in enumerate(header):
                if "参数" in c:
                    param_col = i
                    break

            # Count row cell numbers first; only keep rows with enough cells
            required_cols = max(param_col, xy_col) + 1
            data_rows = [r for r in rows[header_idx + 1:] if len(r) >= required_cols]
            # At this point, cct_xy length should match valid data_rows length (may be 0, 1, or many)
            if not data_rows:
                return []

            out = []
            for r in data_rows:
                xy_text = r[xy_col]
                # tolerant match: "0.3191, 0.2190" with optional spaces
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*,\s*([0-9]+(?:\.[0-9]+)?)", xy_text)
                if not m:
                    continue
                try:
                    x = float(m.group(1))
                    y = float(m.group(2))
                except Exception:
                    continue

                # 参数 content as column 1
                param = r[param_col] if len(r) > param_col and r[param_col] else f"行{len(out)+1}"
                out.append([param, x, y])

            return out
        except Exception:
            return []

    cct_xy = _extract_cct_xy(openai_md_response)

    return combined_summary, cct_xy, text


# ----------------------------
# CIE 1931 canvas + JS (Shadow-DOM safe; robust for Spaces)
# ----------------------------
CANVAS_HTML = """
<div style="padding:12px;background:#fff;border:1px solid #ddd;margin:20px">
  <h1 style="font-size:18px;margin:0 0 8px">ANSI C78.377-2015 chromaticity quadrangles on CIE 1931 (x,y)</h1>
  <canvas id="cie" width="900" height="600"
          style="max-width:100%;height:auto;border:1px solid #ddd;background:#fff">
    Canvas not supported.
  </canvas>
</div>
"""

# IMPORTANT:
# - Use demo.load(js=JS_DRAW) so it runs after app mounts.
# - Use a Shadow-DOM–aware root: query inside <gradio-app>.shadowRoot when present.

JS_DRAW = r"""
() => {
  const MAX_RETRIES = 200;
  let prevSig = "";          // avoid redundant redraws
  let bgCanvas = null;       // cached background (axes/grid/planck/bins)
  let canvasRef = null;      // hold canvas for addPoints/clearPoints API
  const MAX_POINTS = 50;     // hard cap to avoid clutter

  function gradioRoot() {
    const ga = document.querySelector('gradio-app');
    return ga && ga.shadowRoot ? ga.shadowRoot : document;
  }
  function locateCanvas(root) {
    const host = root.getElementById("cie_box");
    const c = host ? host.querySelector("#cie") : root.getElementById("cie");
    return c || null;
  }

  // Robustly extract [[label, x, y], ...] from cct_xy DataFrame
  function extractPoints(root){
    const host = root.getElementById("cct_xy_df");
    if(!host) return [];
    const seen = new Set();
    const pts  = [];

    // 1) Preferred: parse all TBODY rows anywhere under cct_xy_df
    const rows = Array.from(host.querySelectorAll("tbody tr"));
    if (rows.length > 0){
      for (const tr of rows){
        const cells = tr.querySelectorAll("td,th");
        if (cells.length >= 3){
          const label = (cells[0].textContent || "").trim();
          const x = parseFloat((cells[1].textContent || "").replace(/[^\d.\-]/g,""));
          const y = parseFloat((cells[2].textContent || "").replace(/[^\d.\-]/g,""));
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          const k = x.toFixed(4)+","+y.toFixed(4);
          if (seen.has(k)) continue;
          seen.add(k);
          pts.push([label, x, y]);
          if (pts.length >= MAX_POINTS) break;
        }
      }
      if (pts.length) return pts;
    }

    // 2) Fallback: plain text parsing (comma/whitespace separated triplets)
    const text = (host.textContent || "").trim();
    if (!text) return [];
    for (const line of text.split(/\n+/)){
      // examples: "数值 1,0.3398,0.3509" or "数值1 0.3398 0.3509"
      const m = line.match(/^\s*(.+?)\s*[,\s]\s*([+-]?\d*\.?\d+)\s*[,\s]\s*([+-]?\d*\.?\d+)\s*$/);
      if (!m) continue;
      const label = m[1].trim();
      const x = parseFloat(m[2]);
      const y = parseFloat(m[3]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      const k = x.toFixed(4)+","+y.toFixed(4);
      if (seen.has(k)) continue;
      seen.add(k);
      pts.push([label, x, y]);
      if (pts.length >= MAX_POINTS) break;
    }
    return pts;
  }

  function draw(canvas, points){
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;

    const xmin=0.28, xmax=0.50, ymin=0.30, ymax=0.44, pad=60;
    const sx = x => pad + (x - xmin) * (W - 2*pad) / (xmax - xmin);
    const sy = y => H - pad - (y - ymin) * (H - 2*pad) / (ymax - ymin);

    function hexToRgba(hex,a){
      let h=hex.replace('#','');
      if(h.length===3){h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];}
      const r=parseInt(h.slice(0,2),16), g=parseInt(h.slice(2,4),16), b=parseInt(h.slice(4,6),16);
      return `rgba(${r},${g},${b},${a})`;
    }

    const bins=[
      {cct:2700, center:[0.4578,0.4101], corners:[[0.4813,0.4319],[0.4562,0.4260],[0.4373,0.3893],[0.4593,0.3944]]},
      {cct:3000, center:[0.4339,0.4033], corners:[[0.4562,0.4260],[0.4303,0.4173],[0.4150,0.3821],[0.4373,0.3893]]},
      {cct:3500, center:[0.4078,0.3930], corners:[[0.4303,0.4173],[0.4003,0.4035],[0.3895,0.3709],[0.4150,0.3821]]},
      {cct:4000, center:[0.3818,0.3797], corners:[[0.4003,0.4035],[0.3737,0.3880],[0.3671,0.3583],[0.3895,0.3709]]},
      {cct:4500, center:[0.3613,0.3670], corners:[[0.3737,0.3882],[0.3550,0.3754],[0.3514,0.3482],[0.3672,0.3585]]},
      {cct:5000, center:[0.3446,0.3551], corners:[[0.3550,0.3753],[0.3375,0.3619],[0.3366,0.3373],[0.3515,0.3481]]},
      {cct:5700, center:[0.3287,0.3425], corners:[[0.3375,0.3619],[0.3205,0.3476],[0.3221,0.3256],[0.3366,0.3374]]},
      {cct:6500, center:[0.3123,0.3283], corners:[[0.3205,0.3477],[0.3026,0.3311],[0.3067,0.3119],[0.3221,0.3255]]},
    ];

    // --- Background drawing (axes, planck, bins) ---
    function drawAxes(bg){
      bg.clearRect(0,0,W,H);
      bg.fillStyle='#fff'; bg.fillRect(0,0,W,H);
      bg.strokeStyle='#000'; bg.lineWidth=1;
      bg.strokeRect(pad,pad,W-2*pad,H-2*pad);
      bg.font='12px sans-serif'; bg.fillStyle='#000';
      const step=0.02;
      for(let x=Math.ceil(xmin/step)*step; x<=xmax+1e-9; x+=step){
        const X=sx(x);
        bg.strokeStyle='#e6e6e6'; bg.beginPath(); bg.moveTo(X, sy(ymin)); bg.lineTo(X, sy(ymax)); bg.stroke();
        bg.strokeStyle='#999';    bg.beginPath(); bg.moveTo(X, sy(ymin)); bg.lineTo(X, sy(ymin)-4); bg.stroke(); // fix: sy(ymin)
        bg.fillStyle='#000'; bg.fillText(x.toFixed(2), X-12, sy(ymin)+16);
      }
      for(let y=Math.ceil(ymin/step)*step; y<=ymax+1e-9; y+=step){
        const Y=sy(y);
        bg.strokeStyle='#e6e6e6'; bg.beginPath(); bg.moveTo(sx(xmin), Y); bg.lineTo(sx(xmax), Y); bg.stroke();
        bg.strokeStyle='#999';    bg.beginPath(); bg.moveTo(sx(xmin), Y); bg.lineTo(sx(xmin)-4, Y); bg.stroke();
        bg.fillStyle='#000'; bg.fillText(y.toFixed(2), sx(xmin)-40, Y+4);
      }
      bg.fillStyle='#000';
      bg.fillText('x', sx(xmax)+10, sy(ymin)+4);
      bg.fillText('y', sx(xmin)-10, sy(ymax)-8);
    }
    function planckXY(T){
      let x,y;
      if(T>=1667 && T<=4000){
        x = (-0.2661239e9)/(T*T*T) - (0.2343580e6)/(T*T) + (0.8776956e3)/T + 0.179910;
      } else if(T>4000 && T<=25000){
        x = (-3.0258469e9)/(T*T*T) + (2.1070379e6)/(T*T) + (0.2226347e3)/T + 0.240390;
      } else { return [NaN,NaN]; }
      if(T>=1667 && T<=2222){
        y = -1.1063814*Math.pow(x,3) - 1.34811020*Math.pow(x,2) + 2.18555832*x - 0.20219683;
      } else if(T>2222 && T<=4000){
        y = -0.9549476*Math.pow(x,3) - 1.37418593*Math.pow(x,2) + 2.09137015*x - 0.16748867;
      } else {
        y =  3.0817580*Math.pow(x,3) - 5.87338670*Math.pow(x,2) + 3.75112997*x - 0.37001483;
      }
      return [x,y];
    }
    function drawPlanck(bg){
      bg.strokeStyle='#444'; bg.lineWidth=1.5;
      if (bg.setLineDash) bg.setLineDash([5,3]);
      bg.beginPath();
      let started=false;
      for(let T=2000; T<=10000; T+=50){
        const [x,y]=planckXY(T); if (!isFinite(x)) continue;
        const X=sx(x), Y=sy(y);
        if(!started){ bg.moveTo(X,Y); started=true; } else { bg.lineTo(X,Y); }
      }
      bg.stroke();
      if (bg.setLineDash) bg.setLineDash([]);
    }
    function drawBins(bg){
      const palette=['#d81b60','#8e24aa','#3949ab','#1e88e5','#00897b','#43a047','#fdd835','#fb8c00','#e53935','#6d4c41','#7b1fa2'];
      for(let i=0;i<bins.length;i++){
        const b=bins[i], color=palette[i%palette.length], P=b.corners;
        bg.lineWidth=2; bg.strokeStyle=color; bg.fillStyle=hexToRgba(color,0.16);
        bg.beginPath();
        bg.moveTo(sx(P[0][0]), sy(P[0][1]));
        for(let j=1;j<P.length;j++){ bg.lineTo(sx(P[j][0]), sy(P[j][1])); }
        bg.closePath(); bg.fill(); bg.stroke();
        const cx=sx(b.center[0]), cy=sy(b.center[1]);
        bg.fillStyle=color; bg.beginPath(); bg.arc(cx,cy,3,0,Math.PI*2); bg.fill();
        bg.font='12px sans-serif'; bg.fillText(b.cct+'K', cx+6, cy-6);
      }
    }

    function ensureBackground(){
      if (bgCanvas) return bgCanvas;
      const oc = document.createElement('canvas'); oc.width=W; oc.height=H;
      const bg = oc.getContext('2d');
      drawAxes(bg); drawPlanck(bg); drawBins(bg);
      bgCanvas = oc;
      return bgCanvas;
    }

    function drawPoints(points){
      if (!Array.isArray(points) || !points.length) return;
      const size = 4;
      // halo
      ctx.lineWidth = 1; ctx.strokeStyle = '#fff';
      for (const [_,x,y] of points){
        if (x < xmin || x > xmax || y < ymin || y > ymax) continue;
        const X=sx(x), Y=sy(y);
        ctx.beginPath();
        ctx.moveTo(X - size, Y - size); ctx.lineTo(X + size, Y + size);
        ctx.moveTo(X - size, Y + size); ctx.lineTo(X + size, Y - size);
        ctx.stroke();
      }
      // black overlay
      ctx.lineWidth = 1; ctx.strokeStyle = '#000';
      for (const [_,x,y] of points){
        if (x < xmin || x > xmax || y < ymin || y > ymax) continue;
        const X=sx(x), Y=sy(y);
        ctx.beginPath();
        ctx.moveTo(X - size, Y - size); ctx.lineTo(X + size, Y + size);
        ctx.moveTo(X - size, Y + size); ctx.lineTo(X + size, Y - size);
        ctx.stroke();
      }
    }

    // compose: bg + points
    ensureBackground();
    ctx.clearRect(0,0,W,H);
    ctx.drawImage(bgCanvas, 0, 0);
    if (Array.isArray(points) && points.length) drawPoints(points);
  }

  // Watch dataframe changes and redraw
  function observeDataframe(root, canvas){
    const host = root.getElementById("cct_xy_df");
    if (!host) return;
    let rafId = null;
    const update = () => {
      const pts = extractPoints(root);
      const sig = JSON.stringify(pts);
      if (sig !== prevSig){
        prevSig = sig;
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
          try { draw(canvas, pts); } catch(e){ console.error("CIE redraw error:", e); }
        });
      }
    };
    const mo = new MutationObserver(update);
    mo.observe(host, {subtree:true, childList:true, characterData:true});
    update();
  }

  let tries = 0;
  (function waitAndDraw(){
    const root = gradioRoot();
    const canvas = locateCanvas(root);
    if (canvas) {
      canvasRef = canvas;
      try { draw(canvas, extractPoints(root)); } catch(e){ console.error("CIE draw error:", e); }
      observeDataframe(root, canvas);
      // programmatic APIs (kept stable)
      window.addPoints   = (pts) => { try { draw(canvasRef, Array.isArray(pts)? pts.slice(0,MAX_POINTS): []); } catch(e){} };
      window.clearPoints = ()   => { try { draw(canvasRef, []); } catch(e){} };
      return;
    }
    if (tries++ < MAX_RETRIES) {
      requestAnimationFrame(waitAndDraw);
    } else {
      console.warn("CIE canvas not found after waiting.");
    }
  })();
}
"""


# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Photometric extraction") as demo:
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
    btn = gr.Button("Submit")

#    meta = gr.Markdown()
    combined_summary_box = gr.Textbox(label="Summary", lines=14, show_copy_button=True)

    # CIE chart placed right BEFORE the original_text_box
    gr.HTML(CANVAS_HTML, elem_id="cie_box")

    original_text_box = gr.Textbox(label="Sphere PDF extraction", lines=10, show_copy_button=True)

    # NEW: show parsed CIE x,y matrix (参数, x, y)
    cct_xy_box = gr.Dataframe(
        label="CIE x,y (parsed from 光谱参数)",
        headers=["参数", "x", "y"],
        interactive=False,
        elem_id="cct_xy_df",  # <-- add this line
    )
    # Output order unchanged: meta, original_text_box, combined_summary_box
#    btn.click(handle_upload, inputs=inp, outputs=[meta, original_text_box, combined_summary_box])
#    btn.click(handle_upload, inputs=inp, outputs=[combined_summary_box])
    btn.click(handle_upload, inputs=inp, outputs=[combined_summary_box, cct_xy_box, original_text_box])

    # Run the JS after app loads (works in local and Spaces)
    demo.load(fn=lambda: None, inputs=[], outputs=[], js=JS_DRAW)

if __name__ == "__main__":
    demo.launch()
