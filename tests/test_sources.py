"""Source extractors, tested against recorded API responses (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
import responses

from conftest import fixture_json, fixture_text
from dataharvest.config import ProjectConfig, SourceConfig
from dataharvest.sources import SourceError, all_sources, get_source_class
from dataharvest.sources.html_list import HtmlListSource, extract_field, find_next_page, parse_list_page
from dataharvest.sources.nominatim import NOMINATIM_URL, resolve_area
from dataharvest.sources.osm_overpass import (
    OVERPASS_ENDPOINTS,
    OsmOverpassSource,
    build_query,
    element_to_values,
    parse_tag_filter,
)
from dataharvest.sources.wikidata import SPARQL_URL, WikidataSource, binding_to_values


def project(tmp_path: Path, **overrides) -> ProjectConfig:
    data = {"project": {"name": "t", "country": "BE"}, "schema": "leads", "sources": [{"type": "csv_import", "path": "x.csv"}]}
    data.update(overrides)
    cfg = ProjectConfig(**data)
    cfg._path = tmp_path / "projects" / "t.yaml"
    return cfg


class TestSchemaOverrides:
    def test_overrides_change_required_flag(self, tmp_path):
        from dataharvest.schema import apply_overrides, load_schema

        cfg = project(tmp_path, schema_overrides={"category": {"required": False}})
        assert cfg.schema_def.field("category").required is False
        assert load_schema("leads").field("category").required is True  # built-in schema untouched
        try:
            apply_overrides(load_schema("leads"), {"nope": {"required": False}})
        except ValueError as exc:
            assert "unknown field" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")


class TestRegistry:
    def test_all_sources_registered(self):
        assert set(all_sources()) >= {"osm_overpass", "wikidata", "html_list", "csv_import", "google_places", "apollo"}

    def test_unknown_source(self):
        with pytest.raises(SourceError):
            get_source_class("nope")

    def test_keyed_sources_report_missing_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        ok, reason = get_source_class("google_places").availability()
        assert not ok and "GOOGLE_MAPS_API_KEY" in reason


class TestOverpass:
    def test_tag_filters(self):
        assert parse_tag_filter("amenity=restaurant") == '["amenity"="restaurant"]'
        assert parse_tag_filter("office") == '["office"]'
        assert parse_tag_filter("shop=*") == '["shop"]'
        assert parse_tag_filter("office=it|software") == '["office"~"^(it|software)$"]'

    def test_build_query_area_and_bbox(self):
        q = build_query(["amenity=restaurant", "amenity=cafe"], area_id=3600961284)
        assert "area(3600961284)->.searchArea;" in q and q.count("nwr[") == 2 and "out center tags;" in q
        q2 = build_query(["amenity=cafe"], bbox=(50.7, 3.2, 50.9, 3.4))
        assert "(50.7,3.2,50.9,3.4)" in q2
        with pytest.raises(SourceError):
            build_query([], area_id=1)

    def test_element_mapping(self):
        data = fixture_json("overpass_kortrijk.json")
        by_id = {e["id"]: e for e in data["elements"]}
        basta = element_to_values(by_id[9977230081], ["amenity=restaurant"])
        assert basta["company_name"] == "Basta!"
        assert basta["email"] == "info@bastakortrijk.be"  # contact:email preferred
        assert basta["phone"] == "+32 56 49 37 49"
        assert basta["facebook"] == "bastakortrijk"
        assert basta["category"] == "restaurant" and basta["subcategory"] == "mediterranean"
        frituur = element_to_values(by_id[408034848], ["amenity=restaurant"])
        assert frituur["vat_number"] == "BE0629.985.405"
        assert frituur["latitude"] == pytest.approx(50.8048018)  # way -> center
        unnamed = element_to_values(by_id[1289875895], ["amenity=restaurant"])
        assert unnamed["company_name"] is None

    @responses.activate
    def test_extract_end_to_end(self, http, tmp_path):
        responses.add(responses.GET, NOMINATIM_URL, json=fixture_json("nominatim_kortrijk.json"))
        responses.add(responses.POST, OVERPASS_ENDPOINTS[0], json=fixture_json("overpass_kortrijk.json"))
        cfg = project(tmp_path)
        src = OsmOverpassSource(SourceConfig(type="osm_overpass", name="osm", area="Kortrijk", country="BE", tags=["amenity=restaurant", "amenity=cafe"]), http, cfg)
        records = list(src.extract())
        assert len(records) == 60
        assert records[1].values["company_name"] == "Hollywok"
        assert records[1].source_url == "https://www.openstreetmap.org/node/703602654"
        body = unquote_plus(responses.calls[1].request.body)
        assert "area(3600961284)" in body  # resolved through Nominatim (municipality relation)
        assert all(r.values.get("country") == "BE" for r in records)
        limited = list(OsmOverpassSource(SourceConfig(type="osm_overpass", area="Kortrijk", tags=["amenity=cafe"]), http, cfg).extract(limit=5))
        assert len(limited) == 5

    @responses.activate
    def test_falls_back_to_next_mirror(self, http, tmp_path):
        responses.add(responses.GET, NOMINATIM_URL, json=[])
        responses.add(responses.POST, OVERPASS_ENDPOINTS[0], status=504)
        responses.add(responses.POST, OVERPASS_ENDPOINTS[1], json=fixture_json("overpass_kortrijk.json"))
        src = OsmOverpassSource(SourceConfig(type="osm_overpass", area="Kortrijk", tags=["amenity=cafe"]), http, project(tmp_path))
        records = list(src.extract())
        assert records and src.warnings  # warned about the Nominatim fallback
        assert 'area["name"="Kortrijk"]' in unquote_plus(responses.calls[-1].request.body)


class TestNominatim:
    @responses.activate
    def test_prefers_municipality_boundary(self, http):
        responses.add(responses.GET, NOMINATIM_URL, json=fixture_json("nominatim_kortrijk.json"))
        area = resolve_area(http, "Kortrijk", "BE")
        assert area is not None and area.osm_id == 961284 and area.overpass_area_id == 3600961284
        assert area.address_type == "city"

    @responses.activate
    def test_no_result(self, http):
        responses.add(responses.GET, NOMINATIM_URL, json=[])
        assert resolve_area(http, "Nowhere", "BE") is None


class TestWikidata:
    def test_binding_mapping(self):
        rows = fixture_json("wikidata_westflanders.json")["results"]["bindings"]
        values, qid, flags = binding_to_values(rows[0])
        assert qid == "Q807964" and values["company_name"] == "Barco" and values["vat_number"] == "BE0473191041"
        assert values["founded"] == "1934" and values["employees"] == "3600"
        vliz = binding_to_values(rows[1])[0]
        assert vliz["linkedin"] == "https://www.linkedin.com/company/vliz---flanders-marine-institute"
        revor = binding_to_values(rows[6])[0]
        assert revor["email"] == "info@revorgroup.be"
        dissolved = binding_to_values(next(r for r in rows if r.get("dissolved")))
        assert any("dissolved" in f for f in dissolved[2]) and dissolved[0]["status_note"].startswith("dissolved 2013")
        no_label = binding_to_values(next(r for r in rows if r["companyLabel"]["value"].startswith("Q")))
        assert no_label[0]["company_name"] is None and no_label[2]

    @responses.activate
    def test_extract(self, http, tmp_path):
        responses.add(responses.GET, SPARQL_URL, json=fixture_json("wikidata_westflanders.json"))
        cfg = project(tmp_path, schema="companies")
        src = WikidataSource(SourceConfig(type="wikidata", headquarters_in="Q1113"), http, cfg)
        records = list(src.extract())
        assert len(records) == 27
        assert records[0].source_url == "https://www.wikidata.org/wiki/Q807964"
        assert "wd:Q1113" in responses.calls[0].request.url or "Q1113" in responses.calls[0].request.url

    def test_requires_place_or_query(self, http, tmp_path):
        src = WikidataSource(SourceConfig(type="wikidata"), http, project(tmp_path))
        with pytest.raises(SourceError):
            list(src.extract())


BOOK_FIELDS = {
    "title": {"selector": "h3 a", "attr": "title"},
    "price": {"selector": "p.price_color", "regex": r"([0-9]+\.[0-9]+)"},
    "currency": {"selector": "p.price_color", "regex": "([£$€])", "map": {"£": "GBP"}},
    "rating": {"selector": "p.star-rating", "attr": "class", "regex": r"star-rating (\w+)", "map": {"One": 1, "Three": 3, "Four": 4, "Five": 5}},
    "availability": {"selector": "p.instock.availability"},
    "product_url": {"selector": "h3 a", "attr": "href", "absolute": True},
}


class TestHtmlList:
    def test_parse_page(self):
        items = parse_list_page(fixture_text("books_page1.html"), "https://books.toscrape.com/catalogue/page-1.html", "article.product_pod", BOOK_FIELDS)
        assert len(items) == 3
        assert items[0]["title"] == "A Light in the Attic" and items[0]["price"] == "51.77" and items[0]["currency"] == "GBP"
        assert items[0]["rating"] == 3 and items[1]["rating"] == 1  # "One" is not in the map -> raw value kept? no: default
        assert items[0]["product_url"] == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
        assert items[0]["availability"] == "In stock"

    def test_next_page(self):
        assert find_next_page(fixture_text("books_page1.html"), "https://books.toscrape.com/catalogue/page-1.html", "li.next a") == "https://books.toscrape.com/catalogue/page-2.html"
        assert find_next_page(fixture_text("books_page2.html"), "https://books.toscrape.com/catalogue/page-2.html", "li.next a") is None

    def test_extract_field_defaults(self):
        from bs4 import BeautifulSoup

        node = BeautifulSoup("<div><span class='a'>x</span></div>", "lxml")
        assert extract_field(node, {"selector": ".missing", "default": "n/a"}, "https://x") == "n/a"
        assert extract_field(node, ".a", "https://x") == "x"

    @responses.activate
    def test_extract_with_pagination_and_detail(self, http, tmp_path):
        base = "https://books.toscrape.com/catalogue/"
        responses.add(responses.GET, base + "page-1.html", body=fixture_text("books_page1.html"), content_type="text/html")
        responses.add(responses.GET, base + "page-2.html", body=fixture_text("books_page2.html"), content_type="text/html")
        for slug in ("a-light-in-the-attic_1000", "tipping-the-velvet_999", "soumission_998", "sharp-objects_997"):
            responses.add(responses.GET, f"{base}{slug}/index.html", body=fixture_text("book_detail.html"), content_type="text/html")
        cfg = project(tmp_path, schema="products")
        src = HtmlListSource(SourceConfig(
            type="html_list", start_url=base + "page-1.html", item_selector="article.product_pod", fields=BOOK_FIELDS,
            next_page_selector="li.next a", max_pages=5, delay_seconds=0,
            detail={"url_field": "product_url", "fields": {"upc": {"selector": "table.table-striped tr:nth-of-type(1) td"},
                                                          "stock": {"selector": "div.product_main p.availability", "regex": r"\((\d+) available\)"}}},
        ), http, cfg)
        records = list(src.extract())
        assert len(records) == 5  # 3 + 2 (one is a duplicate across pages, kept for dedupe to catch)
        assert records[0].values["upc"] == "a897fe39b1053632" and records[0].values["stock"] == "22"
        assert records[0].source_url.endswith("a-light-in-the-attic_1000/index.html")


class TestCsvImport:
    def test_import_with_mapping_and_aliases(self, http, tmp_path):
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text(fixture_text("seed_list.csv"), encoding="utf-8")
        cfg = project(tmp_path)
        cfg._path = tmp_path / "t.yaml"
        src = get_source_class("csv_import")(SourceConfig(type="csv_import", path=str(csv_path), mapping={"Tel": "phone"}), http, cfg)
        records = list(src.extract())
        assert len(records) == 5
        first = records[0].values
        assert first["company_name"] == "Hollywok" and first["website"] == "hollywok.be" and first["phone"] == "056/20.55.88"
        assert first["email"] == "info@hollywok.be" and first["postcode"] == "8500" and first["country"] == "BE"
        assert first["description"] == "Asian restaurant under the cinema"  # "Notes" mapped via the alias table
        assert not src.warnings

    def test_missing_file(self, http, tmp_path):
        cfg = project(tmp_path)
        cfg._path = tmp_path / "t.yaml"
        src = get_source_class("csv_import")(SourceConfig(type="csv_import", path="missing.csv"), http, cfg)
        with pytest.raises(SourceError):
            list(src.extract())


class TestGooglePlacesAndApollo:
    @responses.activate
    def test_google_places_mapping(self, http, tmp_path, monkeypatch):
        from dataharvest.sources.google_places import PLACES_URL, GooglePlacesSource

        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
        page = {
            "places": [{
                "id": "ChIJ123", "displayName": {"text": "Hollywok"}, "formattedAddress": "President Kennedylaan 100, 8500 Kortrijk, Belgium",
                "addressComponents": [
                    {"longText": "100", "types": ["street_number"]}, {"longText": "President Kennedylaan", "types": ["route"]},
                    {"longText": "Kortrijk", "types": ["locality"]}, {"longText": "8500", "types": ["postal_code"]},
                    {"longText": "Belgium", "shortText": "BE", "types": ["country"]},
                ],
                "internationalPhoneNumber": "+32 56 20 55 88", "websiteUri": "https://hollywok.be/", "location": {"latitude": 50.805, "longitude": 3.275},
                "businessStatus": "CLOSED_TEMPORARILY", "primaryType": "asian_restaurant", "types": ["asian_restaurant", "restaurant"],
                "regularOpeningHours": {"weekdayDescriptions": ["Monday: 11:30 AM – 10:00 PM"]}, "googleMapsUri": "https://maps.google.com/?cid=1",
            }],
        }
        responses.add(responses.POST, PLACES_URL, json=page)
        src = GooglePlacesSource(SourceConfig(type="google_places", text_query="restaurants in Kortrijk"), http, project(tmp_path))
        recs = list(src.extract())
        assert len(recs) == 1
        v = recs[0].values
        assert v["company_name"] == "Hollywok" and v["street"] == "President Kennedylaan" and v["house_number"] == "100"
        assert v["postcode"] == "8500" and v["country"] == "BE" and v["category"] == "asian restaurant"
        assert recs[0].flags and "closed temporarily" in recs[0].flags[0]
        assert responses.calls[0].request.headers["X-Goog-Api-Key"] == "test-key"
        assert json.loads(responses.calls[0].request.body)["textQuery"] == "restaurants in Kortrijk"

    def test_google_places_without_key(self, http, tmp_path, monkeypatch):
        from dataharvest.sources.google_places import GooglePlacesSource

        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        src = GooglePlacesSource(SourceConfig(type="google_places", text_query="x"), http, project(tmp_path))
        with pytest.raises(SourceError, match="GOOGLE_MAPS_API_KEY"):
            list(src.extract())

    @responses.activate
    def test_apollo_search_and_enrich(self, http, tmp_path, monkeypatch):
        from dataharvest.sources.apollo import ENRICH_URL, SEARCH_URL, ApolloSource, enrich_by_domain

        monkeypatch.setenv("APOLLO_API_KEY", "apollo-key")
        org = {"id": "1", "name": "Barco", "website_url": "http://www.barco.com", "linkedin_url": "http://www.linkedin.com/company/barco",
               "primary_phone": {"number": "+32 56 23 32 11"}, "city": "Kortrijk", "country": "Belgium", "postal_code": "8500",
               "industry": "electrical/electronic manufacturing", "estimated_num_employees": 3300, "founded_year": 1934, "primary_domain": "barco.com"}
        responses.add(responses.POST, SEARCH_URL, json={"organizations": [org], "pagination": {"page": 1, "total_pages": 1}})
        responses.add(responses.GET, ENRICH_URL, json={"organization": org})
        src = ApolloSource(SourceConfig(type="apollo", locations=["Kortrijk, Belgium"]), http, project(tmp_path, schema="companies"))
        recs = list(src.extract())
        assert len(recs) == 1 and recs[0].values["linkedin"] == "http://www.linkedin.com/company/barco" and recs[0].values["employees"] == 3300
        assert responses.calls[0].request.headers["x-api-key"] == "apollo-key"
        enriched = enrich_by_domain(http, "barco.com")
        assert enriched and enriched["company_name"] == "Barco"
