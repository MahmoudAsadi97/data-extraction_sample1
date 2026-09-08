# Sample outputs

Files produced by `dataharvest run` and `dataharvest validate`, kept here so the deliverable format can be
reviewed without running anything.

| File | Produced by |
|---|---|
| `kortrijk_restaurants_sample.xlsx` / `.csv` / `_report.md` | `projects/kortrijk_restaurants.yaml` in **offline mode** on the recorded OpenStreetMap extract in `tests/fixtures/` (60 businesses). Offline = no website visits, mail-domain lookups, web search or VIES calls, so contact details show as *unverified*; the run demonstrates extraction, normalisation, validation flags, duplicate merging and the workbook layout. A live run (`dataharvest run projects/kortrijk_restaurants.yaml`) adds the verification results. |
| `seed_companies_audit.xlsx` | `dataharvest validate data/input/seed_companies.csv --schema leads --country BE` - the audit of a client-style seed list with a duplicate and a broken row. |

Open the `.xlsx` files in Excel or LibreOffice; the *Summary* sheet formulas recalculate on open.
