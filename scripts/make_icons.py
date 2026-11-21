"""Draw the app's shield icon and the menu-bar template icon."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "upload_guard" / "assets"


def shield(size: int) -> Image.Image:
    s = size * 4  # draw big, then downsample for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    top, bottom, left, right = s * 0.06, s * 0.96, s * 0.12, s * 0.88
    mid = s / 2
    outline = [
        (mid, top), (right, top + s * 0.12), (right, s * 0.48),
        (mid + s * 0.22, s * 0.8), (mid, bottom), (mid - s * 0.22, s * 0.8),
        (left, s * 0.48), (left, top + s * 0.12),
    ]
    d.polygon(outline, fill=(15, 118, 110, 255))
    # check mark
    w = max(2, int(s * 0.09))
    d.line([(s * 0.32, s * 0.5), (s * 0.45, s * 0.63), (s * 0.69, s * 0.36)], fill="white", width=w, joint="curve")
    return img.resize((size, size), Image.LANCZOS)


def menubar_template(size: int) -> Image.Image:
    """Black shield with a cut-out tick on transparent: macOS tints template images for light/dark."""
    img = shield(size)
    alpha = img.getchannel("A")
    r, g, b, _ = img.split()
    # the tick is white in the colour icon: make it transparent, everything else black
    tick = Image.merge("RGB", (r, g, b)).convert("L").point(lambda v: 255 if v > 200 else 0)
    out_alpha = Image.composite(Image.new("L", img.size, 0), alpha, tick)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.putalpha(out_alpha)
    return out


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    menubar_template(36).save(ASSETS / "menubar.png")  # drawn at 18 pt on retina
    shield(128).save(ASSETS / "icon128.png")
    print(f"icons written to {ASSETS}")


if __name__ == "__main__":
    main()
