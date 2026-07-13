.PHONY: lock lock-check run migrate sync sync-jp sync-data sync-ce sync-custom-assets install-hooks

lock:
	pip-compile --quiet --output-file=requirements.txt requirements.in

lock-check:
	pip-compile --quiet --output-file=- requirements.in | diff - requirements.txt

run:
	python bot.py

migrate:
	dbmate -d ./database/migrations --no-dump-schema up

# Refresh servant data from Atlas. `make sync-data` runs NA then JP in order:
# NA first, so servants that have reached NA graduate into servants.json; then JP,
# so servants_jp.json is only the JP-only remainder (deduped against the fresh NA
# list). New JP servants appear; just-released ones move out of the *jp pool with
# no manual id tracking. Review the diff, commit, and the image rebuild + watchtower
# deploy ships it. (Run `gen_community_aliases.py` separately to refresh NA nicknames.)
sync:
	python scripts/sync_atlas.py

sync-jp:
	python scripts/sync_atlas.py --jp

sync-data:
	python scripts/sync_atlas.py
	python scripts/sync_atlas.py --jp

# Refresh the Craft Essence pool for /guessce (5-star CEs with art) from Atlas.
sync-ce:
	python scripts/sync_ce.py

# Custom-servant art: drop PNGs under custom-assets/<slug>/ (see custom-assets/README.md),
# then push them to the public assets bucket under the custom/ prefix. Only adds/updates --
# never deletes -- so it can't disturb the precomputed silhouettes. Uses default AWS creds.
sync-custom-assets:
	aws s3 sync custom-assets/ "s3://$${ASSETS_BUCKET:-bunyanbot-assets-327760835875}/custom/" --exclude "README.md" --exclude "*.DS_Store" --exclude "*.swp"

# Install the git hooks (a pre-commit that syncs custom-assets/ to S3). One-time, per clone.
install-hooks:
	ln -sf ../../hooks/pre-commit .git/hooks/pre-commit
	chmod +x hooks/pre-commit
	@echo "installed pre-commit hook (custom-assets -> S3 on commit)"
