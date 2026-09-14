"""Regression cases for errors that can corrupt or misrepresent customer data."""

from __future__ import annotations

import csv
import io

import pytest
import responses
from openpyxl import Workbook

from dataharvest.config import OutputConfig, ProjectInfo
from dataharvest.enrich.website import SiteExtraction
from dataharvest.export.flat import export_csv
from dataharvest.models import FieldStatus, Record, RecordStatus
from dataharvest.network import UnsafeURL, validate_public_url
from dataharvest.pipeline import Pipeline
from dataharvest.processing.dedupe import dedupe, find_matches
from dataharvest.processing.verify import MailDomainChecker, verify_email_field
from dataharvest.qa import audit_file, map_columns
from dataharvest.schema import load_schema
from dataharvest.sources.csv_import import read_rows


def record(rid="A", **values):
    rec = Record(rid, "fixture")
    for key, value in values.items():
        rec.set(key, value, "fixture")
    return rec


@pytest.mark.parametrize("value", ['=HYPERLINK("https://bad.example")', " +1+1", "@SUM(1)", "-2+3", "\t=1+1", "\r=1+1", "＝1+1"])
def test_csv_formula_payloads_are_literal(tmp_path, value):
    path = export_csv(tmp_path / "export.csv", [record(company_name=value)], load_schema("leads"))
    row = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))[1]
    assert row[1].startswith("'")


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://10.0.0.1", "http://169.254.169.254/latest/meta-data",
                                  "http://[::1]", "http://[::ffff:127.0.0.1]", "file:///etc/passwd",
                                  "http://localhost.", "http://host.local", "http://user:password@example.org",
                                  "http://example.org:8080", "http://example.org\\@127.0.0.1"])
def test_private_or_ambiguous_urls_rejected(url):
    with pytest.raises(UnsafeURL):
        validate_public_url(url)


def test_dns_mixed_public_private_is_blocked(monkeypatch):
    monkeypatch.setattr("dataharvest.network._resolve", lambda *_: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(UnsafeURL):
        validate_public_url("https://example.org")


@responses.activate
def test_redirect_to_private_destination_never_requested(http):
    responses.get("https://example.org/", status=302, headers={"Location": "http://127.0.0.1/admin"})
    result = http.fetch("https://example.org/", check_robots=False)
    assert not result.ok and result.blocked
    assert len(responses.calls) == 1


@responses.activate
def test_redirect_drops_api_credentials(http):
    responses.get("https://example.org/", status=302, headers={"Location": "https://other.example.org/"})
    responses.get("https://other.example.org/", body="ok")
    http.get("https://example.org/", headers={"X-Api-Key": "fixture-key", "Authorization": "fixture"})
    assert "X-Api-Key" not in responses.calls[1].request.headers
    assert "Authorization" not in responses.calls[1].request.headers


@responses.activate
def test_robots_specific_disallow_cannot_be_overridden_by_wildcard(http):
    http.respect_robots = True
    responses.get("https://example.org/robots.txt", body="User-agent: DataHarvest\nDisallow: /\n\nUser-agent: *\nAllow: /", content_type="text/plain")
    assert not http.allowed_by_robots("https://example.org/private")


@responses.activate
def test_redirect_checks_destination_robots(http):
    http.respect_robots = True
    responses.get("https://example.org/robots.txt", status=404)
    responses.get("https://example.org/", status=302, headers={"Location": "https://other.example.org/"})
    responses.get("https://other.example.org/robots.txt", body="User-agent: *\nDisallow: /", content_type="text/plain")
    assert http.fetch("https://example.org/").blocked
    assert len(responses.calls) == 3


@responses.activate
def test_transient_robots_failure_defers_collection(http):
    http.respect_robots = True
    responses.get("https://example.org/robots.txt", status=503)
    assert not http.allowed_by_robots("https://example.org/")


def test_wrong_company_page_never_verifies_contact(http, leads_project):
    rec = record(company_name="Correct Co", website="https://wrong.example.org", email="hello@correct.example.org")
    site = SiteExtraction(url="https://wrong.example.org", final_url="https://wrong.example.org", ok=True,
                          status=200, name_match=False, name_similarity=20,
                          emails=["hello@correct.example.org"], phones=["+3256205588"])
    Pipeline(leads_project, http=http)._apply_site_extraction(rec, site)
    assert rec.field_status("email") == FieldStatus.UNVERIFIED
    assert not rec.has("phone")
    assert any("identity not confirmed" in f for f in rec.flags)


def test_suffix_domain_is_not_an_owned_mail_domain(http, leads_project):
    rec = record(company_name="Example", website="https://example.org")
    site = SiteExtraction(url="https://example.org", final_url="https://example.org", ok=True,
                          status=200, name_match=True, emails=["person@fakeexample.org"])
    Pipeline(leads_project, http=http)._apply_site_extraction(rec, site)
    assert rec.field_status("email") == FieldStatus.UNVERIFIED


@responses.activate
@pytest.mark.parametrize("status", [2, 5, None])
def test_doh_failure_is_unknown(http, status):
    responses.get("https://dns.google/resolve", json={"Status": status})
    assert MailDomainChecker(http)._resolve_doh("example.org").deliverable is None


@responses.activate
def test_doh_null_mx_rejects_mail(http):
    responses.get("https://dns.google/resolve", json={"Status": 0, "Answer": [{"type": 15, "data": "0 ."}]})
    assert MailDomainChecker(http)._resolve_doh("example.org").deliverable is False


def test_unavailable_dns_keeps_existing_conflict(monkeypatch):
    import dns.exception

    def timeout(*args, **kwargs):
        raise dns.exception.Timeout()

    monkeypatch.setattr("dns.resolver.Resolver.resolve", timeout)
    rec = record(email="info@example.org")
    rec.mark("email", FieldStatus.CONFLICT)
    verify_email_field(rec, MailDomainChecker(None))
    assert rec.field_status("email") == FieldStatus.CONFLICT
    assert rec.checks["email_domain"]["deliverable"] is None


def test_native_null_mx_rejects_mail_without_address_fallback(monkeypatch):
    from types import SimpleNamespace

    def null_mx(self, domain, query_type):
        assert query_type == "MX", "Null MX must not fall back to an address lookup"
        return [SimpleNamespace(exchange=".")]

    monkeypatch.setattr("dns.resolver.Resolver.resolve", null_mx)
    rec = record(email="info@example.org")
    verify_email_field(rec, MailDomainChecker(None))
    assert rec.field_status("email") == FieldStatus.INVALID
    assert rec.checks["email_domain"]["deliverable"] is False


@pytest.mark.parametrize("content", ["Company,Company\nA,B\n", "Company,,Email\nA,B,C\n", "Company,Email\nA,B,C\n", "Company,Email\nA\n"])
def test_ambiguous_csv_rejected(tmp_path, content):
    path = tmp_path / "input.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_rows(path)


def test_formula_workbook_requires_values(tmp_path):
    wb = Workbook()
    wb.active.append(["Company", "Email"])
    wb.active.append(["A", '=HYPERLINK("https://example.org")'])
    path = tmp_path / "input.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="formula"):
        read_rows(path)


def test_conflicting_column_aliases_rejected():
    with pytest.raises(ValueError, match="Multiple columns"):
        map_columns(["Company", "Email", "E-mail"], load_schema("leads"))


def test_missing_required_columns_cannot_pass_audit(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("Company\nExample\n")
    result = audit_file(path, "accounts")
    assert result.records[0].status == RecordStatus.NEEDS_REVIEW
    assert any("account id" in flag for flag in result.records[0].flags)


@pytest.mark.parametrize("slug", ["..", "../escape", "x/y", "x\\y", "bad\nname", ".", "CON"])
def test_output_path_slugs_reject_traversal(slug):
    with pytest.raises(ValueError):
        ProjectInfo(name=slug)
    with pytest.raises(ValueError):
        OutputConfig(basename=slug)


def test_timestamped_outputs_do_not_collide(leads_project, http):
    leads_project.output.timestamp = True
    a, b = Pipeline(leads_project, http=http), Pipeline(leads_project, http=http)
    assert a.output_paths() == a.output_paths()
    assert a.output_paths() != b.output_paths()


def test_transitive_duplicate_bridge_preserves_locations():
    a = record("A", company_name="Example Co", phone="+3256205588", postcode="8500")
    b = record("B", company_name="Example Co", phone="+3256205588")
    c = record("C", company_name="Example Co", phone="+3256205588", postcode="3000")
    groups = dedupe([a, b, c], load_schema("leads"))
    assert not any(g.merged and "A" in g.member_ids and "C" in g.member_ids for g in groups)


def test_large_identifier_block_is_not_silently_skipped():
    records = [record(str(i), company_name=f"n{i:04d}x", email="info@example.org") for i in range(401)]
    matches = find_matches(records, load_schema("leads"))
    assert any(m.a == "0" and m.b == "400" for m in matches)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "1e999"])
def test_nonfinite_and_boolean_numbers_are_invalid(value):
    from dataharvest.processing.normalize import normalize_integer, normalize_number

    assert normalize_number(value) is None
    assert normalize_integer(value) is None


def test_scientific_numbers_and_fractional_integer_are_not_corrupted():
    from dataharvest.processing.normalize import normalize_integer, normalize_number

    assert normalize_number("1e3") == 1000
    assert normalize_integer("1.7") is None


def test_sparse_lists_do_not_create_one_quadratic_block():
    rows = [record(str(i), company_name=f"{i:06d} Demo", email=f"account{i}@example.org") for i in range(1000)]
    assert find_matches(rows, load_schema("leads")) == []
