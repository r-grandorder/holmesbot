from __future__ import annotations

import io
import random

import aiohttp
from PIL import Image

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
    """A random size x size patch of a silhouette, taken from within the FIGURE's
    bounding box (the dark shape) -- the card is opaque so an alpha-bbox crop would
    land on blank background. Used for the harder guess_shadow difficulties."""
    img = _load_rgba(data).convert("RGB")
    # the figure is near-black (~21) on the solid card (~128); threshold between.
    mask = img.convert("L").point(lambda p: 255 if p < 70 else 0)
    left, top, right, bottom = mask.getbbox() or (0, 0, img.width, img.height)
    fw, fh = right - left, bottom - top
    size = max(1, min(size, fw, fh))
    x = left + random.randint(0, max(0, fw - size))
    y = top + random.randint(0, max(0, fh - size))
    return _to_png(img.crop((x, y, x + size, y + size)))


def trim_to_content(data: bytes) -> bytes:
    """The artwork cropped to its content box (a reveal image)."""
    img = _load_rgba(data)
    return _to_png(img.crop(_content_bbox(img)))
