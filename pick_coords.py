#!/usr/bin/env python3
"""
Interactive coordinate picker — screenshots the iPhone then opens in your browser.
Hover to see tap.py coordinates (pixels ÷ scale already done).
Click to print the ready-to-run tap.py command.
Press the Refresh button (or R) to re-take the screenshot without restarting.

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

WDA_URL = "http://127.0.0.1:8100"
SCALE   = 3    # iPhone 14 Pro Max: 3× screen (pixels ÷ 3 = logical points)
PORT    = 9877


# ── WDA helpers ───────────────────────────────────────────────────────────────

def get_session() -> str:
    payload = {"capabilities": {"alwaysMatch": {}}, "desiredCapabilities": {}}
    r = requests.post(f"{WDA_URL}/session", json=payload, timeout=10)
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"Could not create WDA session: {data}")
    return sid


def take_screenshot() -> bytes:
    sid = get_session()
    r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=10)
    return base64.b64decode(r.json()["value"])


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
  body {{ margin: 0; background: #1e1e1e; color: #eee; font-family: monospace; }}
  #bar {{
    position: fixed; top: 0; left: 0; right: 0;
    background: #111; padding: 10px 16px; font-size: 15px;
    z-index: 10; border-bottom: 1px solid #444;
    display: flex; align-items: center; gap: 24px; flex-wrap: wrap;
  }}
  #tap    {{ color: #4ec9b0; font-size: 17px; font-weight: bold; }}
  #px     {{ color: #888; font-size: 13px; }}
  #hint   {{ color: #555; font-size: 12px; margin-left: auto; }}
  #refbtn {{
    background: #2a2a2a; border: 1px solid #555; color: #ccc;
    padding: 4px 12px; font-family: monospace; font-size: 13px;
    cursor: pointer; border-radius: 3px;
  }}
  #refbtn:hover {{ background: #333; color: #fff; }}
  #refbtn.spinning {{ color: #4ec9b0; border-color: #4ec9b0; }}
  #wrap {{ margin-top: 52px; position: relative; display: inline-block; cursor: crosshair; }}
  img   {{ display: block; max-width: 100vw; }}
  #crossH, #crossV {{
    position: absolute; pointer-events: none;
    background: rgba(255, 80, 80, 0.7);
  }}
  #crossH {{ height: 1px; left: 0; right: 0; }}
  #crossV {{ width:  1px; top:  0; bottom: 0; }}
  #log {{
    background: #111; padding: 12px 16px; font-size: 13px; color: #aaa;
    border-top: 1px solid #333; min-height: 80px;
  }}
  #log div {{ margin: 3px 0; }}
  #log .cmd {{ color: #4ec9b0; }}
</style>
</head>
<body>
<div id="bar">
  <div id="tap">Hover over the image</div>
  <div id="px"></div>
  <button id="refbtn" title="Re-take screenshot (R)">&#x21bb; Refresh</button>
  <div id="hint">image {px_w}×{px_h}px · scale {SCALE}× · click to lock</div>
</div>
<div id="wrap">
  <img id="img" src="data:image/png;base64,{b64}" draggable="false">
  <div id="crossH"></div>
  <div id="crossV"></div>
</div>
<div id="log"><div>Click log — commands are also printed in the terminal:</div></div>
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

wrap.addEventListener('mousemove', e => {{
  const {{dispX, dispY, px, py, tx, ty}} = getCoords(e);
  crossH.style.top  = dispY + 'px';
  crossV.style.left = dispX + 'px';
  tapEl.textContent = `python3 tap.py --x ${{tx}} --y ${{ty}}`;
  pxEl.textContent  = `pixel (${{px}}, ${{py}})`;
}});

wrap.addEventListener('click', e => {{
  const {{px, py, tx, ty}} = getCoords(e);
  const cmdStr = `python3 tap.py --x ${{tx}} --y ${{ty}}`;
  const entry = document.createElement('div');
  entry.innerHTML = `<span class="cmd">${{cmdStr}}</span>  <span style="color:#555">· pixel (${{px}}, ${{py}})</span>`;
  logEl.appendChild(entry);
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
    hint.textContent = `image ${{data.px_w}}×${{data.px_h}}px · scale {SCALE}× · click to lock`;
    const entry = document.createElement('div');
    entry.innerHTML = '<span style="color:#888">— screenshot refreshed —</span>';
    logEl.appendChild(entry);
  }} catch(e) {{
    const entry = document.createElement('div');
    entry.innerHTML = '<span style="color:#c44">refresh failed: ' + e + '</span>';
    logEl.appendChild(entry);
  }}
  refBtn.textContent = '↻ Refresh';
  refBtn.classList.remove('spinning');
  refBtn.disabled = false;
}}

refBtn.addEventListener('click', doRefresh);

document.addEventListener('keydown', e => {{
  if (e.key === 'r' || e.key === 'R') doRefresh();
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
                print(f"  python3 tap.py --x {tx} --y {ty}   (pixel {px}, {py})")
                self.send_response(200)
                self.end_headers()

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
    print("Hover to preview · click to print tap.py command · R to refresh · Ctrl+C to quit\n")

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
