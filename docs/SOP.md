# Standard operating procedure - data extraction job

The procedure followed for every extraction task, from brief to delivery. Steps 3-7 are automated by
`dataharvest run`; steps 1, 2, 8 and 9 are the human part of the job.

## 1. Understand the brief

- Read the instructions and write them down as a project file (`dataharvest init NAME --template leads`,
  then edit). Put the client's wording in `project.instructions` so it travels with the output.
- Decide the **schema** (columns): built-in `leads`, `companies` or `products`, or add fields.
- Decide the **scope**: area (municipality, province, bounding box), categories/tags, expected volume.
- Decide the **sources**: free ones first (OpenStreetMap, Wikidata, the client's own list), paid APIs
  when the client provides keys (Google Maps, Apollo). Note anything the brief asks for that is not
  allowed (e.g. scraping LinkedIn) and propose the alternative.
- Agree on the deadline and on how "verified" is defined (see `QA_CHECKLIST.md`).

## 2. Prepare

- Copy `.env.example` to `.env`; add API keys if any; add a contact e-mail for the User-Agent.
- Run a **small test**: `dataharvest run projects/NAME.yaml --limit 20`. Check that the category/tag
  selection and the area are right by opening the source URLs of a few records.
- Adjust tags, area or selectors until the sample looks right.

## 3. Extract

`dataharvest run projects/NAME.yaml` collects every record from every enabled source. Each record keeps
its source id and URL. Nothing is filtered yet - unusable records are kept and marked later.

## 4. Normalise

Every value is converted to the column's canonical format (phone E.164, e-mail lower-case, URL with scheme,
postcode, VAT `BE0123456789`, social profile URLs). Values that cannot be interpreted are set aside as
"other values seen" and flagged.

## 5. Enrich

- Every website is visited (home page + up to two contact/about pages) to confirm the name and to collect
  e-mails, phones, social links, VAT numbers and a description.
- Businesses without a website are looked up with a web search; a hit is stored as *unverified* until
  the page confirms the company name.
- VAT numbers are checked with the EU VIES service; the registered legal name is stored.
- With an Apollo key: LinkedIn URL, industry, size and founding year are completed by domain.

## 6. Verify & validate

Rules in `QA_CHECKLIST.md`. Each field receives a status (`verified`, `unverified`, `conflict`,
`invalid`, `missing`), each record a status and a list of review flags.

## 7. De-duplicate

Records sharing a phone, e-mail, VAT number or website domain with a similar name are merged (the most
complete/verified record is kept; gaps are filled from the others; disagreements become conflicts).
Records that share only a domain but have different addresses (branches, chains) are flagged as
*possible* duplicates for a human decision.

## 8. Review (manual)

Open the workbook:

1. **Summary** - sanity-check the counts against the expected volume.
2. **Needs Review** - work through each row: open the source URL and the website, fix or confirm the
   value in the data sheet, record the outcome in *Resolution* / *Resolved by* / *Resolved on*.
3. **Duplicates** - decide every *POSSIBLE duplicate* (same entity -> merge; different -> keep both).
4. **Data sheet** - use the *Reviewer decision* drop-down (Approved / Rejected / Needs fix / Contacted)
   and *Reviewer notes*. The Summary counts update automatically.
5. Spot-check 5-10 % of *VERIFIED* rows against their sources.

Typical review time: 1-2 minutes per flagged row.

## 9. Deliver

- Re-run with `--no-cache` if the data is older than a few days, then finish the review.
- Deliver the `.xlsx` (and `.csv` / Google Sheet if requested) plus the run report (`*_report.md`).
- In the delivery note, list: number of records, how many verified/unverified/flagged, duplicates
  merged, sources used, known limitations (e.g. searches skipped because of rate limits).
- Keep the JSON export: it holds the full provenance if a value is questioned later.

## Time budget (indicative, municipality-sized task)

| Step | Time |
|---|---|
| Brief -> project file, test run | 30-45 min |
| Full automated run | 5-15 min (depends on number of websites) |
| Manual review of flagged rows | 1-2 min per row |
| Spot checks + delivery note | 20-30 min |
