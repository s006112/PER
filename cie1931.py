import json

# Constants
MAX_POINTS = 50
MAX_RETRIES = 200

# ANSI C78.377-2015 chromaticity bin data (single source of truth)
CIE_BINS = [
    {"cct": 2700, "center": [0.4578, 0.4101], "corners": [[0.4813, 0.4319], [0.4562, 0.4260], [0.4373, 0.3893], [0.4593, 0.3944]]},
    {"cct": 3000, "center": [0.4339, 0.4033], "corners": [[0.4562, 0.4260], [0.4303, 0.4173], [0.4150, 0.3821], [0.4373, 0.3893]]},
    {"cct": 3500, "center": [0.4078, 0.3930], "corners": [[0.4303, 0.4173], [0.4003, 0.4035], [0.3895, 0.3709], [0.4150, 0.3821]]},
    {"cct": 4000, "center": [0.3818, 0.3797], "corners": [[0.4003, 0.4035], [0.3737, 0.3880], [0.3671, 0.3583], [0.3895, 0.3709]]},
    {"cct": 4500, "center": [0.3613, 0.3670], "corners": [[0.3737, 0.3882], [0.3550, 0.3754], [0.3514, 0.3482], [0.3672, 0.3585]]},
    {"cct": 5000, "center": [0.3446, 0.3551], "corners": [[0.3550, 0.3753], [0.3375, 0.3619], [0.3366, 0.3373], [0.3515, 0.3481]]},
    {"cct": 5700, "center": [0.3287, 0.3425], "corners": [[0.3375, 0.3619], [0.3205, 0.3476], [0.3221, 0.3256], [0.3366, 0.3374]]},
    {"cct": 6500, "center": [0.3123, 0.3283], "corners": [[0.3205, 0.3477], [0.3026, 0.3311], [0.3067, 0.3119], [0.3221, 0.3255]]},
]

# Centralized Planck polynomial coefficients (single source of truth)
# x polynomials for ranges: [1667, 4000], (4000, 25000]
PLANCK_X = [
    # ax/T^3 + bx/T^2 + cx/T + dx
    {"range": (1667.0, 4000.0), "a": -0.2661239e9, "b": -0.2343580e6, "c": 0.8776956e3, "d": 0.179910},
    {"range": (4000.0, 25000.0), "a": -3.0258469e9, "b": 2.1070379e6, "c": 0.2226347e3, "d": 0.240390},
]
# y polynomials for ranges: [1667, 2222], (2222, 4000], (4000, 25000]
PLANCK_Y = [
    # ay*x^3 + by*x^2 + cy*x + dy (x is from PLANCK_X result)
    {"range": (1667.0, 2222.0), "a": -1.1063814,  "b": -1.34811020, "c": 2.18555832, "d": -0.20219683},
    {"range": (2222.0, 4000.0), "a": -0.9549476,  "b": -1.37418593, "c": 2.09137015, "d": -0.16748867},
    {"range": (4000.0, 25000.0), "a":  3.0817580,  "b": -5.87338670, "c": 3.75112997, "d": -0.37001483},
]

def get_canvas_html() -> str:
    """Get the CIE 1931 canvas HTML template."""
    return """
<div style="padding:12px;background:#fff;border:1px solid #ddd;margin:20px">
  <h1 style="font-size:18px;margin:0 0 8px">ANSI C78.377-2015 chromaticity quadrangles on CIE 1931 (x,y)</h1>
  <canvas id="cie" width="900" height="600"
          style="max-width:100%;height:auto;border:1px solid #ddd;background:#fff">
    Canvas not supported.
  </canvas>
</div>
"""

def get_drawing_javascript() -> str:
    """Get the complete CIE 1931 canvas drawing JavaScript code."""
    bins_json = json.dumps(CIE_BINS)
    # Inject centralized Planck coefficients to avoid duplicating formulas
    planck_x = json.dumps(PLANCK_X)
    planck_y = json.dumps(PLANCK_Y)
    return f"""
() => {{
  const MAX_RETRIES = {MAX_RETRIES};
  const MAX_POINTS = {MAX_POINTS};
  let prevSig = "";
  let bgCanvas = null;
  let canvasRef = null;
  const PLANCK_X = {planck_x};
  const PLANCK_Y = {planck_y};

  function gradioRoot() {{
    const ga = document.querySelector('gradio-app');
    return ga && ga.shadowRoot ? ga.shadowRoot : document;
  }}
  
  function locateCanvas(root) {{
    const host = root.getElementById("cie_box");
    const c = host ? host.querySelector("#cie") : root.getElementById("cie");
    return c || null;
  }}

  function extractPoints(root){{
    const host = root.getElementById("cct_xy_df");
    if(!host) return [];
    const seen = new Set();
    const pts = [];

    const rows = Array.from(host.querySelectorAll("tbody tr"));
    if (rows.length > 0){{
      for (const tr of rows){{
        const cells = tr.querySelectorAll("td,th");
        if (cells.length >= 3){{
          const label = (cells[0].textContent || "").trim();
          const x = parseFloat((cells[1].textContent || "").replace(/[^\\d.\\-]/g,""));
          const y = parseFloat((cells[2].textContent || "").replace(/[^\\d.\\-]/g,""));
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          const k = x.toFixed(4)+","+y.toFixed(4);
          if (seen.has(k)) continue;
          seen.add(k);
          pts.push([label, x, y]);
          if (pts.length >= MAX_POINTS) break;
        }}
      }}
      if (pts.length) return pts;
    }}

    const text = (host.textContent || "").trim();
    if (!text) return [];
    for (const line of text.split(/\\n+/)){{
      const m = line.match(/^\\s*(.+?)\\s*[,\\s]\\s*([+-]?\\d*\\.?\\d+)\\s*[,\\s]\\s*([+-]?\\d*\\.?\\d+)\\s*$/);
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
    }}
    return pts;
  }}

  function draw(canvas, points){{
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const xmin=0.28, xmax=0.50, ymin=0.30, ymax=0.44, pad=60;
    const sx = x => pad + (x - xmin) * (W - 2*pad) / (xmax - xmin);
    const sy = y => H - pad - (y - ymin) * (H - 2*pad) / (ymax - ymin);

    function hexToRgba(hex,a){{
      let h=hex.replace('#','');
      if(h.length===3){{h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];}}
      const r=parseInt(h.slice(0,2),16), g=parseInt(h.slice(2,4),16), b=parseInt(h.slice(4,6),16);
      return `rgba(${{r}},${{g}},${{b}},${{a}})`;
    }}

    const bins = {bins_json};

    function drawAxes(bg){{
      bg.clearRect(0,0,W,H);
      bg.fillStyle='#fff'; bg.fillRect(0,0,W,H);
      bg.strokeStyle='#000'; bg.lineWidth=1;
      bg.strokeRect(pad,pad,W-2*pad,H-2*pad);
      bg.font='12px sans-serif'; bg.fillStyle='#000';
      const step=0.02;
      for(let x=Math.ceil(xmin/step)*step; x<=xmax+1e-9; x+=step){{
        const X=sx(x);
        bg.strokeStyle='#e6e6e6'; bg.beginPath(); bg.moveTo(X, sy(ymin)); bg.lineTo(X, sy(ymax)); bg.stroke();
        bg.strokeStyle='#999'; bg.beginPath(); bg.moveTo(X, sy(ymin)); bg.lineTo(X, sy(ymin)-4); bg.stroke();
        bg.fillStyle='#000'; bg.fillText(x.toFixed(2), X-12, sy(ymin)+16);
      }}
      for(let y=Math.ceil(ymin/step)*step; y<=ymax+1e-9; y+=step){{
        const Y=sy(y);
        bg.strokeStyle='#e6e6e6'; bg.beginPath(); bg.moveTo(sx(xmin), Y); bg.lineTo(sx(xmax), Y); bg.stroke();
        bg.strokeStyle='#999'; bg.beginPath(); bg.moveTo(sx(xmin), Y); bg.lineTo(sx(xmin)-4, Y); bg.stroke();
        bg.fillStyle='#000'; bg.fillText(y.toFixed(2), sx(xmin)-40, Y+4);
      }}
      bg.fillStyle='#000';
      bg.fillText('x', sx(xmax)+10, sy(ymin)+4);
      bg.fillText('y', sx(xmin)-10, sy(ymax)-8);
    }}

    function planckXY(T){{
      const t = Number(T);
      if (!(t >= 1667 && t <= 25000)) return [NaN, NaN];
      const xr = PLANCK_X[0].range, xc0 = PLANCK_X[0], xc1 = PLANCK_X[1];
      const c = (t >= xr[0] && t <= xr[1]) ? xc0 : xc1;
      const x = (c.a/(t*t*t)) + (c.b/(t*t)) + (c.c/t) + c.d;
      const yr0 = PLANCK_Y[0].range, yr1 = PLANCK_Y[1].range;
      const yc = (t >= yr0[0] && t <= yr0[1]) ? PLANCK_Y[0]
               : (t > yr1[0] && t <= yr1[1]) ? PLANCK_Y[1]
               : PLANCK_Y[2];
      const y = yc.a*(x*x*x) + yc.b*(x*x) + yc.c*x + yc.d;
      return [x,y];
    }}

    function drawPlanck(bg){{
      bg.strokeStyle='#444'; bg.lineWidth=1.5;
      if (bg.setLineDash) bg.setLineDash([5,3]);
      bg.beginPath();
      let started=false;
      for(let T=2500; T<=7500; T+=50){{
        const [x,y]=planckXY(T); if (!isFinite(x)) continue;
        const X=sx(x), Y=sy(y);
        if(!started){{ bg.moveTo(X,Y); started=true; }} else {{ bg.lineTo(X,Y); }}
      }}
      bg.stroke();
      if (bg.setLineDash) bg.setLineDash([]);
    }}

    function drawBins(bg){{
      const palette=['#d81b60','#8e24aa','#3949ab','#1e88e5','#00897b','#43a047','#fdd835','#fb8c00','#e53935','#6d4c41','#7b1fa2'];
      for(let i=0;i<bins.length;i++){{
        const b=bins[i], color=palette[i%palette.length], P=b.corners;
        bg.lineWidth=2; bg.strokeStyle=color; bg.fillStyle=hexToRgba(color,0.16);
        bg.beginPath();
        bg.moveTo(sx(P[0][0]), sy(P[0][1]));
        for(let j=1;j<P.length;j++){{ bg.lineTo(sx(P[j][0]), sy(P[j][1])); }}
        bg.closePath(); bg.fill(); bg.stroke();
        const cx=sx(b.center[0]), cy=sy(b.center[1]);
        bg.fillStyle=color; bg.beginPath(); bg.arc(cx,cy,3,0,Math.PI*2); bg.fill();
        bg.font='12px sans-serif'; bg.fillText(b.cct+'K', cx-15, cy-6);
      }}
    }}

    function ensureBackground(){{
      if (bgCanvas) return bgCanvas;
      const oc = document.createElement('canvas'); oc.width=W; oc.height=H;
      const bg = oc.getContext('2d');
      drawAxes(bg); drawPlanck(bg); drawBins(bg);
      bgCanvas = oc;
      return bgCanvas;
    }}

    function drawPoints(points){{
      if (!Array.isArray(points) || !points.length) return;
      const size = 4;
      ctx.lineWidth = 1; ctx.strokeStyle = '#fff';
      for (const [_,x,y] of points){{
        if (x < xmin || x > xmax || y < ymin || y > ymax) continue;
        const X=sx(x), Y=sy(y);
        ctx.beginPath();
        ctx.moveTo(X - size, Y - size); ctx.lineTo(X + size, Y + size);
        ctx.moveTo(X - size, Y + size); ctx.lineTo(X + size, Y - size);
        ctx.stroke();
      }}
      ctx.lineWidth = 1; ctx.strokeStyle = '#000';
      for (const [_,x,y] of points){{
        if (x < xmin || x > xmax || y < ymin || y > ymax) continue;
        const X=sx(x), Y=sy(y);
        ctx.beginPath();
        ctx.moveTo(X - size, Y - size); ctx.lineTo(X + size, Y + size);
        ctx.moveTo(X - size, Y + size); ctx.lineTo(X + size, Y - size);
        ctx.stroke();
      }}
    }}

    ensureBackground();
    ctx.clearRect(0,0,W,H);
    ctx.drawImage(bgCanvas, 0, 0);
    if (Array.isArray(points) && points.length) drawPoints(points);
  }}

  function observeDataframe(root, canvas){{
    const host = root.getElementById("cct_xy_df");
    if (!host) return;
    let rafId = null;
    const update = () => {{
      const pts = extractPoints(root);
      const sig = JSON.stringify(pts);
      if (sig !== prevSig){{
        prevSig = sig;
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {{
          try {{ draw(canvas, pts); }} catch(e){{ console.error("CIE redraw error:", e); }}
        }});
      }}
    }};
    const mo = new MutationObserver(update);
    mo.observe(host, {{subtree:true, childList:true, characterData:true}});
    update();
  }}

  let tries = 0;
  (function waitAndDraw(){{
    const root = gradioRoot();
    const canvas = locateCanvas(root);
    if (canvas) {{
      canvasRef = canvas;
      try {{ draw(canvas, extractPoints(root)); }} catch(e){{ console.error("CIE draw error:", e); }}
      observeDataframe(root, canvas);
      window.addPoints = (pts) => {{ try {{ draw(canvasRef, Array.isArray(pts)? pts.slice(0,MAX_POINTS): []); }} catch(e){{}} }};
      window.clearPoints = () => {{ try {{ draw(canvasRef, []); }} catch(e){{}} }};
      return;
    }}
    if (tries++ < MAX_RETRIES) {{
      requestAnimationFrame(waitAndDraw);
    }} else {{
      console.warn("CIE canvas not found after waiting.");
    }}
  }})();
}}
"""


