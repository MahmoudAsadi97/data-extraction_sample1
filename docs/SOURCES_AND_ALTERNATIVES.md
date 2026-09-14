# Sources and commercial-use considerations

Checked against provider documentation on 14 September 2026. Availability, pricing and permitted use
can change. The recommended first product uses customer-owned inputs; source availability does not
automatically grant rights to redistribute a dataset.

| Source | Implemented capability | Commercial limitation / reference |
|---|---|---|
| Customer CSV/XLSX | Offline audit and optional extraction import | Customer supplies the list and determines permitted use; no enrichment needed for the workspace |
| OpenStreetMap / Overpass | Tagged locations and business fields | Coverage varies. Keep attribution and review [ODbL obligations](https://www.openstreetmap.org/copyright) |
| Nominatim | Resolves an extraction area | Public service has an aggregate maximum of 1 request/second and restrictions on bulk/periodic use; use an appropriate provider for recurring production workloads. [Usage policy](https://operations.osmfoundation.org/policies/nominatim/) |
| Wikidata | Structured company profiles | Incomplete coverage; structured data is provided under CC0. [Data access guidance](https://www.wikidata.org/wiki/Wikidata:Data_access) |
| HTML listing | CSS fields, pagination and detail pages | Site-specific permission/terms and robots rules apply; a generic scraper cannot establish redistribution rights |
| Google Places | Optional company/location lookup | Review caching/storage restrictions, attribution and EEA-specific terms. Do not treat this integration as a license to resell a company database. [Places policies](https://developers.google.com/maps/documentation/places/web-service/policies) |
| Apollo | Optional company search/enrichment | Requires the customer's entitled account and current contractual rights; fixture tests do not verify paid access |
| Google Custom Search | Legacy optional website discovery | Closed to new customers. Existing customers must transition by January 1, 2027. [API overview](https://developers.google.com/custom-search/v1/overview) |
| DuckDuckGo HTML | Optional website discovery | Best effort and rate-limited; do not promise service availability |
| VIES | Optional VAT registration check | Availability varies; a positive registration check does not establish business trustworthiness. [VIES guidance](https://europa.eu/youreurope/business/taxation/vat/check-vat-number-vies/index_en.htm) |

## Verification meanings

- Format validity and field completeness are separate from identity verification.
- A matched company name on a fetched page is a heuristic supporting signal. Contacts from a page
  without a matching name are not applied to the record.
- MX records indicate that a domain advertises mail routing. They do not verify that an individual
  mailbox exists, accepts messages, or consents to contact. Null MX explicitly refuses mail.
- Phone formatting does not establish that a phone is active or owned by the company.
- Same value in two source labels may not mean independent origin; provenance remains visible.
- Online workflows can encounter individual contact information. The code does not guarantee that
  all collected addresses or phone numbers are non-personal; operator review remains necessary.

## Request behavior

Website requests use identification, per-host delays, public-address checks, bounded redirects and
matching robots rules. Robots denial or temporary unavailability defers collection. No login-wall,
CAPTCHA or access-control bypass is implemented. Cache files can contain source data and need customer
retention controls. `--no-cache` requests fresh data; expired data is not silently substituted on error.

The Nominatim delay is enforced per client process. Shared or parallel deployments require a centralized
rate limit and provider configuration; they are outside this local pilot's scope.
