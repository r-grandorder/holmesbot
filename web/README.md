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

## Deploy (GitHub Action -> Cloudflare Pages)
`.github/workflows/deploy-site.yml` builds `web/kits.json` and deploys `web/` on every push to
main (kit/web/script changes) or via manual dispatch. It uses **token-only** auth -- no Cloudflare
GitHub App / OAuth, so the Cloudflare account and this repo stay unlinked; the only bridge is one
encrypted secret that reveals nothing about the account owner.

One-time setup:
1. **Create the Pages project** (must match `--project-name` in the workflow, `holmesbot-kits`):
   `npx wrangler pages project create holmesbot-kits --production-branch main`
   (or dashboard -> Workers & Pages -> Create -> Pages -> Direct Upload).
2. **Create a scoped API token** (dashboard -> My Profile -> API Tokens -> Create): permission
   **Account -> Cloudflare Pages -> Edit**, scoped to the one account. Copy your **Account ID**
   (Workers & Pages overview, right sidebar).
3. **Add repo secrets** (repo -> Settings -> Secrets and variables -> Actions):
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

Keep the site on the `*.pages.dev` URL (no identity-tied custom domain). The build only reads
public Atlas data + the repo -- no other secrets/env.
