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


# ----------------------------
# CIE 1931 画布 + 绘图脚本（从 cie.py 合并）
# ----------------------------
# HTML container with just a canvas (no form / no inputs)
CANVAS_HTML = """
<div style="padding:12px;background:#fff;border:1px solid #ddd;margin:20px">
  <h1 style="font-size:18px;margin:0 0 8px">ANSI C78.377-2015 chromaticity quadrangles on CIE 1931 (x,y)</h1>
  <canvas id="cie" width="900" height="600"
          style="max-width:100%;height:auto;border:1px solid #ddd;background:#fff">
    Canvas not supported.
  </canvas>
</div>
"""

# JavaScript that draws the chart on page load (no user-point logic)
JS_DRAW = r"""
() => {
  const c = document.getElementById("cie");
  if (!c) { console.warn("Canvas #cie not found"); return; }
  const ctx = c.getContext("2d");
  const W = c.width, H = c.height;

  // Plot window and padding
  const xmin=0.28, xmax=0.54, ymin=0.28, ymax=0.46, pad=60;
  const sx = x => pad + (x - xmin) * (W - 2*pad) / (xmax - xmin);
  const sy = y => H - pad - (y - ymin) * (H - 2*pad) / (ymax - ymin);
  const hexToRgba = (hex,a)=>{
    let h=hex.replace('#',''); if(h.length===3){h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];}
    const r=parseInt(h.slice(0,2),16), g=parseInt(h.slice(2,4),16), b=parseInt(h.slice(4,6),16);
    return `rgba(${r},${g},${b},${a})`;
  };

  // ANSI C78.377-2015 bins (centers + quadrangle corners)
  const bins=[
    {cct:2200, center:[0.5018,0.4153], corners:[[0.5259,0.4342],[0.5045,0.4344],[0.4799,0.3967],[0.4993,0.3967]]},
    {cct:2500, center:[0.4806,0.4141], corners:[[0.5045,0.4344],[0.4813,0.4319],[0.4593,0.3944],[0.4797,0.3967]]},
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

  // Planckian locus (xy approx)
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
    } else { // 4000–25000
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

  // Render
  drawAxes(); drawPlanck(); drawBins();
}
"""

with gr.Blocks(title="Photometric extraction") as demo:
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")

    btn = gr.Button("Submit")

    meta = gr.Markdown()
    combined_summary_box = gr.Textbox(label="Summary", lines=14, show_copy_button=True)

    # --- CIE plot: 放在 original_text_box 之前 ---
    gr.HTML(CANVAS_HTML)

    original_text_box   = gr.Textbox(label="Sphere PDF extraction", lines=10, show_copy_button=True)

    # 输出顺序保持不变：meta, original_text_box, combined_summary_box
    btn.click(handle_upload, inputs=inp, outputs=[meta, original_text_box, combined_summary_box])

    # 页面载入时绘图（不改变原有回调）
    demo.load(fn=None, inputs=None, outputs=None, js=JS_DRAW)

if __name__ == "__main__":
    demo.launch()
