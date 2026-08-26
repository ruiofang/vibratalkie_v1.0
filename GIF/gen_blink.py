#!/usr/bin/env python3
"""Generate a 240x240 blinking eyes GIF animation."""

from PIL import Image, ImageDraw
import math, os

W, H = 240, 240
BG = (30, 30, 30)
WHITE = (255, 255, 255)
IRIS = (70, 130, 200)
PUPIL = (20, 20, 20)
HIGHLIGHT = (255, 255, 255)

# Eye geometry
EYE_CX_L, EYE_CX_R = 82, 158
EYE_CY = 115
EYE_RX, EYE_RY = 28, 22

frames = []

def draw_eye(draw, cx, cy, rx, ry_scale):
    """Draw one eye with vertical squish factor ry_scale (1.0=open, 0.0=closed)."""
    ry = max(EYE_RY * ry_scale, 1.5)

    # White of the eye (sclera)
    bbox = [cx - rx, cy - ry, cx + rx, cy + ry]
    draw.ellipse(bbox, fill=WHITE)

    if ry_scale > 0.15:
        # Iris
        ir = min(rx * 0.55, ry * 0.9)
        draw.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=IRIS)
        # Pupil
        pr = ir * 0.5
        draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=PUPIL)
        # Highlight
        hr = ir * 0.25
        hx, hy = cx - ir * 0.3, cy - ir * 0.35
        draw.ellipse([hx, hy, hx + hr, hy + hr], fill=HIGHLIGHT)

    # Eyelid line on top/bottom when partially closed
    if ry_scale < 1.0:
        draw.line([(cx - rx, cy), (cx + rx, cy)], fill=BG, width=max(1, int(3 * (1 - ry_scale))))


def draw_face(draw):
    """Draw a subtle smile beneath the eyes."""
    # Small smile arc
    draw.arc([95, 140, 145, 170], start=10, end=170, fill=(180, 180, 180), width=2)


def make_frame(ry_scale):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw_eye(draw, EYE_CX_L, EYE_CY, EYE_RX, ry_scale)
    draw_eye(draw, EYE_CX_R, EYE_CY, EYE_RX, ry_scale)
    draw_face(draw)
    return img


# Build animation sequence
# Phase 1: Eyes open (hold)
for _ in range(12):
    frames.append(make_frame(1.0))

# Phase 2: Closing
for i in range(6):
    s = 1.0 - (i + 1) / 6.0
    frames.append(make_frame(s))

# Phase 3: Closed (hold briefly)
for _ in range(2):
    frames.append(make_frame(0.0))

# Phase 4: Opening
for i in range(6):
    s = (i + 1) / 6.0
    frames.append(make_frame(s))

# Phase 5: Eyes open again (hold)
for _ in range(8):
    frames.append(make_frame(1.0))

out = os.path.join(os.path.dirname(__file__), "eye_blink.gif")
frames[0].save(out, save_all=True, append_images=frames[1:],
               duration=50, loop=0, optimize=True)
print(f"Saved: {out}  ({len(frames)} frames)")
