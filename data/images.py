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
