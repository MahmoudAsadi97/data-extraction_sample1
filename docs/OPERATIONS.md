# Operating and release guide

## Local use

Install from the repository or a built wheel. Run `dataharvest ui` from the checkout when you also want
the project YAML examples. An installed wheel can run the audit dashboard and synthetic demo from any
working directory. Use `DATAHARVEST_WORKSPACE` or CLI `--db` to select a customer-specific database.
Keep the application on localhost; the CLI sets `--server.address 127.0.0.1`.

Before importing, agree on the required fields, country rules, meaning of an account and a stable ID.
The country selector supplies phone/postcode normalization defaults; mixed-country lists need
appropriate schemas and validation against representative cases. Unmapped columns are reported and
excluded from schema delivery; retain the source file and never overwrite the customer's original export.
Paste workbook formulas as values first. Review all ambiguous mappings rather than discarding columns.

## Backups and retention

Stop the local application before copying the SQLite file and any output files. Restore into a separate
path, open it with `--db` / `DATAHARVEST_WORKSPACE`, and check the saved runs and a known decision.
Protect backups with the same OS permissions and disk encryption as the source data. Deleting a run
removes database records and history; it does not securely erase storage media or existing backups.

## Existing laptop checkout

For this checkout location, PowerShell commands are:

```powershell
Set-Location 'C:\Users\Nima\Desktop\MINE\My_projects\Dataextraction_sample\data-extraction_sample1'
git status --short
git pull --ff-only
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[ui]"
dataharvest ui
```

If local edits prevent the pull, preserve them before resolving the conflict. Do not use a hard reset to
synchronize customer work. The remote repository update does not directly modify a laptop filesystem.

## Reproduce development checks

```bash
uv sync --locked --extra dev --extra ui
uv run --no-sync ruff check src tests app scripts
uv run --no-sync pytest -m "not network" --cov=dataharvest --cov-report=term-missing
uv build
python scripts/check_repository.py
```

CI executes the offline suite on Linux (Python 3.10/3.11/3.12) and Windows (Python 3.11), then checks
installed-wheel startup separately. External APIs need separate credentials and a permitted network;
recorded fixtures exercise adapter logic but do not validate a provider's current account entitlements.

## Before pushing

The repository checker examines tracked/staged paths, secret-like values, file sizes and attribution
markers. Review `git diff --cached --stat` and `git diff --cached` as well. The checker is a practical
release guard, not a guarantee that all possible secrets or unwanted files can be recognized.
Only application code, tests, docs, synthetic samples, example configs and the dependency lock belong
in a release. No virtual environment, customer input, output workbook, SQLite database, token or cache.

Commits for this repository use Mahmoud Asadi Heris and the repository owner's email. Do not add
co-author trailers for tooling. Third-party code/data attribution and licenses remain intact.

## Live-source troubleshooting

A DNS failure means the source could not be checked, not that it is dead. Restore normal DNS/network
access and retry; do not disable destination validation or TLS verification. A blocked robots file,
rate limit or challenge is a review condition. Use a permitted source/provider if access is unavailable.

For repeatable offline work, run `dataharvest workspace demo` or import a customer-owned list. Do not
promise geographic completeness or mailbox delivery based on public-source coverage or MX checks.
