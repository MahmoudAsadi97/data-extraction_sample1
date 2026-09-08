# Sources, alternatives and rules of use

## Sources implemented

| Source (`type`) | What it gives | Key needed | Cost / limits | Notes |
|---|---|---|---|---|
| `osm_overpass` - OpenStreetMap via Overpass API | businesses/places by tag inside an area: name, address, phone, e-mail, website, socials, opening hours, VAT (`ref:vatin`), coordinates | no | free; be polite (one query per run, mirrors used on 429/504) | Area resolved with Nominatim (1 req/s). Coverage depends on mapping activity - very good in Belgian cities. ODbL licence: keep attribution. |
| `wikidata` - Wikidata SPARQL | company profiles: label, legal name, website, founding/dissolution dates, industries, HQ, employees, VAT, LinkedIn, phone, e-mail | no | free; queries up to 60 s | Structured and citable, but only for companies notable enough to have an item. |
| `html_list` - any listing website | whatever the CSS selectors describe, with pagination and detail pages | no | depends on the site; delay configurable | Use only on sites whose terms allow it; robots.txt is honoured. The demo uses books.toscrape.com, a public scraping sandbox. |
| `csv_import` - CSV/Excel | a client list, a LinkedIn Sales Navigator or Apollo export, manual research | no | - | Column aliases mapped automatically; explicit `mapping` for the rest. |
| `google_places` - Google Maps Places API (New) | text search: name, address components, phone, website, coordinates, business status, hours, rating | `GOOGLE_MAPS_API_KEY` | paid per request (monthly credit) | Field mask limited to what the schema needs. |
| `apollo` - Apollo.io | company search by location/keywords/size; enrichment by domain (LinkedIn URL, industry, size, founding year) | `APOLLO_API_KEY` | credits per request | Also used as an enrichment step when enabled. |

Web search providers (used only to find a missing website): `google_cse` (Programmable Search JSON API,
100 free queries/day, needs `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID`) and DuckDuckGo HTML (no key,
rate-limited - the tool backs off after repeated refusals and reports it).

Verification services: EU **VIES** VAT validation (`ec.europa.eu/taxation_customs/vies/rest-api`, free, no key,
concurrency-limited - 1 request/s with retries), DNS MX lookups (system resolver, DNS-over-HTTPS fallback).

## Mapping the brief's "preferred tools"

| Brief | Used here | Why |
|---|---|---|
| Google Maps | `osm_overpass` by default, `google_places` when a key is provided | Same data model; OSM is free and has no request quota. Both can run in one project and are de-duplicated together. |
| Google Search | `google_cse` when keys are set, DuckDuckGo otherwise | A search result is never trusted by itself: the page must show the company name before the website counts as verified. |
| LinkedIn | export/CSV import + LinkedIn URLs found on company websites, Wikidata and Apollo | LinkedIn's terms prohibit automated collection; profile URLs are collected from sources that publish them. |
| Apollo | `apollo` source and enrichment | Requires an account/key; skipped with a warning otherwise. |
| Google Sheets | `gspread` export, or CSV import | Service-account setup in `GOOGLE_SHEETS_SETUP.md`. |
| Excel | `openpyxl` workbook | Formatting, formulas, validation, hyperlinks. |

## Rules followed

1. **robots.txt** is checked before fetching company websites (`enrichment.website.respect_robots`).
2. **Rate limits**: per-host minimum delay, single-worker access to Nominatim/VIES/search, exponential
   back-off on 429/5xx, mirror fallback for Overpass.
3. **Identification**: every request carries a User-Agent naming the tool and the repository, plus a contact
   e-mail when `DATAHARVEST_CONTACT_EMAIL` is set.
4. **Business data only**: company contact channels (info@, +32 56 …) are collected; personal names, private
   phone numbers or e-mails of individuals are not targeted. Sources that publish personal data (LinkedIn
   profiles) are not scraped.
5. **Caching**: pages are cached for 7 days so re-runs do not hit the sites again.
6. **Attribution**: OpenStreetMap data is © OpenStreetMap contributors (ODbL); Wikidata is CC0.
7. **No circumvention**: no CAPTCHA solving, no login walls, no JavaScript emulation to bypass protections.

## Choosing a source for a new task

| Need | Start with |
|---|---|
| all businesses of a type in a town/region | `osm_overpass` (add `google_places` for completeness) |
| companies by industry/size with firmographics | `apollo` (key) or `wikidata` (large companies) |
| a client's own list to complete | `csv_import` |
| a directory or catalogue website | `html_list` |
| verifying an existing spreadsheet | `dataharvest validate` |
