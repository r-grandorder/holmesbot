"""Precompute guess_shadow assets and upload them to S3.

Reads data/servants.json (run scripts/sync_atlas.py first). For every charaFigure
ascension it extracts the MAIN figure from the sprite sheet -- the largest
connected alpha component plus any pieces whose box overlaps it (a held weapon,
say) -- which robustly ignores the expression-tile grid regardless of how many
tiles there are or how they're laid out. It renders a dark-on-white silhouette
and a cropped colored reveal, uploads both to S3, writes data/shadows.json (the
manifest the bot picks from), and saves a contact sheet for spot-checking.

Usage:
    pip install -r scripts/requirements-precompute.txt
    python scripts/sync_atlas.py                 # builds data/servants.json
    ASSETS_BUCKET=<bucket> python scripts/precompute_silhouettes.py

Uses default AWS credentials. scipy/numpy/boto3 live here only, never in the bot.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import boto3
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SERVANTS = ROOT / "data" / "servants.json"
SERVANTS_JP = ROOT / "data" / "servants_jp.json"  # JP-only servants for /guessshadowjp
MANIFEST = ROOT / "data" / "shadows.json"
CONTACT = ROOT / "test_output" / "shadow_contact.png"
ALPHA_THRESHOLD = 16
ASSET_PREFIX = "v3"  # bump to bust the CDN cache; keep in sync with cogs/guess_shadow.py
CACHE_CONTROL = "public, max-age=604800"


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def extract_main_figure(sheet: Image.Image) -> Image.Image:
    """Crop the main standing figure out of the charaFigure sheet.

    The sheet is the figure on top with a grid of face-expression tiles below
    (sometimes one connected alpha blob with the figure). The grid rows pack the
    content width densely, while the figure never does -- so we find the first
    dense row below a minimum figure height (the grid onset), back up to a
    coverage trough only if there's a real gap there, and crop above it. No
    assumption about how many tiles there are or how they're laid out.
    """
    rgba = sheet.convert("RGBA")
    opaque = np.array(rgba.getchannel("A")) > ALPHA_THRESHOLD
    ys, xs = np.where(opaque)
    if len(ys) == 0:
        return rgba
    x0, x1, y0, h = int(xs.min()), int(xs.max()) + 1, int(ys.min()), rgba.height
    cov = opaque[:, x0:x1].sum(axis=1) / (x1 - x0)
    min_fig = y0 + int(0.20 * h)
    onset = next((y for y in range(min_fig, h) if cov[y] > 0.6), None)
    if onset is None:
        boundary = h
    else:
        lo = max(min_fig, onset - 128)
        trough = lo + int(np.argmin(cov[lo : onset + 1]))
        boundary = trough if cov[trough] < 0.15 else onset
    fig = rgba.crop((x0, y0, x1, boundary))
    bbox = fig.split()[-1].getbbox()
    return fig.crop(bbox) if bbox else fig


def _fit(img: Image.Image, maxdim: int) -> Image.Image:
    if max(img.size) <= maxdim:
        return img
    img = img.copy()
    img.thumbnail((maxdim, maxdim))
    return img


def silhouette(fig: Image.Image) -> bytes:
    # Black silhouette on a solid #4D84AE card. Opaque, so it reads identically on
    # any Discord theme -- and it's a color, not a white box.
    fig = _fit(fig, 512)
    canvas = Image.new("RGB", fig.size, (77, 132, 174))
    canvas.paste((20, 20, 25), (0, 0), fig.split()[-1])
    return _png(canvas)


def colored(fig: Image.Image) -> bytes:
    # The reveal: the colored figure on transparent (the art carries its colors).
    return _png(fig)


def main() -> int:
    bucket = os.environ.get("ASSETS_BUCKET")
    if not bucket:
        print("ASSETS_BUCKET env var required", file=sys.stderr)
        return 1
    if not SERVANTS.exists():
        print("data/servants.json missing -- run scripts/sync_atlas.py first", file=sys.stderr)
        return 1

    s3 = boto3.client("s3")
    servants = json.loads(SERVANTS.read_text())
    if SERVANTS_JP.exists():  # JP servants too, so /guessshadowjp has silhouettes
        servants += json.loads(SERVANTS_JP.read_text())
    limit = int(os.environ.get("PRECOMPUTE_LIMIT", "0"))
    if limit:
        servants = servants[:limit]
        print(f"PRECOMPUTE_LIMIT={limit}: processing first {len(servants)} servants only", flush=True)
    total = sum(len(s.get("figure", {})) for s in servants)
    print(f"Processing {total} charaFigure assets -> s3://{bucket}/", flush=True)

    manifest: dict[str, list[str]] = {}
    thumbs: list[Image.Image] = []
    done = 0
    for s in servants:
        sid = s["id"]
        for asc, url in s.get("figure", {}).items():
            done += 1
            try:
                fig = extract_main_figure(Image.open(io.BytesIO(_fetch(url))))
                if fig.width < 32 or fig.height < 32:
                    continue
                sil = silhouette(fig)
                s3.put_object(
                    Bucket=bucket, Key=f"shadow/{ASSET_PREFIX}/{sid}/{asc}.png", Body=sil,
                    ContentType="image/png", CacheControl=CACHE_CONTROL,
                )
                s3.put_object(
                    Bucket=bucket, Key=f"figure/{ASSET_PREFIX}/{sid}/{asc}.png", Body=colored(fig),
                    ContentType="image/png", CacheControl=CACHE_CONTROL,
                )
                manifest.setdefault(str(sid), []).append(asc)
                if len(thumbs) < 80:
                    t = Image.open(io.BytesIO(sil)).convert("RGBA")
                    t.thumbnail((120, 160))
                    thumbs.append(t)
            except Exception as e:  # noqa: BLE001
                print(f"  skip {sid}/{asc}: {e}", file=sys.stderr)
            if done % 25 == 0:
                print(f"  {done}/{total}", flush=True)

    MANIFEST.write_text(json.dumps(manifest))
    print(f"Wrote {MANIFEST} ({sum(len(v) for v in manifest.values())} assets, {len(manifest)} servants)")

    if thumbs:
        cols = 10
        rows = (len(thumbs) + cols - 1) // cols
        sw, sh = cols * 120, rows * 160
        sheet = Image.new("RGB", (sw, sh), (49, 51, 56))  # left half: Discord dark
        sheet.paste(Image.new("RGB", (sw // 2, sh), (240, 241, 245)), (sw // 2, 0))  # right half: light
        for i, t in enumerate(thumbs):
            sheet.paste(t, ((i % cols) * 120, (i // cols) * 160), t)
        CONTACT.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(CONTACT)
        print(f"Contact sheet: {CONTACT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
