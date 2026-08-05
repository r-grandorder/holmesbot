# Autobattle kit browser (static site)

A zero-backend site for browsing/filtering every servant's autobattle kit by class, trigger, and
effect. It's pure static reference data -- no API, no database, no auth -- so it's the easy first
slice of the planned Cloudflare Pages dashboard. The in-Discord companion is `/ab kit <servant>`
for quick single lookups.

## Files
- `index.html` -- the whole app (vanilla JS; loads `kits.json` and filters client-side).
- `kits.json` -- generated data (gitignored). Produced by `scripts/build_site_data.py` from the
  committed kit sources in `data/kits/*.json`, enriched with each servant's rarity + face portrait
  when `data/servants.json` is present.

## Local preview
```sh
python scripts/build_site_data.py        # writes web/kits.json (art included if servants.json exists)
python -m http.server -d web 8000        # serve it (fetch() won't work from file://)
# open http://localhost:8000
```
Run `scripts/sync_atlas.py` first if you want the servant face/rarity locally (otherwise the site
still works from kit data alone -- names, class, skill, effects).

## Cloudflare Pages (TODO -- set up 2026-08-06)
Connect the repo and configure:
- **Build command:** `python scripts/sync_atlas.py && python scripts/build_site_data.py`
  (drop the `sync_atlas.py` half for a faster build with no face art)
- **Build output directory:** `web`
- **Framework preset:** None

The build regenerates `web/kits.json` on every deploy, so the site stays in sync with the kit
files. No secrets or environment needed -- it only reads public Atlas data + the repo.
