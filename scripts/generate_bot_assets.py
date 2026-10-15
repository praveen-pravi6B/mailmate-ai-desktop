"""Generate MailMate AI bot profile and description images."""

from PIL import Image, ImageDraw, ImageFont
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "bot_assets")
os.makedirs(OUT_DIR, exist_ok=True)


def draw_rounded_rect(draw, xy, radius, fill):
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.ellipse([x0, y0, x0 + radius * 2, y0 + radius * 2], fill=fill)
    draw.ellipse([x1 - radius * 2, y0, x1, y0 + radius * 2], fill=fill)
    draw.ellipse([x0, y1 - radius * 2, x0 + radius * 2, y1], fill=fill)
    draw.ellipse([x1 - radius * 2, y1 - radius * 2, x1, y1], fill=fill)


def draw_envelope(draw, cx, cy, size, color):
    """Draw a simple envelope icon centered at (cx, cy)."""
    w, h = size, int(size * 0.7)
    x0, y0 = cx - w // 2, cy - h // 2
    x1, y1 = cx + w // 2, cy + h // 2

    # Body
    draw.rectangle([x0, y0, x1, y1], outline=color, width=max(3, size // 18))

    # Flap V shape
    lw = max(3, size // 18)
    draw.line([(x0, y0), (cx, cy - h // 6)], fill=color, width=lw)
    draw.line([(x1, y0), (cx, cy - h // 6)], fill=color, width=lw)


def draw_sparkle(draw, cx, cy, r, color, lw=3):
    """Draw a small 4-point star (AI sparkle)."""
    import math
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        length = r if angle % 90 == 0 else r * 0.5
        ex = cx + length * math.cos(rad)
        ey = cy + length * math.sin(rad)
        draw.line([(cx, cy), (ex, ey)], fill=color, width=lw)


# ── Profile picture  640×640 ─────────────────────────────────────────────────

def make_profile_pic():
    size = 640
    bg = (15, 15, 30)          # deep navy
    accent = (99, 179, 237)    # sky blue
    white = (255, 255, 255)

    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)

    # Gradient-ish background circle
    for r in range(300, 0, -1):
        alpha = int(30 * (1 - r / 300))
        draw.ellipse(
            [size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r],
            fill=(accent[0] // 8, accent[1] // 8, accent[2] // 8 + alpha),
        )

    # Envelope
    draw_envelope(draw, size // 2, size // 2 - 30, 260, accent)

    # AI sparkle top-right of envelope
    draw_sparkle(draw, size // 2 + 100, size // 2 - 110, 28, white, lw=4)

    # Text "MailMate AI"
    try:
        font_lg = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 56)
        font_sm = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
    except Exception:
        font_lg = ImageFont.load_default()
        font_sm = font_lg

    text = "MailMate AI"
    bbox = draw.textbbox((0, 0), text, font=font_lg)
    tw = bbox[2] - bbox[0]
    draw.text((size // 2 - tw // 2, size // 2 + 120), text, font=font_lg, fill=white)

    sub = "Smart email assistant"
    bbox2 = draw.textbbox((0, 0), sub, font=font_sm)
    sw = bbox2[2] - bbox2[0]
    draw.text((size // 2 - sw // 2, size // 2 + 190), sub, font=font_sm, fill=accent)

    path = os.path.join(OUT_DIR, "profile_pic.png")
    img.save(path)
    print(f"Saved: {path}")
    return path


# ── Description picture  640×360 (16:9) ─────────────────────────────────────

def make_description_pic():
    w, h = 640, 360
    bg = (15, 15, 30)
    accent = (99, 179, 237)
    green = (72, 199, 142)
    orange = (251, 146, 60)
    red = (248, 113, 113)
    white = (255, 255, 255)
    muted = (120, 130, 160)

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 38)
        font_body  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        font_tag   = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 15)
    except Exception:
        font_title = font_body = font_tag = ImageFont.load_default()

    # Title
    draw.text((36, 28), "MailMate AI", font=font_title, fill=white)
    draw.text((36, 76), "Local-first email assistant powered by Laya", font=font_body, fill=muted)

    # Pipeline boxes — scale to fit 640px
    steps = [
        ("Gmail", accent),
        ("Laya AI", accent),
        ("Policy", accent),
        ("SQLite", accent),
        ("Telegram", green),
        ("Calendar", orange),
    ]
    box_w, box_h = 82, 32
    gap = 10
    total = len(steps) * box_w + (len(steps) - 1) * gap
    sx = (w - total) // 2
    sy = 130

    for i, (label, color) in enumerate(steps):
        bx = sx + i * (box_w + gap)
        draw_rounded_rect(draw, [bx, sy, bx + box_w, sy + box_h], 7, color)
        bbox = draw.textbbox((0, 0), label, font=font_tag)
        tx = bx + (box_w - (bbox[2] - bbox[0])) // 2
        ty = sy + (box_h - (bbox[3] - bbox[1])) // 2
        draw.text((tx, ty), label, font=font_tag, fill=bg)
        if i < len(steps) - 1:
            ax = bx + box_w + 2
            ay = sy + box_h // 2
            draw.line([(ax, ay), (ax + gap - 3, ay)], fill=muted, width=2)
            draw.polygon([(ax + gap - 3, ay - 3), (ax + gap - 3, ay + 3),
                          (ax + gap, ay)], fill=muted)

    # Priority pills — single row, dot drawn with PIL (no emoji)
    priorities = [
        ("P0  Critical",  red,              red),
        ("P1  Important", orange,            orange),
        ("P2  Routine",   (234, 179, 8),     (234, 179, 8)),
        ("OTHER  Summary", muted,            muted),
    ]
    pill_h = 30
    dot_r  = 6
    py = 205
    # calculate total width to center
    pill_widths = []
    for label, _, _ in priorities:
        bbox = draw.textbbox((0, 0), label, font=font_tag)
        pill_widths.append(bbox[2] - bbox[0] + dot_r * 2 + 28)
    gap_p = 10
    total_p = sum(pill_widths) + gap_p * (len(priorities) - 1)
    px = (w - total_p) // 2

    for i, (label, dot_color, text_color) in enumerate(priorities):
        pw = pill_widths[i]
        bg_color = (dot_color[0] // 5, dot_color[1] // 5, dot_color[2] // 5)
        draw_rounded_rect(draw, [px, py, px + pw, py + pill_h], 7, bg_color)
        # colored dot
        cx = px + 12 + dot_r
        cy = py + pill_h // 2
        draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=dot_color)
        # label text
        draw.text((cx + dot_r + 8, py + (pill_h - font_tag.size) // 2 - 1),
                  label, font=font_tag, fill=text_color)
        px += pw + gap_p

    # Sparkle
    draw_sparkle(draw, w - 44, 44, 18, accent, lw=3)

    path = os.path.join(OUT_DIR, "description_pic.png")
    img.save(path)
    print(f"Saved: {path}")
    return path


if __name__ == "__main__":
    p1 = make_profile_pic()
    p2 = make_description_pic()
    print("Done.")
