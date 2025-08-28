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

def test_with_known_data():
    """Test function with known 3-point data to isolate the issue"""
    test_data = [
        ["POINT_A", 0.35, 0.35],
        ["POINT_B", 0.40, 0.40],
        ["POINT_C", 0.45, 0.42]
    ]

    test_summary = f"""## DEBUG: Test Data with {len(test_data)} Points

Testing with exactly 3 coordinate points (simplified):
- POINT_A: (0.35, 0.35) - Bottom left
- POINT_B: (0.40, 0.40) - Middle
- POINT_C: (0.45, 0.42) - Top right

All coordinates are within plotting range (x: 0.28-0.50, y: 0.30-0.44).
If only 1 point shows, the issue is in JavaScript extraction/rendering.

**Check the dataframe below - it should show 3 rows of data.**
"""

    print(f"PYTHON DEBUG: Returning {len(test_data)} points to Gradio")
    for i, point in enumerate(test_data):
        print(f"PYTHON DEBUG: Point {i}: {point}")

    return test_summary, test_data, "Test content"

def handle_upload(file_path: str):
    """
    Gradio callback: read file path, parse PDF, call OpenAI with two prompts
    Returns: combined_summary (str), cct_xy (list[list])  # [[参数, x, y], ...]
    """
    # TEMPORARY: Use test data to isolate the issue
    return test_with_known_data()





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
  console.log("🚀 JAVASCRIPT STARTED - CIE Chart JS is executing!");

  // IMMEDIATE VISUAL TEST - Draw something on any canvas found
  const testCanvas = document.querySelector('canvas');
  if (testCanvas) {
    console.log("✓ Found canvas for immediate test");
    const testCtx = testCanvas.getContext('2d');
    testCtx.fillStyle = '#00ff00';
    testCtx.fillRect(5, 5, 200, 30);
    testCtx.fillStyle = '#000';
    testCtx.font = '14px Arial';
    testCtx.fillText('JS EXECUTING!', 10, 25);
  } else {
    console.log("✗ No canvas found for immediate test");
  }

  const MAX_RETRIES = 200;
  let prevSig = ""; // avoid redundant redraws

  function gradioRoot() {
    const ga = document.querySelector('gradio-app');
    return ga && ga.shadowRoot ? ga.shadowRoot : document;
  }

  function locateCanvas(root) {
    const host = root.getElementById("cie_box");
    const c = host ? host.querySelector("#cie") : root.getElementById("cie");
    return c || null;
  }

  // NEW: extract [[param, x, y], ...] from the cct_xy Dataframe table
  function extractPoints(root){
    console.log("=== EXTRACT POINTS SIMPLE VERSION ===");

    // SIMPLE TEST: Return hardcoded test data to verify rendering works
    console.log("Returning hardcoded test data for debugging");
    const testPoints = [
      ["POINT_A", 0.35, 0.35],
      ["POINT_B", 0.40, 0.40],
      ["POINT_C", 0.45, 0.42]
    ];
    console.log("Test points:", testPoints);
    return testPoints;
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

    function drawAxes(){
      ctx.clearRect(0,0,W,H);
      ctx.fillStyle='#fff'; ctx.fillRect(0,0,W,H);
      ctx.strokeStyle='#000'; ctx.lineWidth=1;
      ctx.strokeRect(pad,pad,W-2*pad,H-2*pad);

      ctx.font='12px sans-serif'; ctx.fillStyle='#000';
      const step=0.02;
      for(let x=Math.ceil(xmin/step)*step; x<=xmax+1e-9; x+=step){
        const X=sx(x);
        ctx.strokeStyle='#e6e6e6'; ctx.beginPath(); ctx.moveTo(X, sy(ymin)); ctx.lineTo(X, sy(ymax)); ctx.stroke();
        ctx.strokeStyle='#999'; ctx.beginPath(); ctx.moveTo(X, sy(ymin)); ctx.lineTo(X, sy(ymin)-4); ctx.stroke();
        ctx.fillStyle='#000'; ctx.fillText(x.toFixed(2), X-12, sy(ymin)+16);
      }
      for(let y=Math.ceil(ymin/step)*step; y<=ymax+1e-9; y+=step){
        const Y=sy(y);
        ctx.strokeStyle='#e6e6e6'; ctx.beginPath(); ctx.moveTo(sx(xmin), Y); ctx.lineTo(sx(xmax), Y); ctx.stroke();
        ctx.strokeStyle='#999'; ctx.beginPath(); ctx.moveTo(sx(xmin), Y); ctx.lineTo(sx(xmin)-4, Y); ctx.stroke();
        ctx.fillStyle='#000'; ctx.fillText(y.toFixed(2), sx(xmin)-40, Y+4);
      }
      ctx.fillStyle='#000';
      ctx.fillText('x', sx(xmax)+10, sy(ymin)+4);
      ctx.fillText('y', sx(xmin)-10, sy(ymax)-8);
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

    function drawPlanck(){
      ctx.strokeStyle='#444'; ctx.lineWidth=1.5;
      if (ctx.setLineDash) ctx.setLineDash([5,3]);
      ctx.beginPath();
      let started=false;
      for(let T=2000; T<=10000; T+=50){
        const [x,y]=planckXY(T); if (!isFinite(x)) continue;
        const X=sx(x), Y=sy(y);
        if(!started){ ctx.moveTo(X,Y); started=true; } else { ctx.lineTo(X,Y); }
      }
      ctx.stroke();
      if (ctx.setLineDash) ctx.setLineDash([]);
    }

    function drawBins(){
      const palette=['#d81b60','#8e24aa','#3949ab','#1e88e5','#00897b','#43a047',
                     '#fdd835','#fb8c00','#e53935','#6d4c41','#7b1fa2'];
      for(let i=0;i<bins.length;i++){
        const b=bins[i], color=palette[i%palette.length], P=b.corners;
        ctx.lineWidth=2; ctx.strokeStyle=color; ctx.fillStyle=hexToRgba(color,0.16);
        ctx.beginPath();
        ctx.moveTo(sx(P[0][0]), sy(P[0][1]));
        for(let j=1;j<P.length;j++){ ctx.lineTo(sx(P[j][0]), sy(P[j][1])); }
        ctx.closePath(); ctx.fill(); ctx.stroke();

        const cx=sx(b.center[0]), cy=sy(b.center[1]);
        ctx.fillStyle=color; ctx.beginPath(); ctx.arc(cx,cy,3,0,Math.PI*2); ctx.fill();
        ctx.font='12px sans-serif'; ctx.fillText(b.cct+'K', cx+6, cy-6);
      }
    }

    // NEW: draw points as cross symbols "╳"
    function drawPoints(points){
      console.log("=== DRAW POINTS DEBUG START ===");
      console.log("drawPoints called with:", points);

      if (!Array.isArray(points)) {
        console.log("ERROR: points is not an array:", typeof points);
        return;
      }

      if (!points.length) {
        console.log("ERROR: points array is empty");
        return;
      }

      console.log(`✓ Drawing ${points.length} points`);

      const size = 6;
      ctx.strokeStyle = '#000';
      ctx.lineWidth = 2;

      for (let i = 0; i < points.length; i++){
        const p = points[i];
        console.log(`Point ${i}:`, p);

        const x_coord = p[1];
        const y_coord = p[2];
        console.log(`Point ${i} coordinates: x=${x_coord}, y=${y_coord}`);

        const X = sx(x_coord);
        const Y = sy(y_coord);
        console.log(`Point ${i} screen coords: X=${X}, Y=${Y}`);

        if (!isFinite(X) || !isFinite(Y)) {
          console.log(`✗ Point ${i} skipped - non-finite screen coordinates`);
          continue;
        }

        console.log(`✓ Drawing point ${i} at screen (${X}, ${Y})`);
        ctx.beginPath();
        ctx.moveTo(X - size, Y - size);
        ctx.lineTo(X + size, Y + size);
        ctx.moveTo(X - size, Y + size);
        ctx.lineTo(X + size, Y - size);
        ctx.stroke();
      }

      console.log("=== DRAW POINTS DEBUG END ===");
    }

    console.log("=== DRAW FUNCTION DEBUG ===");
    console.log("draw() called with points:", points);

    // IMMEDIATE VISUAL TEST - Green indicator that draw() is called
    ctx.fillStyle = '#00ff00';
    ctx.fillRect(canvas.width - 150, 5, 140, 25);
    ctx.fillStyle = '#000';
    ctx.font = '12px Arial';
    ctx.fillText('DRAW() CALLED!', canvas.width - 145, 20);

    drawAxes(); drawPlanck(); drawBins();

    // Add comprehensive debug info to canvas
    ctx.fillStyle = '#ff0000';
    ctx.font = '10px Arial';
    let debugY = 15;

    if (Array.isArray(points)) {
      console.log("✓ Points is array, calling drawPoints");
      ctx.fillText(`DEBUG: ${points.length} points received`, 10, debugY);
      debugY += 12;

      // Show detailed coordinate info
      for (let i = 0; i < Math.min(points.length, 5); i++) {
        const p = points[i];
        ctx.fillText(`${i}: [${p[0]}, ${p[1]}, ${p[2]}]`, 10, debugY);
        debugY += 12;

        // Check for duplicate coordinates
        if (i > 0) {
          const prev = points[i-1];
          if (p[1] === prev[1] && p[2] === prev[2]) {
            ctx.fillStyle = '#ff0000';
            ctx.fillText(`⚠️ DUPLICATE COORDS!`, 200, debugY - 12);
            ctx.fillStyle = '#ff0000';
          }
        }
      }

      // Show unique coordinate count
      const uniqueCoords = new Set(points.map(p => `${p[1]},${p[2]}`));
      ctx.fillText(`Unique coords: ${uniqueCoords.size}/${points.length}`, 10, debugY);
      debugY += 12;

      if (uniqueCoords.size < points.length) {
        ctx.fillStyle = '#ff0000';
        ctx.fillText(`ERROR: Coordinate extraction failed!`, 10, debugY);
        ctx.fillStyle = '#ff0000';
      }

      drawPoints(points); // <- overlay
    } else {
      console.log("✗ Points is not array:", typeof points, points);
      ctx.fillText(`DEBUG: Points not array: ${typeof points}`, 10, debugY);
    }

    console.log("=== DRAW FUNCTION END ===");
  }

  // NEW: observe dataframe changes and redraw when cct_xy updates
  function observeDataframe(root, canvas){
    const host = root.getElementById("cct_xy_df");
    if (!host) return;

    let retryCount = 0;
    const maxRetries = 10;

    const update = () => {
      console.log("=== UPDATE FUNCTION DEBUG ===");
      console.log("update() called, retry:", retryCount);

      // Progressive delay - more time for later retries
      const delay = Math.min(100 + (retryCount * 100), 1000);

      setTimeout(() => {
        const pts = extractPoints(root);
        console.log("extractPoints returned:", pts);

        const sig = JSON.stringify(pts);
        console.log("New signature:", sig);
        console.log("Previous signature:", prevSig);

        // If we got no points and haven't retried much, try again
        if (pts.length === 0 && retryCount < maxRetries) {
          retryCount++;
          console.log(`No points found, scheduling retry ${retryCount}/${maxRetries}`);
          setTimeout(update, 500);
          return;
        }

        // If we got fewer points than expected, also retry
        if (pts.length === 1 && retryCount < maxRetries) {
          retryCount++;
          console.log(`Only 1 point found, scheduling retry ${retryCount}/${maxRetries}`);
          setTimeout(update, 500);
          return;
        }

        if (sig !== prevSig){
          console.log("✓ Signature changed, redrawing");
          prevSig = sig;
          retryCount = 0; // Reset retry count on successful update
          try {
            draw(canvas, pts);
          } catch(e){
            console.error("CIE redraw error:", e);
          }
        } else {
          console.log("✗ Signature unchanged, skipping redraw");
        }

        console.log("=== UPDATE FUNCTION END ===");
      }, delay);
    };

    const mo = new MutationObserver(update);
    mo.observe(host, {subtree:true, childList:true, characterData:true});
    update(); // initial
  }

  let tries = 0;
  (function waitAndDraw(){
    console.log("DEBUG: waitAndDraw() called");
    const root = gradioRoot();
    const canvas = locateCanvas(root);
    if (canvas) {
      console.log("Canvas found, setting up initial draw and observer");

      // Initial draw with delay
      setTimeout(() => {
        console.log("Performing delayed initial draw");
        try {
          const initialPoints = extractPoints(root);
          draw(canvas, initialPoints);
        } catch(e){
          console.error("CIE initial draw error:", e);
        }
      }, 500); // 500ms delay for initial draw

      observeDataframe(root, canvas);
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
        test_btn = gr.Button("🔬 Test with 3 Points", variant="secondary")
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
    test_btn.click(test_with_known_data, inputs=[], outputs=[combined_summary_box, cct_xy_box, original_text_box])

    # Run the JS after app loads (works in local and Spaces)
    demo.load(fn=lambda: None, inputs=[], outputs=[], js=JS_DRAW)

    # Auto-load test data on startup for debugging
    demo.load(fn=test_with_known_data, inputs=[], outputs=[combined_summary_box, cct_xy_box, original_text_box])

if __name__ == "__main__":
    demo.launch()
