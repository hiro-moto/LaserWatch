from __future__ import annotations
import html, math
from pathlib import Path

def _fmt(v,digits=4):
    if v is None: return '-'
    try:
        x=float(v)
        return f'{x:.{digits}g}' if math.isfinite(x) else '-'
    except Exception:
        return html.escape(str(v))

def _downsample(x,y,max_points=1000):
    n=min(len(x),len(y))
    if n<=max_points: return list(x[:n]),list(y[:n])
    step=max(1,math.ceil(n/max_points)); return list(x[:n:step]),list(y[:n:step])

def _svg(x,series,width=800,height=220):
    valid=[]
    for name,y in series:
        xx,yy=_downsample(x,y); pts=[]
        for a,b in zip(xx,yy):
            try: a=float(a); b=float(b)
            except Exception: continue
            if math.isfinite(a) and math.isfinite(b): pts.append((a,b))
        if pts: valid.append((name,pts))
    if not valid: return '<div class="empty">No plot data</div>'
    ax=[p[0] for _,pts in valid for p in pts]; ay=[p[1] for _,pts in valid for p in pts]
    xmin,xmax=min(ax),max(ax); ymin,ymax=min(ay),max(ay)
    if xmax<=xmin: xmax=xmin+1.0
    if ymax<=ymin: ymax=ymin+1.0
    pad=32; pw=width-2*pad; ph=height-2*pad
    def sx(v): return pad+(v-xmin)/(xmax-xmin)*pw
    def sy(v): return pad+(ymax-v)/(ymax-ymin)*ph
    colors=['#0b63ce','#d94801','#238b45','#7a0177']
    chunks=[f'<svg viewBox="0 0 {width} {height}">',f'<rect width="{width}" height="{height}" fill="white"/>',f'<rect x="{pad}" y="{pad}" width="{pw}" height="{ph}" fill="none" stroke="#bbb"/>']
    for i,(name,pts) in enumerate(valid):
        c=colors[i%len(colors)]; coords=' '.join(f'{sx(a):.2f},{sy(b):.2f}' for a,b in pts)
        chunks.append(f'<polyline points="{coords}" fill="none" stroke="{c}" stroke-width="1.5"/>')
        chunks.append(f'<text x="{pad+8+150*i}" y="18" font-size="12" fill="{c}">{html.escape(name)}</text>')
    chunks.append('</svg>'); return ''.join(chunks)

def build_html_report(summary,arrays=None,psd=None):
    arrays=arrays or {}; psd=psd or {}; camera=summary.get('camera') or {}; stats=summary.get('statistics') or {}; rec=summary.get('recording') or {}; ref=summary.get('reference') or {}
    def table(d): return ''.join(f'<tr><th>{html.escape(str(k))}</th><td>{_fmt(v,6)}</td></tr>' for k,v in d.items())
    psvg=_svg(arrays.get('t',[]),[('X [um]',arrays.get('x',[])),('Y [um]',arrays.get('y',[]))])
    ssvg=_svg(arrays.get('t',[]),[('D4sigma X [um]',arrays.get('d4x',[])),('D4sigma Y [um]',arrays.get('d4y',[]))])
    isvg=_svg(arrays.get('t',[]),[('Integrated',arrays.get('intensity',[]))])
    fsvg=_svg(psd.get('f',[]),[('PSD X',psd.get('psd_x',[])),('PSD Y',psd.get('psd_y',[]))])
    return '''<!doctype html><html><head><meta charset="utf-8"><title>LaserWatch Measurement Report</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#222}table{border-collapse:collapse;width:100%;max-width:900px;margin-bottom:24px}th,td{border:1px solid #ddd;padding:6px 9px;text-align:left}th{background:#f5f5f5;width:38%}svg{width:100%;max-width:900px;border:1px solid #eee}.small{color:#666;font-size:.9em}.empty{padding:18px;background:#f5f5f5;color:#777}</style></head><body>''' + \
        f'<h1>LaserWatch Measurement Report</h1><p class="small">Generated: {html.escape(str(summary.get("generated_at","")))}</p>' + \
        f'<h2>Camera</h2><table>{table(camera)}</table><h2>Session statistics</h2><table>{table(stats)}</table>' + \
        f'<h2>Reference</h2><table>{table(ref) if ref else "<tr><td>Not set</td></tr>"}</table>' + \
        f'<h2>Recording</h2><table>{table(rec)}</table><h2>Pointing history</h2>{psvg}<h2>Beam-size history</h2>{ssvg}' + \
        f'<h2>Intensity history</h2>{isvg}<h2>Pointing PSD</h2>{fsvg}' + \
        '<p class="small">Statistics cover the full logging session using online statistics. Plot histories may be downsampled or bounded.</p></body></html>'

def write_html_report(path,summary,arrays=None,psd=None):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(build_html_report(summary,arrays,psd),encoding='utf-8'); return path
