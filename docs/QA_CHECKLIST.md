# Quality rules

## Offline workspace

- Required Account ID and Company name are checked per row, including when the entire column is absent.
- Values are normalized before format and cross-field checks. Invalid original values remain as evidence/candidates.
- Ambiguous headers/mappings, malformed table rows and workbook formulas stop import instead of losing data.
- Every record remains in the saved snapshot. Duplicate candidates are flagged; workspace audits never merge rows.
- Approval requires present required fields, no invalid/conflicting values, and no excluded/merged state.
- Other flags require an explicit reviewer note. Decisions do not change field verification statuses.
- New snapshots start with pending decisions. Identity comparison requires a valid unique Account ID.
- Exported approved CSV contains only explicit approvals, with verification status and decision metadata.

## Extraction

Website ownership is a name-match heuristic. Unmatched pages do not supply trusted contacts.
Same-domain lists and different locations must not be silently collapsed. Conflicting legal identifiers
and transitive location conflicts prevent automatic merging. Missing/blocked services produce explicit
unknown or review outcomes. DNS timeouts are not evidence of mailbox failure; Null MX is.

Raw extraction exports include flagged research records. They are not equivalent to an approved workspace delivery.
Review source rights, row accounting, unusual merges, unknown fields and warnings before a client delivery.

## Release checks

Run the test suite, lint, dependency audit, installed-wheel smoke test and repository checker.
Record the result and environment in `VALIDATION.md`. Evaluate customer accuracy on a labeled sample
with separate duplicate precision and recall; fixture pass counts do not establish real-world accuracy.
