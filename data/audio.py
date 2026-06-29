from __future__ import annotations

import random

import aiohttp

ATLAS_API = "https://api.atlasacademy.io"


async def fetch_voice_clip(
    session: aiohttp.ClientSession, servant_id: int, *, region: str = "NA"
) -> bytes | None:
    """One random voice-line clip (mp3) for a servant, or None if it has none.

    Deliberately a single clip, not a concatenation: robust, no ffmpeg needed.
    """
    url = f"{ATLAS_API}/nice/{region}/servant/{servant_id}?lore=true"
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()

    assets: list[str] = []
    for group in data.get("profile", {}).get("voices", []):
        for line in group.get("voiceLines", []):
            assets.extend(a for a in line.get("audioAssets", []) if a.endswith(".mp3"))
    if not assets:
        return None

    async with session.get(random.choice(assets)) as resp:
        if resp.status != 200:
            return None
        return await resp.read()
