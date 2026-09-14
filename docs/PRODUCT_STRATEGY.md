# Product strategy and release assessment

Assessment date: 14 September 2026. Recommendations below are hypotheses to validate with customers,
not evidence of demand or guaranteed sales.

## Recommended first offer

**A company-data maintenance workbench for small B2B teams using spreadsheets or periodic CRM exports.**
Start with one segment, such as Benelux distributors or service agencies with 200–5,000 company records.
The initial service: import their list, review exceptions with them, return an approved file and evidence,
then compare a refreshed export monthly. Sell the work outcome and accountability.

The segment is a proposed starting point based on the project's existing Belgian phone, postcode,
VAT and local-company workflows. Customer interviews must establish actual urgency and willingness to pay.

## Why a general enrichment product is a difficult starting point

HubSpot already provides duplicate management, formatting tools and enrichment coverage in its
[data quality tools](https://knowledge.hubspot.com/data-management/use-data-quality-tools).
Salesforce offers [duplicate management](https://help.salesforce.com/s/articleView?id=sales.data_quality.htm&language=en&type=5).
Clay markets [CRM enrichment and multi-provider workflows](https://www.clay.com/).
These are verified competitor capabilities, not proof that all customers are satisfied.

**Inference:** competing on source count or generic enrichment alone would be difficult. A better initial
position is a small, inspectable workflow for a particular customer's data, with local operation and
reviewable exceptions. The current software supports that position; demand still needs validation.

## Angles ranked for this project

| Priority | Offer | Buyer and problem | Advantage available now | Evidence still needed |
|---|---|---|---|---|
| 1 | Company-list cleanup and recurring QA | Small sales-operations team with inconsistent CRM exports | Offline import, conservative matching, decisions, change reports | Paid pilot, false-positive rate, time saved |
| 2 | Supplier master-data review | Procurement team maintaining company locations and identifiers | Required fields, explicit conflicts, evidence exports; optional VAT checks | Domain-specific rules and procurement interviews |
| 3 | Directory / branch-list maintenance | Trade association, franchise office or local directory operator | Branch preservation and repeat-run differences | Location identity model and licensed refresh sources |
| 4 | Generic bulk lead scraping | Broad outbound-sales market | Existing connectors | Strong differentiation, reliable licensed coverage, unit economics |

Do not market this as supplier risk certification, mailbox deliverability verification, or a complete
business register. Those require capabilities and evidence the current project does not have.

## Weaknesses found and what changed

| Weakness in the baseline | Consequence | Release response |
|---|---|---|
| Shared domain/name could merge distinct branches | Lost locations and corrupted customer lists | Conflicting locations/identifiers remain separate; shared domains need stronger evidence |
| Blocks above 400 records were skipped | False impression that duplicate detection was complete | Process candidates or fail explicitly at the comparison budget |
| Website contacts applied after company-name mismatch | Unrelated contacts could become trusted | Keep page evidence; do not apply contacts without a name match |
| Domain suffix matching accepted lookalikes | Wrong email domain could appear company-owned | Exact domain or dot-delimited subdomain comparison |
| No persisted review workflow | Decisions lost outside a manually edited workbook | Durable snapshots, decision events, approval-only delivery |
| Sequential row IDs only | Rows appeared unrelated after reordering | Explicit unique Account ID for comparisons |
| Whole missing required columns ignored for row status | Incomplete lists could look acceptable | Missing requirements block approval unless schema explicitly makes them optional |
| CSV text could be interpreted as formulas | Spreadsheet injection and changed data interpretation | Escape risky text; retain exact evidence separately |
| Import silently overwrote duplicate headers | Undetected information loss | Reject ambiguous headers, mappings and malformed rows |
| Day-only output filename | Subsequent run overwrote prior outputs | Per-run timestamp plus a unique run identifier, independent of clock resolution |
| UI lived outside package | Wheel installation could not start dashboard | Dashboard and sample assets included in package |
| No dependency lock | Behavior changed with fresh installs | Universal `uv.lock` and locked CI installs |

## Four-week paid-pilot experiment

1. Interview ten people from one target segment. Ask for their last data-cleanup incident, workflow,
   consequences and buying process. Do not assume the decision maker is the person doing the cleanup.
2. Offer three scoped pilots on customer-owned lists. Agree on schema, row limit, permitted data use,
   customer reviewers and acceptance criteria before receiving files.
3. Review a labeled sample of 100–200 records with the customer. Include true duplicates, separate
   branches, missing identifiers, bad formats, and common edge cases. Keep a holdout portion.
4. Compare manual review time against the assisted workflow on comparable batches. Record corrections,
   false merges, unresolved cases and delivery acceptance. Keep operator time and external-source costs.

Suggested experiment, **not a market price**: test a €400 fixed-scope initial cleanup and €150/month
for repeat checks on an agreed small list. Reprice after observing operator time and willingness to pay.
A subscription is only justified if customers repeatedly obtain useful changes and renew.

## Pilot acceptance criteria

These are targets for customer validation, not achieved metrics:

- Zero wrongly merged separate companies/branches in the agreed labeled acceptance sample.
- All source rows accounted for; unknown or invalid values remain explicit.
- At least 50% less review time for the same accepted output quality, measured against a baseline.
- Every delivered record has a reviewer decision; no rejected or pending rows enter approved delivery.
- Three customers complete the pilot; at least two request a further paid cycle.

Measure duplicate precision and recall separately; avoiding false merges can reduce recall. A fixture test
count is not a model-quality metric and cannot substitute for this customer evaluation.

## Next engineering investments, in order

1. Customer-specific mappings, an agreed correction workflow and trustworthy stable identifiers.
2. A labeled duplicate benchmark, stronger entity/location separation and scalable candidate indexing.
3. One CRM connector demanded by paid customers, with previewable changes, idempotent updates,
   source-ID retention and an undo/recovery plan.
4. Authenticated operators, customer authorization boundaries, encrypted deployment/backups and a
   background job queue before shared hosting. Review names alone are not access control.
5. Contracted/licensed enrichment providers and measurable freshness before selling refresh guarantees.
6. Scheduling, billing and team workflow only when repeated usage supports them.

No CRM credentials, paid enrichment accounts, billing accounts or hosted deployment were needed for
this release. No customer communications or CRM writes were made.
