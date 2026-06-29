.PHONY: lock lock-check run migrate

lock:
	pip-compile --quiet --output-file=requirements.txt requirements.in

lock-check:
	pip-compile --quiet --output-file=- requirements.in | diff - requirements.txt

run:
	python bot.py

migrate:
	dbmate -d ./database/migrations --no-dump-schema up
