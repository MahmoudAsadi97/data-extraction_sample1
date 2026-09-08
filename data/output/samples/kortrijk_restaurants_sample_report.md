# Run report - Restaurants & cafés in Kortrijk (BE) - verified lead list

- Project: `kortrijk_restaurants`  
- Started: 2026-09-08T08:53:56+00:00  
- Tool: DataHarvest 1.0.0 on Python 3.11.15  
- Records extracted: **60**, delivered: **54** (excluded/merged: 6)  
- Duplicate groups: 5

## Record status

| Status | Count |
|---|---|
| VERIFIED | 0 |
| PARTIALLY_VERIFIED | 0 |
| UNVERIFIED | 46 |
| NEEDS_REVIEW | 8 |
| EXCLUDED | 6 |

## Sources

| Source | Records |
|---|---|
| osm | 60 |

## Verification of key fields (delivered records)

| Field | verified | unverified | conflict | invalid | missing |
|---|---|---|---|---|---|
| company_name | 0 | 53 | 1 | 0 | 0 |
| phone | 0 | 26 | 0 | 0 | 28 |
| email | 0 | 19 | 0 | 0 | 35 |
| website | 0 | 45 | 0 | 0 | 9 |
| vat_number | 0 | 1 | 0 | 0 | 53 |

## Field completeness (delivered records)

| Field | Filled |
|---|---|
| company_name | 100% |
| legal_name | 0% |
| category | 100% |
| subcategory | 48% |
| street | 68% |
| house_number | 68% |
| postcode | 63% |
| city | 100% |
| district | 13% |
| country | 100% |
| phone | 48% |
| email | 35% |
| website | 83% |
| linkedin | 0% |
| facebook | 18% |
| instagram | 7% |
| vat_number | 2% |
| opening_hours | 41% |
| description | 2% |
| latitude | 100% |
| longitude | 100% |

## Most common review flags

| Flag | Records |
|---|---|
| possible duplicate | 6 |
| missing required field | 4 |
| missing company name - record unusable | 4 |
| e-mail domain  differs from website domain  - review | 3 |
| note | 2 |
| merged duplicates disagree on | 2 |
| source marks the business as closed  - review | 2 |
| duplicate of KOR-0010 | 1 |
| duplicate of KOR-0033 | 1 |

## Pipeline stages

| Stage | In | Out | Seconds | Notes |
|---|---|---|---|---|
| extract | 0 | 60 | 0.01 | osm: 60 |
| normalise | 60 | 60 | 0.0 |  |
| verify: e-mail domains & phones | 60 | 60 | 0.0 | offline: mail domains not checked |
| validate | 60 | 60 | 0.0 | 8 problem(s) flagged |
| dedupe | 60 | 58 | 0.02 | 5 group(s), 2 record(s) merged |
| finalise | 60 | 54 | 0.0 |  |
| export | 0 | 2 | 0.12 | xlsx, csv |

## Output files

- xlsx: `data/output/samples/kortrijk_restaurants_sample.xlsx`
- csv: `data/output/samples/kortrijk_restaurants_sample.csv`
