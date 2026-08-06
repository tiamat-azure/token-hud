"""Build the promo GIF: ticker -> double-click -> dashboard -> double-click -> ticker."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "images"
W, H = 880, 520
FPS = 20
CY = H // 2

ticker = Image.open(IMAGES / "ticker.png").convert("RGBA")
dash = Image.open(IMAGES / "dashboard.png").convert("RGBA")


def background() -> Image.Image:
    """Flat dark desktop with a faint grid - a gradient would force dithering in the palette."""
    bg = Image.new("RGBA", (W, H), (10, 14, 20, 255))
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(grid)
    for x in range(0, W, 40):
        g.line([(x, 0), (x, H)], fill=(60, 220, 255, 14))
    for y in range(0, H, 40):
        g.line([(0, y), (W, y)], fill=(60, 220, 255, 14))
    return Image.alpha_composite(bg, grid)


BG = background()


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def widget_frame(t: float) -> Image.Image:
    """t = 0 fully ticker, t = 1 fully dashboard. Sizes and opacities interpolate."""
    e = ease(t)
    w = round(ticker.width + (dash.width - ticker.width) * e)
    h = round(ticker.height + (dash.height - ticker.height) * e)
    box = ((W - w) // 2, CY - h // 2)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for img, alpha in ((ticker, 1.0 - e), (dash, e)):
        if alpha <= 0.01:
            continue
        scaled = img.resize((max(w, 1), max(h, 1)), Image.LANCZOS)
        if alpha < 1.0:
            faded = scaled.copy()
            faded.putalpha(scaled.getchannel("A").point(lambda v, a=alpha: int(v * a)))
            scaled = faded
        layer.alpha_composite(scaled, box)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        [box[0] - 6, box[1] - 6, box[0] + w + 6, box[1] + h + 6], radius=18, outline=(57, 255, 200, 26), width=6
    )
    return Image.alpha_composite(glow, layer)


def cursor(draw: ImageDraw.ImageDraw, x: float, y: float) -> None:
    arrow = [(0, 0), (0, 17), (4.4, 13.2), (7.4, 19.4), (10.4, 18.0), (7.5, 12.0), (12.6, 11.6)]
    pts = [(x + dx, y + dy) for dx, dy in arrow]
    draw.polygon(pts, fill=(245, 250, 255, 255), outline=(6, 10, 14, 255))


def ripples(draw: ImageDraw.ImageDraw, x: float, y: float, phases: list[float]) -> None:
    """One expanding neon ring per click still in flight."""
    for p in phases:
        r = 6 + 34 * p
        a = int(210 * (1 - p))
        draw.ellipse([x - r, y - r, x + r, y + r], outline=(57, 255, 200, a), width=3)


def build() -> list[tuple[Image.Image, int]]:
    frames: list[tuple[Image.Image, int]] = []
    cx, cy = W + 40.0, CY + 120.0  # cursor starts off-screen
    click_phases: list[float] = []

    def emit(t: float, hold: int = 1) -> None:
        frame = Image.alpha_composite(BG, widget_frame(t))
        draw = ImageDraw.Draw(frame)
        ripples(draw, cx, cy, click_phases)
        cursor(draw, cx, cy)
        frames.append((frame.convert("RGB"), hold))

    def advance() -> None:
        click_phases[:] = [p + 0.12 for p in click_phases if p + 0.12 < 1.0]

    # 1. ticker on screen, cursor still away
    for _ in range(20):
        emit(0.0)
        advance()

    # 2. cursor travels to the widget
    start = (cx, cy)
    for i in range(20):
        k = ease((i + 1) / 20)
        cx = start[0] + (W / 2 - 24 - start[0]) * k
        cy = start[1] + (CY - 6 - start[1]) * k
        emit(0.0)
        advance()

    # 3. double-click
    for i in range(14):
        if i in (0, 5):
            click_phases.append(0.0)
        emit(0.0)
        advance()

    # 4. expand, then hold the dashboard
    for i in range(14):
        emit((i + 1) / 14)
        advance()
    for _ in range(50):
        emit(1.0)
        advance()

    # 5. second double-click, collapse back
    for i in range(14):
        if i in (0, 5):
            click_phases.append(0.0)
        emit(1.0)
        advance()
    for i in range(12):
        emit(1.0 - (i + 1) / 12)
        advance()
    for i in range(26):
        emit(0.0, hold=2 if i == 25 else 1)
        advance()

    return frames


frames = build()
delay = round(1000 / FPS)
images = [f for f, _ in frames]
durations = [delay * h for _, h in frames]
out = IMAGES / "demo.gif"
images[0].save(
    out,
    save_all=True,
    append_images=images[1:],
    duration=durations,
    loop=0,
    optimize=True,
    disposal=2,
)
print(out, out.stat().st_size, len(images))
