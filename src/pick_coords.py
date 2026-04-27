#!/usr/bin/env python3
"""
Interactive coordinate picker — screenshots the iPhone then opens in your browser.
Hover to see tap.py coordinates (pixels ÷ scale already done).
Click to log the tap.py command and fill the X/Y coord boxes.
Press Space while hovering to tap the iPhone and auto-refresh the screenshot.
Press R or the Refresh button to re-take the screenshot without restarting.

Usage:
    python3 pick_coords.py               # screenshot + open picker
    python3 pick_coords.py --img my.png  # skip screenshot, use existing file
"""

import argparse
import base64
import http.server
import io
import json
import sys
import threading
import webbrowser
from urllib.parse import urlparse, parse_qs

import requests
from PIL import Image

from _session import WDA_URL, get_or_create_session
from gestures.tap import tap
from screenshot import take_screenshot

SCALE   = 3    # iPhone 14 Pro Max: 3× screen (pixels ÷ 3 = logical points)
PORT    = 9877


def screenshot_to_b64(img_bytes: bytes) -> tuple[str, int, int]:
    """Return (base64-png, px_w, px_h)."""
    img = Image.open(io.BytesIO(img_bytes))
    px_w, px_h = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), px_w, px_h


# ── HTTP handler ──────────────────────────────────────────────────────────────

html_template = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>iPhone Coordinate Picker</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: #1e1e1e; color: #eee; font-family: monospace;
    display: flex; height: 100vh; overflow: hidden;
  }}
  /* ── left panel: image ── */
  #left {{
    flex: 1; min-width: 0; overflow: auto; padding: 16px;
    display: flex; align-items: flex-start; justify-content: center;
  }}
  #wrap {{ position: relative; display: inline-block; cursor: crosshair; max-width: 100%; }}
  #wrap img {{ display: block; max-width: 100%; max-height: calc(100vh - 32px); width: auto; height: auto; }}
  #crossH, #crossV {{
    position: absolute; pointer-events: none;
    background: rgba(255, 80, 80, 0.7);
  }}
  #crossH {{ height: 1px; left: 0; right: 0; }}
  #crossV {{ width:  1px; top:  0; bottom: 0; }}
  /* ── right panel: controls + log ── */
  #right {{
    width: 320px; flex-shrink: 0;
    display: flex; flex-direction: column;
    background: #111; border-left: 1px solid #444;
    height: 100vh; overflow: hidden;
  }}
  #bar {{
    padding: 14px 16px;
    border-bottom: 1px solid #333;
    display: flex; flex-direction: column; gap: 10px;
  }}
  #tap  {{ color: #4ec9b0; font-size: 14px; font-weight: bold; word-break: break-all; }}
  #px   {{ color: #888; font-size: 12px; }}
  .bar-row {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
  .bar-lbl {{ color: #888; font-size: 12px; }}
  #coordx, #coordy {{
    width: 56px; background: #2a2a2a; border: 1px solid #555; color: #ccc;
    padding: 3px 6px; font-family: monospace; font-size: 13px; border-radius: 3px;
  }}
  button {{
    background: #2a2a2a; border: 1px solid #555; color: #ccc;
    padding: 4px 10px; font-family: monospace; font-size: 13px;
    cursor: pointer; border-radius: 3px;
  }}
  button:hover {{ background: #333; color: #fff; }}
  #refbtn.spinning {{ color: #4ec9b0; border-color: #4ec9b0; }}
  #goerr {{ color: #c44; font-size: 11px; min-height: 14px; }}
  #hint  {{ color: #555; font-size: 11px; }}
  /* ── log ── */
  #log {{
    flex: 1; overflow-y: auto;
    padding: 10px 14px; font-size: 13px; color: #aaa;
    border-top: 1px solid #333;
  }}
  #log div {{ margin: 3px 0; }}
  #log .cmd {{ color: #4ec9b0; }}
</style>
</head>
<body>
<div id="left">
  <div id="wrap">
    <img id="img" src="data:image/png;base64,{b64}" draggable="false">
    <div id="crossH"></div>
    <div id="crossV"></div>
  </div>
</div>
<div id="right">
  <div id="bar">
    <div id="tap">Hover over the image</div>
    <div id="px"></div>
    <div class="bar-row">
      <span class="bar-lbl">X</span>
      <input id="coordx" type="text" placeholder="x">
      <span class="bar-lbl">Y</span>
      <input id="coordy" type="text" placeholder="y">
      <button id="gobtn">Go</button>
      <button id="copybtn">Copy</button>
    </div>
    <div id="goerr"></div>
    <div class="bar-row">
      <button id="refbtn" title="Re-take screenshot (R)">&#x21bb; Refresh</button>
      <div id="hint">image {px_w}×{px_h}px · scale {SCALE}× · Space to tap</div>
    </div>
  </div>
  <div id="log"><div>Click log — commands are also printed in the terminal:</div></div>
</div>
<script>
const SCALE  = {SCALE};
let PX_W     = {px_w};
let PX_H     = {px_h};
const wrap   = document.getElementById('wrap');
const imgEl  = document.getElementById('img');
const crossH = document.getElementById('crossH');
const crossV = document.getElementById('crossV');
const tapEl  = document.getElementById('tap');
const pxEl   = document.getElementById('px');
const logEl  = document.getElementById('log');
const refBtn = document.getElementById('refbtn');
const hint   = document.getElementById('hint');
const coordX  = document.getElementById('coordx');
const coordY  = document.getElementById('coordy');
const gobtn   = document.getElementById('gobtn');
const copybtn = document.getElementById('copybtn');
const goerr   = document.getElementById('goerr');

let lastTx = null, lastTy = null;

function getCoords(e) {{
  const r     = imgEl.getBoundingClientRect();
  const dispX = e.clientX - r.left;
  const dispY = e.clientY - r.top;
  const px    = Math.round(dispX * (PX_W / r.width));
  const py    = Math.round(dispY * (PX_H / r.height));
  const tx    = Math.floor(px / SCALE);
  const ty    = Math.floor(py / SCALE);
  return {{dispX, dispY, px, py, tx, ty}};
}}

function logEntry(html) {{
  const entry = document.createElement('div');
  entry.innerHTML = html;
  logEl.appendChild(entry);
  logEl.scrollTop = logEl.scrollHeight;
}}

function moveCrosshair(dispX, dispY) {{
  crossH.style.top  = dispY + 'px';
  crossV.style.left = dispX + 'px';
}}

function updateLabels(tx, ty, px, py) {{
  tapEl.textContent = `python3 src/tap.py --x ${{tx}} --y ${{ty}}`;
  pxEl.textContent  = `pixel (${{px}}, ${{py}})`;
}}

wrap.addEventListener('mousemove', e => {{
  const {{dispX, dispY, px, py, tx, ty}} = getCoords(e);
  lastTx = tx; lastTy = ty;
  moveCrosshair(dispX, dispY);
  updateLabels(tx, ty, px, py);
}});

wrap.addEventListener('click', e => {{
  const {{px, py, tx, ty}} = getCoords(e);
  coordX.value = tx;
  coordY.value = ty;
  goerr.textContent = '';
  logEntry(`<span class="cmd">python3 src/tap.py --x ${{tx}} --y ${{ty}}</span>  <span style="color:#555">· pixel (${{px}}, ${{py}})</span>`);
  fetch('/click?px=' + px + '&py=' + py + '&tx=' + tx + '&ty=' + ty);
}});

async function doRefresh() {{
  refBtn.textContent = '⟳ refreshing…';
  refBtn.classList.add('spinning');
  refBtn.disabled = true;
  try {{
    const res  = await fetch('/refresh');
    const data = await res.json();
    imgEl.src = 'data:image/png;base64,' + data.b64;
    PX_W = data.px_w;
    PX_H = data.px_h;
    hint.textContent = `image ${{data.px_w}}×${{data.px_h}}px · scale {SCALE}× · Space to tap`;
    logEntry('<span style="color:#888">— screenshot refreshed —</span>');
  }} catch(e) {{
    logEntry('<span style="color:#c44">refresh failed: ' + e + '</span>');
  }}
  refBtn.textContent = '↻ Refresh';
  refBtn.classList.remove('spinning');
  refBtn.disabled = false;
}}

refBtn.addEventListener('click', doRefresh);

document.addEventListener('keydown', e => {{
  if (e.target === coordX || e.target === coordY) return;
  if (e.key === 'r' || e.key === 'R') doRefresh();
  if (e.key === ' ' && !e.repeat) {{
    e.preventDefault();
    if (lastTx === null) {{
      logEntry('<span style="color:#888">Space pressed — hover over image first</span>');
      return;
    }}
    const tx = lastTx, ty = lastTy;
    fetch('/tap?tx=' + tx + '&ty=' + ty)
      .then(r => r.json())
      .then(data => {{
        if (data.ok) {{
          logEntry('<span style="color:#4ec9b0">tapped (' + tx + ', ' + ty + ')</span>');
          doRefresh();
        }} else {{
          logEntry('<span style="color:#c44">tap failed: ' + (data.error || 'unknown') + '</span>');
        }}
      }})
      .catch(err => {{
        logEntry('<span style="color:#c44">tap failed: ' + err + '</span>');
      }});
  }}
}});

const intRe = /^\\s*-?\\d+\\s*$/;

function goToCoord() {{
  const xVal = coordX.value.trim();
  const yVal = coordY.value.trim();
  if (!intRe.test(xVal) || !intRe.test(yVal)) {{
    goerr.textContent = 'Enter integers in both fields';
    return;
  }}
  const tx = parseInt(xVal, 10);
  const ty = parseInt(yVal, 10);
  const maxTx = Math.floor(PX_W / SCALE);
  const maxTy = Math.floor(PX_H / SCALE);
  if (tx < 0 || tx >= maxTx || ty < 0 || ty >= maxTy) {{
    goerr.textContent = 'Out of range (0–' + (maxTx - 1) + ', 0–' + (maxTy - 1) + ')';
    return;
  }}
  goerr.textContent = '';
  const px    = tx * SCALE;
  const py    = ty * SCALE;
  const r     = imgEl.getBoundingClientRect();
  const dispX = px * (r.width  / PX_W);
  const dispY = py * (r.height / PX_H);
  moveCrosshair(dispX, dispY);
  updateLabels(tx, ty, px, py);
  lastTx = tx; lastTy = ty;
}}

gobtn.addEventListener('click', goToCoord);
coordX.addEventListener('keydown', e => {{ if (e.key === 'Enter') goToCoord(); }});
coordY.addEventListener('keydown', e => {{ if (e.key === 'Enter') goToCoord(); }});

copybtn.addEventListener('click', () => {{
  if (lastTx === null) {{
    copybtn.textContent = 'No pos';
    setTimeout(() => {{ copybtn.textContent = 'Copy'; }}, 1200);
    return;
  }}
  navigator.clipboard.writeText('--x ' + lastTx + ' --y ' + lastTy)
    .then(() => {{ copybtn.textContent = 'Copied!'; setTimeout(() => {{ copybtn.textContent = 'Copy'; }}, 1200); }})
    .catch(() => {{ copybtn.textContent = 'Failed';  setTimeout(() => {{ copybtn.textContent = 'Copy'; }}, 1200); }});
}});
</script>
</body>
</html>"""


def build_html(state: dict) -> str:
    """Render the picker HTML with the current image state."""
    return html_template.format(
        b64=state["b64"], px_w=state["px_w"], px_h=state["px_h"], SCALE=SCALE)


def make_handler(state: dict, args) -> type:
    """Return a configured BaseHTTPRequestHandler subclass for the coord picker."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass  # silence access log

        def do_GET(self):
            if self.path.startswith("/click"):
                q  = parse_qs(urlparse(self.path).query)
                tx = q.get("tx", ["?"])[0]
                ty = q.get("ty", ["?"])[0]
                px = q.get("px", ["?"])[0]
                py = q.get("py", ["?"])[0]
                print(f"  python3 src/tap.py --x {tx} --y {ty}   (pixel {px}, {py})")
                self.send_response(200)
                self.end_headers()

            elif self.path.startswith("/tap"):
                q = parse_qs(urlparse(self.path).query)
                try:
                    tx = int(q["tx"][0])
                    ty = int(q["ty"][0])
                except (KeyError, ValueError, IndexError):
                    payload = json.dumps({"ok": False, "error": "tx and ty must be integers"})
                else:
                    try:
                        session_id = get_or_create_session()
                        tap(session_id, tx, ty)
                        payload = json.dumps({"ok": True})
                    except Exception as e:
                        payload = json.dumps({"ok": False, "error": str(e)})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            elif self.path == "/refresh":
                print("  Refreshing screenshot...")
                try:
                    if args.img:
                        with open(args.img, "rb") as f:
                            new_bytes = f.read()
                    else:
                        new_bytes = take_screenshot()
                    new_b64, new_w, new_h = screenshot_to_b64(new_bytes)
                    state["b64"]  = new_b64
                    state["px_w"] = new_w
                    state["px_h"] = new_h
                    print(f"  Refreshed  ({len(new_bytes) // 1024} KB)")
                    payload = json.dumps({"b64": new_b64, "px_w": new_w, "px_h": new_h})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(payload.encode())
                except Exception as e:
                    print(f"  Refresh failed: {e}")
                    self.send_response(500)
                    self.end_headers()

            else:
                body = build_html(state).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

    return Handler


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Screenshot iPhone and pick tap coordinates interactively.")
    parser.add_argument("--img", default=None,
                        help="Use an existing PNG instead of taking a new screenshot")
    args = parser.parse_args()

    if args.img:
        with open(args.img, "rb") as f:
            img_bytes = f.read()
        print(f"Loaded: {args.img}")
    else:
        try:
            requests.get(f"{WDA_URL}/status", timeout=3)
        except Exception:
            print("ERROR: WDA not reachable. Run ./start_wda.sh first.", file=sys.stderr)
            sys.exit(1)
        print("Taking screenshot...")
        img_bytes = take_screenshot()
        print(f"Screenshot taken  ({len(img_bytes) // 1024} KB)")

    b64, px_w, px_h = screenshot_to_b64(img_bytes)
    # Mutable container so the handler closure can update it
    state = {"b64": b64, "px_w": px_w, "px_h": px_h}

    pt_w, pt_h = px_w // SCALE, px_h // SCALE
    print(f"Size: {px_w}×{px_h} px  →  {pt_w}×{pt_h} pts  (scale {SCALE}×)")
    print(f"Opening picker at http://127.0.0.1:{PORT}")
    print("Hover to preview · click to log + fill coords · Space to tap · R to refresh · Ctrl+C to quit\n")

    server = http.server.HTTPServer(("127.0.0.1", PORT), make_handler(state, args))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webbrowser.open(f"http://127.0.0.1:{PORT}")

    print("Press Ctrl+C to stop.\n")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    server.shutdown()


if __name__ == "__main__":
    main()
