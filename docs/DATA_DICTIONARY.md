# Data dictionary

The columns of the delivered spreadsheet. The same information is included in the workbook itself
(sheet *Data Dictionary*); this page documents the `leads` schema, the system columns and the statuses.

## `leads` schema (one row per business)

| Column | Key | Type | Required | Description | Verification |
|---|---|---|---|---|---|
| Company name | `company_name` | string | yes | Trade name as used publicly | website / VIES name |
| Legal entity name | `legal_name` | string | | Registered name from the VAT register | VIES |
| Category | `category` | string | yes | Main category (restaurant, cafe, office…) | - |
| Subcategory | `subcategory` | string | | Cuisine, industry, brand… | - |
| Street / No. | `street`, `house_number` | string | | | website / VIES address |
| Postcode | `postcode` | postcode | | 4 digits for BE | format |
| City / District | `city`, `district` | string | | District = sub-municipality (Marke, Heule…) | - |
| Country | `country` | string | | ISO code | - |
| Phone | `phone` | phone | | `+32 56 20 55 88` (E.164 in JSON) | libphonenumber + website |
| E-mail | `email` | email | | lower-case | syntax + MX + website |
| Website | `website` | url | | canonical URL | live fetch + name match |
| LinkedIn / Facebook / Instagram | `linkedin`, `facebook`, `instagram` | social | | canonical profile URLs | website |
| VAT number | `vat_number` | vat | | `BE0629985405` | checksum + VIES |
| Opening hours | `opening_hours` | text | | OSM / Google format | - |
| Description | `description` | text | | source or website meta description | - |
| Latitude / Longitude | `latitude`, `longitude` | number | | WGS84, 6 decimals | range |

`companies` (registers) adds `industry`, `founded`, `employees`, `status_note`; `products` (catalogues)
has `title`, `price`, `currency`, `rating`, `availability`, `stock`, `category`, `upc`, `description`,
`product_url`, `image_url`. Run `dataharvest schemas NAME` for the exact list.

## System columns (every export)

| Column | Description |
|---|---|
| Record ID | Stable id (`KOR-0001`), referenced by the Duplicates and Needs Review sheets |
| Verification status | VERIFIED / PARTIALLY_VERIFIED / UNVERIFIED / NEEDS_REVIEW (see below) |
| Review flags | One issue per line. Lines starting with `note:` are informational (e.g. legal name differs from trade name) |
| Completeness % | Share of schema fields filled; required fields weigh double |
| *Field* check | Per key field (name, website, e-mail, phone, VAT): `status - reason \| other values seen: …` |
| Source | Primary source label (osm, wikidata, client_list…) |
| Source URL | Link to the record in the source (OpenStreetMap node, Wikidata item, file name) |
| Duplicate group | `DUP-001` when the record was part of a duplicate cluster |
| Extracted at (UTC) | Timestamp of extraction |
| Reviewer decision | Drop-down: Approved / Rejected / Needs fix / Contacted - empty on delivery |
| Reviewer notes | Free text for the reviewer |

## Field status values

| Status | Meaning |
|---|---|
| `verified` | confirmed by an independent check or a second source (the reason is in the check column) |
| `unverified` | present and well-formed, but single-source / not confirmable |
| `conflict` | sources disagree; the delivered value is the most reliable one, the others are listed |
| `invalid` | failed a check (bad format, dead website, VAT rejected, domain cannot receive mail) |
| `missing` | no value found |

## JSON export

`<project>_<date>.json` contains, per record: `record_id`, `source`, `source_url`, `status`, `flags`,
`completeness`, `duplicate_group`, `duplicate_of`, `extracted_at`, `fields` (value, source, status, note,
candidates per field), `checks` (website liveness, mail-domain lookup, phone details, VIES answer) and
`raw` (the original payload, e.g. all OpenStreetMap tags) - the full audit trail.
