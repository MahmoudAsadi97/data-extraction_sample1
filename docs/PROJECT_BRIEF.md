# Example project brief

This is the brief the sample projects in `projects/` implement. It is written the way a client typically
sends instructions for a data-extraction / lead-generation job. Names of clients are placeholders.

## 1. Context

The client needs a clean, verified database of businesses to feed a sales/prospecting process and a
market study. The data must be collected from public online sources, organised in a spreadsheet with a
fixed structure, checked for errors and duplicates, and delivered with a note on anything that could not
be verified.

## 2. Tasks

### Task A - Restaurants & cafés in Kortrijk (BE) -> `projects/kortrijk_restaurants.yaml`

Collect **every restaurant and café located in the municipality of Kortrijk** (including Marke, Heule,
Bissegem, Bellegem, Aalbeke, Kooigem and Rollegem). For each business:

- trade name, category (restaurant / café) and cuisine or subcategory
- full address (street, number, postcode, city), coordinates
- phone, e-mail, website
- Facebook, Instagram and LinkedIn pages
- VAT / enterprise number and the registered legal name
- opening hours

### Task B - B2B prospect list -> `projects/kortrijk_b2b_offices.yaml`

IT, software, consulting, telecom and marketing offices in Kortrijk with verified website, e-mail, phone,
LinkedIn page and VAT number. When a Google Maps or Apollo account is available, add those sources and
merge the results with the OpenStreetMap records.

### Task C - Company register West Flanders -> `projects/west_flanders_companies.yaml`

All companies headquartered in the province of West Flanders that are documented in a public
structured source (Wikidata), with industry, founding year, size, website, LinkedIn and VAT number.
Companies that no longer exist must be marked, not deleted.

### Task D - Seed list enrichment -> `projects/seed_list_enrichment.yaml`

The client provides a list (CSV/Excel) with company names and partial contact data collected by an
intern. Complete the missing websites/e-mails/phones, check the existing ones, remove duplicates and
report which rows could not be verified.

### Task E - Product catalogue -> `projects/books_catalogue.yaml`

Extract a product catalogue (title, price, currency, rating, availability, stock, UPC, description, URL)
from a paginated listing website, following each product page for the details.

## 3. Rules

1. **Accuracy over volume.** Only add data you have seen in a source. Do not guess or "complete" values
   from memory.
2. **Verify before adding.** Contact details must be cross-checked (website live, e-mail domain exists,
   phone valid for Belgium, VAT number registered). Record *how* each value was verified.
3. **Flag, don't hide.** Anything that could not be verified, or where sources disagree, is delivered with
   a clear flag and a reason, in a separate review list.
4. **No duplicates.** The same business may appear in several sources or several times in one source
   (different spellings, branches). Merge what is certainly the same entity; flag what might be.
5. **Consistent format.** One format per column: international phone numbers, lower-case e-mails, full
   URLs with scheme, postcode as 4 digits, VAT as `BE0123456789`.
6. **Respect the sources.** Follow robots.txt and rate limits, identify the tool in the User-Agent, collect
   business data only (no personal data beyond a business contact address).
7. **Traceability.** Every row keeps a link to its source record and the extraction date.

## 4. Deliverables

- Excel workbook with: the database, a *Needs Review* sheet, a *Duplicates* sheet, a *Summary* with counts
  and completeness, a *Run Log* and a *Data Dictionary*.
- The same data as CSV (importable into Google Sheets / a CRM) and JSON (with provenance).
- A short run report: records collected, verified, flagged, duplicates, sources used, warnings.
- Optional: the database pushed to a shared Google Sheet.

## 5. Deadline & communication

Agreed per task (typically 3-5 working days for a municipality-sized list). Progress and blockers are
reported when they occur, with the run report attached to the delivery.
