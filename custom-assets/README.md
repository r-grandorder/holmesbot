# Custom servant art (staged for S3)

Art for custom summonable servants lives on S3 (the project's asset-hosting standard).
Stage the images here, then push them up. Everything in this directory is gitignored
**except this README** -- the repo never holds the binaries; S3 is the source of truth.

## Layout

One folder per unit, named with a slug (kebab-case of the servant's name):

```
custom-assets/<slug>/art.png     full character art  (REQUIRED -- the big /summon image)
custom-assets/<slug>/face.png    square portrait      (recommended -- thumbnail + /duel banner)
```

Example:

```
custom-assets/space-ereshkigal/art.png
custom-assets/space-ereshkigal/face.png
```

Both should be transparent PNGs. `art` ~512-1024px tall (portrait); `face` ~256x256 (square).
`figure` is not needed -- custom units are summon-only and never appear in the shadow game.

## Upload

```
make sync-custom-assets
```

Runs `aws s3 sync custom-assets/ s3://bunyanbot-assets-327760835875/custom/` with your default AWS credentials
(override with `ASSETS_BUCKET=<bucket> make sync-custom-assets`). It only adds/updates (never
`--delete`), so it can't disturb anything else in the bucket. The pre-commit hook (installed
via `make install-hooks`) runs this automatically on commit.

## Reference it in data/custom_servants.json

The public URL is `$ASSETS_BASE_URL/custom/<slug>/<file>` -- the same base the bot already
uses for silhouettes. So for the example above:

```json
"art":  { "0": "https://bunyanbot-assets-327760835875.s3.us-east-1.amazonaws.com/custom/space-ereshkigal/art.png" },
"face":       "https://bunyanbot-assets-327760835875.s3.us-east-1.amazonaws.com/custom/space-ereshkigal/face.png"
```

(FGO event units sourced from Atlas can keep their Atlas URLs and skip all of this; this is
for truly custom art you host yourself.)
