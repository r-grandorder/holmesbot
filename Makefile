.PHONY: lock lock-check run migrate sync sync-jp sync-data

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
