#!/usr/bin/env bash
# One-shot setup + demo run on macOS / Linux. Run from anywhere:
#   scripts/demo.sh            (add --full to collect everything instead of a 30-record sample)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip --quiet
pip install -e ".[dev]" --quiet
[ -f .env ] || cp .env.example .env

LIMIT=(--limit 30)
[ "${1:-}" = "--full" ] && LIMIT=()

echo; echo "=== Project 1/3: restaurants & cafes in Kortrijk (OpenStreetMap + websites + VIES) ==="
dataharvest run projects/kortrijk_restaurants.yaml "${LIMIT[@]}"

echo; echo "=== Project 2/3: client seed list - enrichment & verification ==="
dataharvest run projects/seed_list_enrichment.yaml

echo; echo "=== Project 3/3: product catalogue (html_list on books.toscrape.com) ==="
dataharvest run projects/books_catalogue.yaml

echo; echo "=== Audit of an existing spreadsheet ==="
dataharvest validate data/input/seed_companies.csv --schema leads --country BE -m "Notes from client=description" --optional category --out data/output/seed_companies_audit.xlsx

echo; echo "Done. Results are in data/output/."
