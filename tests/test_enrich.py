"""Enrichment: website analysis, web search, VIES - against recorded pages/responses."""

from __future__ import annotations

import requests
import responses
from bs4 import BeautifulSoup

from conftest import fixture_json, fixture_text
from dataharvest.enrich.search import DDG_HTML_URL, WebSearch, is_directory, parse_ddg_html
from dataharvest.enrich.vies import VIES_URL, ViesClient
from dataharvest.enrich.website import (
    WebsiteEnricher,
    extract_emails,
    extract_phones,
    extract_socials,
    extract_vat_numbers,
    name_matches,
    pick_social,
    rank_emails,
)

SITE = "https://www.brasseriedeleie.be"


def _soup_and_text(html: str):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup, soup.get_text(" ", strip=True)


class TestExtractors:
    def test_emails_from_mailto_text_and_obfuscated(self):
        soup, text = _soup_and_text(fixture_text("site_home.html"))
        emails = extract_emails(soup, text)
        assert "reservaties@brasseriedeleie.be" in emails
        assert "fotograaf@studio-lens.be" in emails
        assert "tracking@analytics-vendor.io" not in emails  # inside <script>, removed
        soup2, text2 = _soup_and_text(fixture_text("site_contact.html"))
        assert "info@brasseriedeleie.be" in extract_emails(soup2, text2)

    def test_junk_emails_filtered(self):
        soup, text = _soup_and_text('<p>logo@2x.png hero@3x.jpg name@domain.com contact@example.com x@sentry.io</p><a href="mailto:info@shop.be">m</a>')
        assert extract_emails(soup, text) == ["info@shop.be"]

    def test_phones(self):
        soup, text = _soup_and_text(fixture_text("site_contact.html"))
        phones = extract_phones(soup, text, "BE")
        assert "+3256123456" in phones and "+32478123456" in phones

    def test_socials_skip_share_links_and_prefer_own_profile(self):
        soup, _ = _soup_and_text(fixture_text("site_home.html"))
        socials = extract_socials(soup, SITE)
        assert socials["facebook"] == ["https://www.facebook.com/brasseriedeleie"]
        assert socials["instagram"] == ["https://www.instagram.com/brasserie_de_leie"]
        assert socials["linkedin"] == ["https://www.linkedin.com/company/webbureau-x", "https://www.linkedin.com/company/brasserie-de-leie"]
        assert pick_social(socials["linkedin"], "Brasserie De Leie") == "https://www.linkedin.com/company/brasserie-de-leie"
        assert pick_social(socials["linkedin"], None) == "https://www.linkedin.com/company/webbureau-x"

    def test_vat_numbers_need_context_and_checksum(self):
        _, text = _soup_and_text(fixture_text("site_contact.html"))
        assert extract_vat_numbers(text, "BE") == ["BE0629985405"]  # the ".404" variant fails the checksum
        _, text_home = _soup_and_text(fixture_text("site_home.html"))
        assert extract_vat_numbers(text_home, "BE") == ["BE0629985405"]
        assert extract_vat_numbers("call 0472 35 49 04 today", "BE") == []  # phone numbers are not VAT numbers

    def test_name_match(self):
        assert name_matches("Brasserie De Leie", "Brasserie De Leie | Restaurant", "welkom")[0]
        assert name_matches("Brasserie De Leie BV", "Home", "Welkom bij Brasserie De Leie")[0]
        assert not name_matches("Totally Different Name", "Home", "Welkom bij Brasserie De Leie")[0]

    def test_rank_emails(self):
        ranked = rank_emails(["fotograaf@studio-lens.be", "reservaties@brasseriedeleie.be", "info@brasseriedeleie.be"], "brasseriedeleie.be")
        assert ranked[0] == "info@brasseriedeleie.be" and ranked[-1] == "fotograaf@studio-lens.be"


class TestWebsiteEnricher:
    @responses.activate
    def test_full_analysis(self, http):
        responses.add(responses.GET, SITE + "/", body=fixture_text("site_home.html"), content_type="text/html; charset=utf-8")
        responses.add(responses.GET, SITE + "/contact", body=fixture_text("site_contact.html"), content_type="text/html")
        responses.add(responses.GET, SITE + "/over-ons", status=404, body="nope", content_type="text/html")
        result = WebsiteEnricher(http, country="BE", max_pages=3, respect_robots=False).analyse(SITE + "/", "Brasserie De Leie")
        assert result.ok and result.liveness == "live" and result.status == 200
        assert result.title.startswith("Brasserie De Leie")
        assert result.description.startswith("Brasserie De Leie: Belgische keuken")
        assert result.name_match is True
        assert result.emails[0] == "info@brasseriedeleie.be"
        assert "+3256123456" in result.phones
        assert result.vat_numbers == ["BE0629985405"]
        assert result.socials["facebook"] == "https://www.facebook.com/brasseriedeleie"
        assert result.socials["linkedin"] == "https://www.linkedin.com/company/brasserie-de-leie"  # not the web agency's page
        assert len(result.pages_fetched) == 2  # home + contact (over-ons failed)

    @responses.activate
    def test_dead_and_redirected_sites(self, http):
        responses.add(responses.GET, "https://dead.example.org/", status=404, body=fixture_text("site_dead.html"), content_type="text/html")
        dead = WebsiteEnricher(http, respect_robots=False).analyse("https://dead.example.org/")
        assert not dead.ok and dead.liveness == "dead"
        responses.add(responses.GET, "https://old.example.org/", status=301, headers={"Location": "https://new.example.org/"})
        responses.add(responses.GET, "https://new.example.org/", body="<html><title>New</title><body>hello</body></html>", content_type="text/html")
        moved = WebsiteEnricher(http, respect_robots=False).analyse("https://old.example.org/", "New")
        assert moved.ok and moved.liveness == "redirected" and moved.final_url == "https://new.example.org/"

    @responses.activate
    def test_connection_error(self, http):
        responses.add(responses.GET, "https://down.example.org/", body=requests.exceptions.ConnectionError("boom"))
        result = WebsiteEnricher(http, respect_robots=False).analyse("https://down.example.org/")
        assert not result.ok and result.liveness == "unreachable" and "connection error" in result.error

    @responses.activate
    def test_non_html(self, http):
        responses.add(responses.GET, "https://pdf.example.org/", body="%PDF-1.4", content_type="application/pdf")
        result = WebsiteEnricher(http, respect_robots=False).analyse("https://pdf.example.org/")
        assert result.ok and "not an HTML page" in result.error


class TestSearch:
    def test_parse_ddg(self):
        hits = parse_ddg_html(fixture_text("ddg_results.html"))
        assert [h.url for h in hits][:2] == ["https://hollywok.be/", "https://hollywok.be/restaurant/"]
        assert all("example-ads" not in h.url for h in hits)  # ad skipped
        assert hits[0].title == "HOME - Hollywok" and "Hollywok" in hits[0].snippet

    def test_directory_filter(self):
        assert is_directory("https://www.facebook.com/HollywokKortrijk/")
        assert is_directory("https://www.tripadvisor.be/Restaurant_Review-x.html")
        assert not is_directory("https://hollywok.be/")

    @responses.activate
    def test_find_website(self, http):
        responses.add(responses.GET, DDG_HTML_URL, body=fixture_text("ddg_results.html"), content_type="text/html")
        search = WebSearch(http, provider="duckduckgo", delay=0)
        hit = search.find_website("Hollywok", "Kortrijk")
        assert hit is not None and hit.url == "https://hollywok.be/" and hit.provider == "duckduckgo"
        assert search.queries == 1

    @responses.activate
    def test_rate_limit_disables_after_failures(self, http):
        responses.add(responses.GET, DDG_HTML_URL, status=202, body="anomaly")
        search = WebSearch(http, provider="duckduckgo", delay=0)
        for _ in range(3):
            assert search.search("x") == []
        assert not search.available and "rate-limited" in search.disabled_reason

    def test_provider_auto(self, http, monkeypatch):
        monkeypatch.delenv("GOOGLE_CSE_API_KEY", raising=False)
        assert WebSearch(http).provider == "duckduckgo"
        monkeypatch.setenv("GOOGLE_CSE_API_KEY", "k")
        monkeypatch.setenv("GOOGLE_CSE_ID", "cx")
        assert WebSearch(http).provider == "google_cse"


class TestVies:
    @responses.activate
    def test_valid_invalid_and_busy(self, http):
        responses.add(responses.GET, VIES_URL.format(country="BE", number="0629985405"), json=fixture_json("vies_valid.json"))
        responses.add(responses.GET, VIES_URL.format(country="BE", number="0000000097"), json=fixture_json("vies_invalid.json"))
        responses.add(responses.GET, VIES_URL.format(country="BE", number="0473191041"), json=fixture_json("vies_busy.json"))
        responses.add(responses.GET, VIES_URL.format(country="BE", number="0473191041"), json=fixture_json("vies_busy.json"))
        responses.add(responses.GET, VIES_URL.format(country="BE", number="0473191041"), json=fixture_json("vies_busy.json"))
        client = ViesClient(http, delay=0)
        ok = client.check("BE 0629.985.405")
        assert ok.valid is True and ok.name == "BV HUMICO" and ok.city == "Kortrijk" and ok.postcode == "8510"
        bad = client.check("BE0000000097")
        assert bad.valid is False
        busy = client.check("BE0473191041")
        assert busy.valid is None and "MS_MAX_CONCURRENT_REQ" in busy.error
        assert client.check("BE0629985405") is ok  # cached
        assert client.check("BE0629985404").valid is False  # checksum fails locally, no request
        assert client.requests_made == 5
