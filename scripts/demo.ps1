# One-shot setup + demo run on Windows (PowerShell). Run from the repository root:
#   .\scripts\demo.ps1            (add -Full to collect everything instead of a 30-record sample)
param([switch]$Full)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
}
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip --quiet
pip install -e ".[dev]" --quiet

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

$limit = if ($Full) { @() } else { @("--limit", "30") }

Write-Host "`n=== Project 1/3: restaurants & cafes in Kortrijk (OpenStreetMap + websites + VIES) ===" -ForegroundColor Green
dataharvest run projects/kortrijk_restaurants.yaml @limit

Write-Host "`n=== Project 2/3: client seed list - enrichment & verification ===" -ForegroundColor Green
dataharvest run projects/seed_list_enrichment.yaml

Write-Host "`n=== Project 3/3: product catalogue (html_list on books.toscrape.com) ===" -ForegroundColor Green
dataharvest run projects/books_catalogue.yaml

Write-Host "`n=== Audit of an existing spreadsheet ===" -ForegroundColor Green
dataharvest validate data/input/seed_companies.csv --schema leads --country BE -m "Notes from client=description" --out data/output/seed_companies_audit.xlsx

Write-Host "`nDone. Open the files in data\output\ (start data\output)." -ForegroundColor Green
