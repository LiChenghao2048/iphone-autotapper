#!/usr/bin/env python3
"""
Interactive coordinate picker — opens in your browser.
Hover over the iPhone screenshot to see tap.py coordinates.
Click to lock in a coordinate (prints the command to the terminal).

Usage:
    python3 pick_coords.py               # uses screenshot.png
    python3 pick_coords.py --img my.png
"""

import argparse
import base64
import http.server
import os
import sys
import threading
import webbrowser
from PIL import Image
import io

SCALE = 3   # iPhone 14 Pro Max: 3× screen (pixels ÷ 3 = logical points)
PORT  = 9876


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--img", default=None)
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    img_path   = args.img or os.path.join(script_dir, "screenshot.png")

    if not os.path.exists(img_path):
        print(f"ERROR: {img_path} not found. Run 'python3 find_coords.py' first.")
        sys.exit(1)

    # Load image as-is — WDA returns the screenshot in the device's current orientation
    img = Image.open(img_path)
    px_w, px_h = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    print(f"Loaded: {img_path}  ({px_w}×{px_h} px)")
    print(f"Scale factor: {SCALE}×  →  logical size {px_w//SCALE}×{px_h//SCALE} pts")
    print(f"Opening in browser at http://127.0.0.1:{PORT}")
    print("Click anywhere on the screenshot to get the tap.py command.\n")

    html = f"""<!DOCTYPE html>
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
    display: flex; align-items: center; gap: 20px;
  }}
  #cmd  {{ color: #4ec9b0; font-size: 17px; font-weight: bold; }}
  #hint {{ color: #888; font-size: 13px; }}
  #wrap {{ margin-top: 48px; position: relative; display: inline-block; cursor: crosshair; }}
  img   {{ display: block; max-width: 100vw; }}
  #crossH, #crossV {{
    position: absolute; pointer-events: none;
    background: rgba(255,80,80,0.7);
  }}
  #crossH {{ height: 1px; left: 0; right: 0; }}
  #crossV {{ width: 1px;  top: 0; bottom: 0; }}
  #log {{
    background: #111; padding: 12px 16px; font-size: 13px; color: #aaa;
    border-top: 1px solid #333; min-height: 80px;
  }}
  #log div {{ margin: 2px 0; }}
  #log div span {{ color: #4ec9b0; }}
</style>
</head>
<body>
<div id="bar">
  <div id="cmd">Hover over the image</div>
  <div id="hint">Click to lock coordinate · coordinates already divided by {SCALE}</div>
</div>
<div id="wrap">
  <img id="img" src="data:image/png;base64,{b64}" draggable="false">
  <div id="crossH"></div>
  <div id="crossV"></div>
</div>
<div id="log"><div>Click log:</div></div>
<script>
const SCALE = {SCALE};
const wrap   = document.getElementById('wrap');
const img    = document.getElementById('img');
const crossH = document.getElementById('crossH');
const crossV = document.getElementById('crossV');
const cmd    = document.getElementById('cmd');
const log    = document.getElementById('log');

function getCoords(e) {{
  const r    = img.getBoundingClientRect();
  const dispX = e.clientX - r.left;
  const dispY = e.clientY - r.top;
  const scaleX = {px_w} / r.width;
  const scaleY = {px_h} / r.height;
  const px = Math.round(dispX * scaleX);
  const py = Math.round(dispY * scaleY);
  const tx = Math.floor(px / SCALE);
  const ty = Math.floor(py / SCALE);
  return {{dispX, dispY, px, py, tx, ty}};
}}

wrap.addEventListener('mousemove', e => {{
  const {{dispX, dispY, px, py, tx, ty}} = getCoords(e);
  crossH.style.top  = dispY + 'px';
  crossV.style.left = dispX + 'px';
  cmd.textContent = `x: ${{tx}}, y: ${{ty}}  (pixel ${{px}}, ${{py}})`;
}});

wrap.addEventListener('click', e => {{
  const {{px, py, tx, ty}} = getCoords(e);
  const entry = document.createElement('div');
  entry.innerHTML = `clicked: <span>x: ${{tx}}, y: ${{ty}}</span>  (pixel ${{px}}, ${{py}})`;
  log.appendChild(entry);
  fetch('/click?px=' + px + '&py=' + py);
}});
</script>
</body>
</html>"""

    # Simple HTTP server to serve the page and receive click events
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass   # silence access log
        def do_GET(self):
            if self.path.startswith("/click"):
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                px, py = q.get("px", ["?"])[0], q.get("py", ["?"])[0]
                tx, ty = int(px) // SCALE, int(py) // SCALE
                print(f"  x: {tx}, y: {ty}  (pixel {px}, {py})")
                self.send_response(200); self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    webbrowser.open(f"http://127.0.0.1:{PORT}")

    print("Close this terminal or press Ctrl+C to stop.\n")
    try:
        threading.Event().wait()   # block forever until Ctrl+C
    except KeyboardInterrupt:
        pass
    server.shutdown()


if __name__ == "__main__":
    main()
