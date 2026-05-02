.PHONY: install dev-install bootstrap run test lint type clean

PY ?= python

install:
	$(PY) -m pip install -e .

dev-install:
	$(PY) -m pip install -e ".[dev]"

bootstrap:
	$(PY) scripts/bootstrap_db.py

run:
	$(PY) -m agent_memory_lite

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests scripts
	$(PY) -m ruff format --check src tests scripts

type:
	$(PY) -m mypy src

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis build dist *.egg-info
