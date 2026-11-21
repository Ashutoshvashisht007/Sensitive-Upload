"""Draw the extension's shield icon at 16, 48 and 128 px."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "extension" / "icons"


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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        shield(size).save(OUT / f"icon{size}.png")
    print(f"icons written to {OUT}")


if __name__ == "__main__":
    main()
