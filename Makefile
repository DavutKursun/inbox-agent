.PHONY: setup setup-dev test fixtures run eval-dry app

PY ?= python

setup:
	$(PY) -m pip install -r requirements.txt
setup-dev:
	$(PY) -m pip install -r requirements-dev.txt
test:
	$(PY) -m pytest -q
fixtures:         ## regenerate data/crm.sqlite and data/calendar.json (seeded)
	$(PY) scripts/make_fixtures.py
run:              ## make run FILE=data/samples/booking.eml.json
	$(PY) scripts/run_one.py $(FILE)
eval-dry:
	$(PY) scripts/eval.py --dry-run
app:
	$(PY) -m uvicorn app:app --port 7860
