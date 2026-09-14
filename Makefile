.PHONY: install test lint demo ui clean

install:
	pip install -e ".[dev,ui]"

test:
	pytest -q -m "not network"

lint:
	ruff check src tests app

demo:
	dataharvest workspace demo

ui:
	dataharvest ui

clean:
	rm -rf data/cache/*.sqlite .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
