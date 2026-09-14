# Requirements traceability

| Requirement | Implementation | Verification |
|---|---|---|
| Offline import with bounded resources | `sources/csv_import.py`, `qa.py` | Import edge cases in `test_product_safety.py`; fixture source tests |
| Retain all audit records | `qa.py`, `Workspace.save_audit` | Snapshot roundtrip and sample row count in `test_workspace.py` |
| Preserve separate locations | `processing/dedupe.py` | Branch rules, transitive conflict and large-block regression tests |
| Do not trust unrelated website contacts | `Pipeline._apply_site_extraction` | Wrong-company and domain-suffix tests |
| Correct DNS uncertainty | `processing/verify.py` | DoH failures, Null MX and preserved conflicts |
| Persist decisions without altering evidence | `Workspace.review` | Roundtrip, history and concurrent-update tests |
| Block defective approvals | `approval_blockers` | Invalid field and required-column tests |
| Compare revisions using stable IDs | `Workspace.compare` | Known changed/added/removed sample, missing/duplicate keys and project boundaries |
| Approved-only delivery | `Workspace.approved_csv` | Approve/reject roundtrip, CSV row accounting |
| Spreadsheet-safe text | `export/safety.py` | Formula payload parametrization; Excel literal-cell regression |
| Browser workflow | `dashboard.py` | Streamlit AppTest demo, review, comparison and extraction-navigation tests |
| Installed product works outside checkout | Package resources and CLI launcher | Wheel smoke job in CI |
| Only necessary repository files | `.gitignore`, `scripts/check_repository.py` | Tracked/staged-file release check |

See `VALIDATION.md` for the actual release outcome and remaining verification limits.
