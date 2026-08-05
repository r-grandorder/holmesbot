from __future__ import annotations

import io
import random

import aiohttp
from PIL import Image, ImageFilter

# Runtime image work for guess_servant (random crops) and reveals. The
# guess_shadow silhouettes are precomputed offline (scripts/precompute_silhouettes.py)
# and served from S3, so no sprite-sheet processing happens in the bot.


async def fetch_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.read()


def _load_rgba(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _content_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    return img.split()[-1].getbbox() or (0, 0, img.width, img.height)


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def crop_random(
    data: bytes, size: int, *, grayscale: bool = False, scramble: bool = False
) -> bytes:
    """A random size x size patch of the artwork (the guess_servant prompt)."""
    img = _load_rgba(data)
    left, top, right, bottom = _content_bbox(img)
    cw, ch = right - left, bottom - top
    size = max(1, min(size, cw, ch))
    x = left + random.randint(0, max(0, cw - size))
    y = top + random.randint(0, max(0, ch - size))
    crop = img.crop((x, y, x + size, y + size))
    flat = Image.new("RGBA", crop.size, (255, 255, 255, 255))
    flat.alpha_composite(crop)
    out = flat.convert("RGB")
    if grayscale:
        out = out.convert("L").convert("RGB")
    if scramble:
        if random.random() < 0.5:
            out = out.transpose(Image.FLIP_LEFT_RIGHT)
        out = out.rotate(random.choice([0, 90, 180, 270]))
    return _to_png(out)


def crop_silhouette(data: bytes, size: int) -> bytes:
    """A random size x size patch that STRADDLES the silhouette's outline, so it always
    shows a recognizable edge -- never all card (blue) and never all figure (a solid
    dark square, both unguessable). Anchors each candidate on an edge pixel and keeps
    the most balanced figure/background mix. Used for the harder guess_shadow tiers."""
    img = _load_rgba(data).convert("RGB")
    w, h = img.size
    # the figure is near-black (~21) on the solid card (~120); threshold between.
    mask = img.convert("L").point(lambda p: 255 if p < 70 else 0)
    left, top, right, bottom = mask.getbbox() or (0, 0, w, h)
    size = max(1, min(size, right - left, bottom - top))
    # Anchor on the figure OUTLINE (edge pixels) so a crop spans figure + card, not the
    # solid interior or the empty card. Fall back to any figure pixel if no edge found.
    anchors = [i for i, v in enumerate(mask.filter(ImageFilter.FIND_EDGES).tobytes()) if v]
    if not anchors:
        anchors = [i for i, v in enumerate(mask.tobytes()) if v]
    if not anchors:  # no figure at all -- fall back to a bbox crop
        return _to_png(img.crop((left, top, left + size, top + size)))
    total = size * size
    balanced = total * 0.25  # figure and card each >= ~25% reads as a clear outline
    best_box, best_score = None, -1
    for _ in range(10):
        px, py = divmod(random.choice(anchors), w)[::-1]
        x = min(max(px - random.randint(0, size - 1), 0), w - size)
        y = min(max(py - random.randint(0, size - 1), 0), h - size)
        box = (x, y, x + size, y + size)
        fig = mask.crop(box).histogram()[255]
        score = min(fig, total - fig)  # 0 if all one colour, largest near a 50/50 mix
        if score > best_score:
            best_score, best_box = score, box
        if score >= balanced:
            break
    return _to_png(img.crop(best_box))


def trim_to_content(data: bytes) -> bytes:
    """The artwork cropped to its content box (a reveal image)."""
    img = _load_rgba(data)
    return _to_png(img.crop(_content_bbox(img)))


def skill_strip(
    icons: list[bytes], *, side: int = 128, gap: int = 16, pad: int = 16
) -> bytes:
    """A horizontal strip of the servant's skill icons (the guess_skill prompt): each
    icon scaled to `side` px and laid left-to-right in skill order on a transparent
    background. The names are NOT drawn -- they're revealed one at a time as hints."""
    tiles = [
        Image.open(io.BytesIO(b)).convert("RGBA").resize((side, side), Image.LANCZOS)
        for b in icons
    ]
    n = max(1, len(tiles))
    canvas = Image.new(
        "RGBA", (pad * 2 + n * side + (n - 1) * gap, pad * 2 + side), (0, 0, 0, 0)
    )
    x = pad
    for tile in tiles:
        canvas.alpha_composite(tile, (x, pad))
        x += side + gap
    return _to_png(canvas)


def duel_banner(left: bytes, right: bytes, *, side: int = 120, gap: int = 32, pad: int = 11) -> bytes:
    """Two servant faces side by side (the duel result banner): each scaled to `side` px and
    laid left and right on a transparent background with a gap between them."""
    a = Image.open(io.BytesIO(left)).convert("RGBA").resize((side, side), Image.LANCZOS)
    b = Image.open(io.BytesIO(right)).convert("RGBA").resize((side, side), Image.LANCZOS)
    canvas = Image.new("RGBA", (pad * 2 + side * 2 + gap, pad * 2 + side), (0, 0, 0, 0))
    canvas.alpha_composite(a, (pad, pad))
    canvas.alpha_composite(b, (pad + side + gap, pad))
    return _to_png(canvas)


def battle_preview(bg: bytes, left_faces: list[bytes], right_faces: list[bytes]) -> bytes:
    """The /ab result image: both teams' face portraits laid over a battle background, the left
    team along the left edge and the right team along the right, each a bottom-anchored row.
    Faces scale to the background so up to three per side fit in each half."""
    base = Image.open(io.BytesIO(bg)).convert("RGBA")
    bw, bh = base.size
    side = max(48, min(int(bh * 0.34), int(bw * 0.15)))
    gap = max(4, int(side * 0.1))
    y = bh - side - int(bh * 0.06)  # bottom-anchored, small margin

    def _row(faces: list[bytes], center_x: int) -> None:
        tiles = []
        for fb in faces:
            try:
                tiles.append(
                    Image.open(io.BytesIO(fb)).convert("RGBA").resize((side, side), Image.LANCZOS)
                )
            except Exception:  # a bad/undecodable face just drops out of the row
                continue
        if not tiles:
            return
        row_w = len(tiles) * side + (len(tiles) - 1) * gap
        x = center_x - row_w // 2
        for tile in tiles:
            base.alpha_composite(tile, (max(0, x), max(0, y)))
            x += side + gap

    _row(left_faces, bw // 4)
    _row(right_faces, bw * 3 // 4)
    return _to_png(base.convert("RGB"))
