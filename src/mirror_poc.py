#!/usr/bin/env python3
"""
POC: iPhone Mirroring backend for the Brawl Stars bot.
Replaces WDA+USB with CGEventPostToPid (tap) and CGWindowListCreateImage (screenshot).

POC goals, in priority order:
  1. background_tap(tx, ty)  — inject a tap while iPhone Mirroring is not frontmost
  2. capture_frame()         — capture the window content as PNG (even in background)
  3. map_point(tx, ty, ...)  — convert logical iPhone point to macOS screen point

Requires:
  - System Settings → Privacy & Security → Accessibility  (for CGEventPostToPid)
  - System Settings → Privacy & Security → Screen Recording (for CGWindowListCreateImage)
  - pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa

Run:
    python3 src/mirror_poc.py --info
    python3 src/mirror_poc.py --tap 215,466
    python3 src/mirror_poc.py --screenshot [frame.png]
    python3 src/mirror_poc.py --tap 215,466 --screenshot
"""

import argparse
import sys
import time

try:
    import Quartz
    from AppKit import NSBitmapImageRep
except ImportError as exc:
    sys.exit(
        f"ERROR: pyobjc not found ({exc}).\n"
        "  pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa"
    )

# ── Tuneable constants ─────────────────────────────────────────────────────────

TITLE_BAR_H    = 28    # macOS Sequoia standard title bar height (screen points)
PHONE_LOGICAL_W = 430  # iPhone 14 Pro Max: pixels ÷ 3 = 1290 ÷ 3
PHONE_LOGICAL_H = 932  # iPhone 14 Pro Max: pixels ÷ 3 = 2796 ÷ 3
_PNG_TYPE       = 4    # NSBitmapImageFileTypePNG


# ── Window discovery ──────────────────────────────────────────────────────────

def _find_mirror_window() -> tuple[int, int, dict]:
    """Return (pid, window_id, bounds) for the iPhone Mirroring main window.

    bounds keys: X, Y, Width, Height — all in macOS screen points.
    When multiple layer-0 windows exist, picks the one with the largest area.
    Raises RuntimeError if the app is not running or no window is visible.
    """
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    best = None
    best_area = 0
    for w in windows:
        if w.get("kCGWindowOwnerName") != "iPhone Mirroring":
            continue
        if w.get("kCGWindowLayer", -1) != 0:
            continue
        b = w.get("kCGWindowBounds", {})
        area = b.get("Width", 0) * b.get("Height", 0)
        if area > best_area:
            best_area = area
            best = w
    if best is None:
        raise RuntimeError(
            "iPhone Mirroring window not found — is the app open and not minimised?"
        )
    return (
        int(best["kCGWindowOwnerPID"]),
        int(best["kCGWindowNumber"]),
        dict(best["kCGWindowBounds"]),
    )


# ── Coordinate mapping ────────────────────────────────────────────────────────

def map_point(
    tx: int,
    ty: int,
    bounds: dict,
    phone_pts: tuple[int, int] = (PHONE_LOGICAL_W, PHONE_LOGICAL_H),
    title_bar_h: int = TITLE_BAR_H,
) -> tuple[float, float]:
    """Convert a logical iPhone point (tx, ty) to a macOS screen point (sx, sy).

    Accounts for:
      - window position on screen (bounds X, Y)
      - title bar height
      - letterboxing when the window aspect ratio differs from the phone aspect ratio

    tx, ty: logical iPhone points (pixels ÷ SCALE), origin at top-left.
    Returns screen points suitable for CGEventCreateMouseEvent / CGEventPostToPid.
    Raises ValueError if phone_pts contains a non-positive dimension.
    """
    phone_w, phone_h = phone_pts
    if phone_w <= 0 or phone_h <= 0:
        raise ValueError(f"phone_pts must be positive, got {phone_pts}")
    content_w = bounds["Width"]
    content_h = bounds["Height"] - title_bar_h

    # Scale the phone display to fit within the content area (letterbox if needed)
    scale = min(content_w / phone_w, content_h / phone_h)
    display_w = phone_w * scale
    display_h = phone_h * scale

    # Centre the display within the content area (accounts for letterboxing)
    offset_x = (content_w - display_w) / 2
    offset_y = (content_h - display_h) / 2

    sx = bounds["X"] + offset_x + tx * scale
    sy = bounds["Y"] + title_bar_h + offset_y + ty * scale
    return sx, sy


# ── Background tap ────────────────────────────────────────────────────────────

def _send_tap(pid: int, bounds: dict, tx: int, ty: int) -> None:
    """Post kCGEventLeftMouseDown/Up to pid at the screen position for (tx, ty).

    Separated from _find_mirror_window so callers that already hold the window
    info (e.g. main()) can reuse it without a redundant lookup.
    """
    sx, sy = map_point(tx, ty, bounds)
    pt = Quartz.CGPointMake(sx, sy)
    down = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, pt, Quartz.kCGMouseButtonLeft
    )
    up = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, pt, Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPostToPid(pid, down)
    time.sleep(0.05)   # 50 ms hold, matching WDA tap duration
    Quartz.CGEventPostToPid(pid, up)


def background_tap(tx: int, ty: int) -> None:
    """Inject a mouse click at iPhone logical point (tx, ty) via CGEventPostToPid.

    iPhone Mirroring does not need to be the frontmost window.
    The key open question for this POC is whether iPhone actually registers
    the tap when the window is in the background — see README.
    """
    pid, _, bounds = _find_mirror_window()
    _send_tap(pid, bounds, tx, ty)


# ── Frame capture ─────────────────────────────────────────────────────────────

def capture_frame() -> bytes:
    """Return PNG bytes of the iPhone Mirroring window content.

    Uses CGWindowListCreateImage — works even when the window is in the
    background, as long as Screen Recording permission is granted.
    If permission is denied, macOS returns a valid but all-black image.
    """
    _, wid, _ = _find_mirror_window()
    cg_img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        wid,
        Quartz.kCGWindowImageDefault,
    )
    if cg_img is None:
        raise RuntimeError(
            "CGWindowListCreateImage returned None — "
            "grant Screen Recording permission in System Settings and retry."
        )
    rep = NSBitmapImageRep.alloc().initWithCGImage_(cg_img)
    data = rep.representationUsingType_properties_(_PNG_TYPE, None)
    if data is None:
        raise RuntimeError("Failed to encode captured window image as PNG.")
    return bytes(data)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="iPhone Mirroring POC — background tap + screenshot"
    )
    parser.add_argument(
        "--tap", metavar="TX,TY",
        help="Tap at logical iPhone point, e.g. --tap 215,466",
    )
    parser.add_argument(
        "--screenshot", metavar="FILE", nargs="?", const="frame.png",
        help="Capture frame to FILE (default: frame.png)",
    )
    parser.add_argument(
        "--info", action="store_true",
        help="Print iPhone Mirroring window info and mapped iPhone origin",
    )
    args = parser.parse_args()

    if not args.tap and not args.screenshot and not args.info:
        parser.print_help()
        sys.exit(0)

    try:
        pid, wid, bounds = _find_mirror_window()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.info:
        print(f"iPhone Mirroring  PID={pid}  WindowID={wid}")
        print(
            f"  bounds: X={bounds['X']}  Y={bounds['Y']}  "
            f"W={bounds['Width']}  H={bounds['Height']}"
        )
        sx0, sy0 = map_point(0, 0, bounds)
        print(f"  iPhone (0,0) → screen ({sx0:.1f}, {sy0:.1f})")

    if args.tap:
        parts = args.tap.split(",")
        if len(parts) != 2:
            print("ERROR: --tap requires TX,TY (e.g. --tap 215,466)", file=sys.stderr)
            sys.exit(1)
        try:
            tx, ty = int(parts[0]), int(parts[1])
        except ValueError:
            print("ERROR: TX and TY must be integers", file=sys.stderr)
            sys.exit(1)
        sx, sy = map_point(tx, ty, bounds)
        print(f"Tapping iPhone ({tx}, {ty}) → screen ({sx:.1f}, {sy:.1f}) ...")
        _send_tap(pid, bounds, tx, ty)
        print("Done. Check if the iPhone registered the tap.")

    if args.screenshot:
        print("Capturing frame ...")
        try:
            png = capture_frame()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        with open(args.screenshot, "wb") as f:
            f.write(png)
        print(f"Saved: {args.screenshot}  ({len(png) // 1024} KB)")


if __name__ == "__main__":
    main()
