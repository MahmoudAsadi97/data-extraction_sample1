# Architecture

## Two supported workflows

The default dashboard and `workspace` CLI use the offline company-audit workflow:

`bounded input → column mapping → normalization → validation → duplicate candidates → SQLite snapshot → operator decisions → approved CSV`

The extraction workflow remains available under **Extraction projects** and `dataharvest run`:

`configured sources → normalization → website/API enrichment → verification → validation → deduplication → research exports`

Workspace approval controls do not automatically apply to extraction exports. The two workflows share
schemas, field status models, normalization, validation and duplicate detection. This avoids duplicating
business rules while keeping network use an explicit operator choice.

## Components

| Module | Responsibility |
|---|---|
| `models.py`, `schema.py` | Field values with status/source/candidates; configurable schema |
| `sources/csv_import.py` | Bounded table ingestion; structural input validation |
| `qa.py` | Offline audit orchestration and field mapping |
| `processing/` | Normalization, validation, DNS/phone checks and duplicate matching |
| `workspace.py` | Transactional snapshots, review event log, comparison and approved export |
| `workspace_cli.py`, `dashboard.py` | Product entrypoints |
| `sources/`, `enrich/`, `pipeline.py` | Original extraction adapters and orchestration |
| `network.py`, `http.py` | Destination validation, redirect policy, robots rules, HTTP/cache controls |
| `export/`, `report.py` | Spreadsheet/JSON research delivery and reports |

## Storage and identity

Each SQLite database is an operator-owned workspace. `runs` contains immutable normalized records,
schema, report, input name, SHA-256 fingerprint and creation time. It does not retain the original
uploaded file; evidence includes field candidates and raw metadata produced by the audit.

`reviews` holds the latest decision for each `(run_id, record_id)`. `events` records every successful
change. A transaction and expected revision protect against concurrent updates. Reviewer names are
operator-entered labels. This is a local change history, not a tamper-proof or authenticated audit log.
Deleting a run cascades to its reviews/events. Filesystem backups may retain deleted data.

Row IDs identify records within a run. Cross-run comparison uses the caller-selected schema field
(`account_id` by default), requires a valid unique value on every record, and refuses cross-project or
schema-mismatched comparisons. Changes to identity values appear as removal/addition; the application
does not guess that they describe the same entity. Approval never propagates to a new snapshot.

## Decision semantics

Approving a record does not modify values or automatic verification status. Approval is blocked for
missing required fields, invalid/conflicting fields, and excluded/merged records. Other flags require an
explanation. The operator corrects the source list and re-imports when a blocking problem exists.

A duplicate candidate can be acknowledged as a distinct location with a note. That does not resolve or
merge other group members automatically. Approved CSV contains the original status and reviewer metadata.

## Operating boundaries

- Local trusted operator; separate OS permissions/databases for separate customers.
- 5,000 input records, 200 columns, 20 MiB input, 100 MiB expanded XLSX.
- 250,000 duplicate candidate pairs; exceeding the budget raises an error.
- CSV formula mitigation affects text representation; evidence JSON preserves normalized values.
- Public-address checks happen before requests and redirect hops. They do not pin connections to a
  resolved address; DNS rebinding/proxy resolution need outbound network enforcement.
- Robots rules apply per destination. Missing robots files permit collection; unavailable/denied files defer it.
- Ordinary site requests are bounded by timeouts/retries and a 3 MB page-body limit. Trusted source API
  responses use adapter-specific limits and are not a general arbitrary-upload service.
- The HTTP cache is local and may contain extracted data; expired entries are not silently served after errors.
- There is no job scheduler, SaaS tenant boundary, SSO, billing or contractual SLA in this release.
