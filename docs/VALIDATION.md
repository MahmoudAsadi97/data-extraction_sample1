# Release validation — 1.1.0

Validated on 14 September 2026. Base repository commit: `17eb0626f24395b8269c6fc6f7be6395099ccd52`.

## Executed checks

| Check | Outcome | Scope |
|---|---|---|
| Baseline test suite | 118 passed; 87% coverage | Original repository before changes |
| Expanded suite | **187 passed**, **85.65% coverage** | Linux, Python 3.12; all offline tests; 80% minimum gate passed |
| Ruff | Passed | Application, tests, app launcher and scripts |
| Dependency audit | No known vulnerabilities found | Installed locked environment, editable project excluded; vulnerability database is time-dependent |
| Build | Source distribution and wheel built | Package resources and entrypoints included |
| Installed-wheel smoke | Passed outside the source checkout | Offline demo and dashboard AppTest load/sample workflow |
| Real browser workflow | Passed; no page exceptions or JavaScript errors | Chromium: upload CSV → audit → select record → approve → download approved CSV |
| Browser layout | Inspected at 1440px desktop and 390px mobile | Default view, review view and responsive layout; screenshot in README |
| Synthetic capacity check | 5,000 records saved in 0.354 seconds | Unique synthetic names/emails with no fuzzy duplicate candidates; one local run, not a general performance guarantee |
| Live catalogue request | Could not execute successfully | Environment DNS could not resolve books.toscrape.com; surfaced as source unavailability |

The pipeline integration tests use recorded responses for extraction, website parsing, search and VAT
checks. The new tests cover unsafe URL/redirect destinations, robots rules, ambiguous imports, CSV
formula payloads, numeric edge cases, location conflicts, large duplicate blocks, missing identifiers,
persistence, concurrent decisions and approval-only export.
Native mail-DNS responses are mocked explicitly, including timeout and Null MX cases. An unexpected
native DNS lookup fails an offline test instead of depending on the machine's network environment.
Output-name isolation is tested with a frozen clock, covering systems where consecutive runs receive
the same timestamp.

## Known verification boundaries

- Current paid API entitlements, live-source coverage and production network behavior were not validated.
- An offline list audit does not prove that a business, email address or phone number is reachable.
- The 5,000-row capacity test excludes expensive fuzzy candidate blocks. Repetitive lists can hit the
  explicit 250,000-pair budget and must be split; no duplicate-completeness guarantee is made.
- Test coverage measures executed lines, not business correctness or the absence of defects.
- Browser checks exercise the critical local flow. They are not a comprehensive accessibility audit.
- Authentication, authorization between customers, billing, hosted operations and service levels are
  outside this local pilot release. Reviewer names are operator-entered labels.
- Customer-specific accuracy and commercial acceptance require the labeled evaluation and paid pilots
  described in `PRODUCT_STRATEGY.md`.

The repository includes CI for Linux Python 3.10/3.11/3.12, Windows Python 3.11 and a separate wheel job.
The Actions run attached to the release commit is the source of truth for remote results; this document
records the local execution evidence above.
