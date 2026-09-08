# Quality checklist

The checks applied automatically to every record, and the gates a delivery has to pass.

## Field-level checks

| Field | Automatic checks | `verified` | `invalid` |
|---|---|---|---|
| Company name | present; trimmed; legal forms kept in display, ignored for matching | found on the company website, or ≥70 % similar to the VIES legal name | - (missing name -> record EXCLUDED) |
| Category | required (leads schema) | - | missing -> flag |
| Street / number / postcode / city | postcode format for the country (BE: 4 digits) | - | bad postcode |
| Phone | parsed with libphonenumber for the project country; type recorded (fixed/mobile) | same number on the website | not a valid number |
| E-mail | syntax; mail-domain MX lookup (A-record fallback; DNS-over-HTTPS when local DNS is unavailable) | listed on the company website, or same value in two sources | bad syntax; domain cannot receive mail |
| Website | fetched live with redirects; HTML check; robots.txt honoured | live and company name found on the page | unreachable / HTTP error |
| VAT number | canonical form; modulo-97 checksum (BE); EU VIES lookup with retries | VIES valid | checksum fails or VIES rejects |
| Social profiles | canonical URLs; share/login/agency links removed | linked from the company website | not a recognised profile URL |
| Numbers / years / coordinates | numeric; min/max from the schema | - | out of range |

Values that are present but not confirmed stay **unverified**. Values that differ between sources become
**conflict** with the alternatives listed under "other values seen".

## Record-level checks

- Required fields present (schema).
- E-mail domain vs website domain (free-mail providers excepted) -> review flag.
- Opening hours marked "closed" -> review flag; source marks company dissolved -> flag + status note.
- Website found via search but company name not on the page -> review flag.
- Duplicate detection (see below).

## Record status

| Status | Meaning | Delivered? |
|---|---|---|
| VERIFIED | required fields present, no invalid/conflict, ≥2 contact fields verified (or name + 1) | yes |
| PARTIALLY_VERIFIED | ≥1 field verified, nothing invalid or conflicting | yes |
| UNVERIFIED | well-formed, single-source, nothing could be cross-checked | yes (marked) |
| NEEDS_REVIEW | invalid value, conflict, missing required field, possible duplicate | yes + listed in Needs Review |
| EXCLUDED | no name, or merged into another record | not in the data sheet; listed in Needs Review / Duplicates, kept in JSON |

## Duplicate rules

| Evidence | Decision |
|---|---|
| same phone / e-mail / VAT / product id **and** names ≥60 % similar | duplicate - merged |
| same website domain **and** (names ≥60 % similar or same postcode) | duplicate - merged |
| names ≥ threshold (default 90 %) **and** same address, or ≥97 % and same postcode | duplicate - merged |
| same website domain, different names and addresses | *possible* duplicate (branch/chain) - flagged only |
| same identifier but different names | *possible* duplicate - flagged only |

When merging, the record with the most verified fields (then completeness) is kept; missing values are
filled from the others; differing values are kept as candidates and the field is marked `conflict`.

## Delivery gates

Before a workbook goes to the client:

- [ ] Record count is plausible for the scope (compare with the source's own count).
- [ ] 0 records with status NEEDS_REVIEW left unresolved, or each one has a *Resolution*.
- [ ] Every *possible duplicate* has a decision.
- [ ] Completeness of the fields the client asked for is reported (Summary sheet).
- [ ] No column contains mixed formats (spot-check phone, e-mail, website columns with a filter).
- [ ] Run report warnings are read and, if relevant, mentioned in the delivery note.
- [ ] Data is fresh (run date in the file name; `--no-cache` for a final refresh).
- [ ] The JSON export is archived with the delivery.
