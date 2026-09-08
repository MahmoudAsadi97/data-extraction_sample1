"""Validation, verification rules and duplicate detection."""

from __future__ import annotations

from dataharvest.models import FieldStatus, Record, RecordStatus
from dataharvest.processing.dedupe import compare, dedupe, find_matches
from dataharvest.processing.validate import normalize_record, validate_record
from dataharvest.processing.verify import (
    MailDomainChecker,
    assign_record_status,
    compute_completeness,
    verify_email_field,
    verify_phone_field,
)
from dataharvest.schema import load_schema

LEADS = load_schema("leads")


def make(record_id: str, source: str = "osm", **values) -> Record:
    rec = Record(record_id=record_id, source=source)
    for k, v in values.items():
        rec.set(k, v, source=source)
    return rec


class TestValidation:
    def test_normalises_and_flags_unusable_values(self):
        rec = make("R1", company_name=" Hollywok ", phone="056/20.55.88", email="Info@Hollywok.BE", website="hollywok.be", postcode="B-8500", latitude="999")
        normalize_record(rec, LEADS, "BE")
        assert rec.get("phone") == "+3256205588" and rec.get("email") == "Info@hollywok.be" and rec.get("website") == "https://hollywok.be/"
        assert rec.get("postcode") == "8500"
        assert rec.get("latitude") is None and rec.field_status("latitude") == FieldStatus.INVALID
        assert any("latitude" in f for f in rec.flags)

    def test_required_and_format_rules(self):
        rec = make("R2", company_name="X", email="nobody@", postcode="ABCD", phone="+32 1 23", website="https://x.be/", vat_number="BE0629985404")
        problems = validate_record(rec, LEADS, "BE")
        joined = " | ".join(problems)
        assert "missing required field: category" in joined
        assert "e-mail: invalid" in joined and "postcode: invalid" in joined and "phone: invalid" in joined
        assert "vat number: invalid" in joined
        assert rec.field_status("email") == FieldStatus.INVALID

    def test_cross_field_email_vs_website_domain(self):
        rec = make("R3", company_name="Thai Lin", category="restaurant", email="info@thailin.be", website="http://www.y-notthai.com/")
        validate_record(rec, LEADS, "BE")
        assert any("differs from website domain" in f for f in rec.flags)
        gmail = make("R4", company_name="Gursha", category="restaurant", email="gursha.kortrijk@gmail.com", website="https://gurshakortrijk.com/")
        validate_record(gmail, LEADS, "BE")
        assert not any("differs from website domain" in f for f in gmail.flags)  # free-mail providers are fine

    def test_closed_business_flag(self):
        rec = make("R5", company_name="Kostba(a)r", category="restaurant", opening_hours='closed "Tot Eind Juli 2026 wegen verbouwingen"')
        validate_record(rec, LEADS, "BE")
        assert any("closed" in f for f in rec.flags)


class TestVerification:
    def test_phone_and_email_field_checks(self):
        rec = make("R6", phone="+32478123456", email="info@hollywok.be")
        verify_phone_field(rec, "BE")
        assert rec.checks["phone"]["type"] == "mobile" and rec.field_status("phone") == FieldStatus.UNVERIFIED
        checker = MailDomainChecker(http=None)
        checker.cache["hollywok.be"] = type("Mx", (), {"domain": "hollywok.be", "deliverable": True, "detail": "MX: mx.hollywok.be"})()
        verify_email_field(rec, checker)
        assert "domain accepts mail" in rec.fields["email"].note
        bad = make("R7", email="hello@nope.invalid")
        checker.cache["nope.invalid"] = type("Mx", (), {"domain": "nope.invalid", "deliverable": False, "detail": "NXDOMAIN"})()
        verify_email_field(bad, checker)
        assert bad.field_status("email") == FieldStatus.INVALID and any("cannot receive mail" in f for f in bad.flags)

    def test_record_status_rules(self):
        verified = make("R8", company_name="Hollywok", category="restaurant", website="https://hollywok.be/", email="info@hollywok.be")
        verified.mark("website", FieldStatus.VERIFIED)
        verified.mark("email", FieldStatus.VERIFIED)
        assert assign_record_status(verified, LEADS) == RecordStatus.VERIFIED
        partial = make("R9", company_name="Hollywok", category="restaurant", website="https://hollywok.be/")
        partial.mark("website", FieldStatus.VERIFIED)
        assert assign_record_status(partial, LEADS) == RecordStatus.PARTIALLY_VERIFIED
        plain = make("R10", company_name="Hollywok", category="restaurant")
        assert assign_record_status(plain, LEADS) == RecordStatus.UNVERIFIED
        conflict = make("R11", company_name="Hollywok", category="restaurant", email="a@b.be")
        conflict.mark("email", FieldStatus.CONFLICT)
        assert assign_record_status(conflict, LEADS) == RecordStatus.NEEDS_REVIEW
        nameless = make("R12", category="restaurant")
        assert assign_record_status(nameless, LEADS) == RecordStatus.EXCLUDED
        missing_required = make("R13", company_name="X")
        validate_record(missing_required, LEADS, "BE")
        assert assign_record_status(missing_required, LEADS) == RecordStatus.NEEDS_REVIEW

    def test_completeness_weights_required_fields(self):
        rec = make("R14", company_name="X", category="restaurant")
        pct = compute_completeness(rec, LEADS)
        assert 0 < pct < 30
        full = make("R15", **{f.name: "x" for f in LEADS.fields})
        assert compute_completeness(full, LEADS) == 100.0


class TestDedupe:
    def test_compare_rules(self):
        a = make("A", company_name="Kostbar", email="els@kostbar-marke.be", website="https://kostbar-marke.be/", postcode="8510")
        b = make("B", company_name="Kostba(a)r", email="els@kostbar-marke.be", website="https://kostbar-marke.be/")
        m = compare(a, b, "company_name", 90)
        assert m is not None and m.confident and "same email" in m.reason
        c = make("C", company_name="Bistro Tout Court", website="https://www.bistrotoutcourt.be/", postcode="8501", street="Sint-Katharinastraat")
        d = make("D", company_name="Bistro Tout Court", website="https://www.bistrotoutcourt.be/", postcode="8500", street="Budastraat")
        m2 = compare(c, d, "company_name", 90)
        assert m2 is not None and m2.confident  # same domain + identical names -> duplicate
        e = make("E", company_name="Pizza Hut Kortrijk", website="https://restaurants.pizzahut.be/a", postcode="8500")
        f = make("F", company_name="Pizza Hut Kinepolis", website="https://restaurants.pizzahut.be/b", postcode="8500")
        m3 = compare(e, f, "company_name", 90)
        assert m3 is not None and m3.confident is True  # same domain and same postcode
        g = make("G", company_name="Bocca", website="https://www.bocca.be/x", postcode="8500")
        h = make("H", company_name="Kaffee Bar Zuid", website="https://www.bocca.be/y", postcode="9000")
        m4 = compare(g, h, "company_name", 90)
        assert m4 is not None and not m4.confident and "branch or chain" in m4.reason
        assert compare(make("I", company_name="Alpha"), make("J", company_name="Omega"), "company_name", 90) is None

    def test_dedupe_merges_and_keeps_best(self):
        a = make("A", company_name="Hollywok", category="restaurant", phone="+3256205588", website="https://hollywok.be/", postcode="8500")
        a.mark("website", FieldStatus.VERIFIED)
        b = make("B", source="csv", company_name="Hollywok Kortrijk", phone="+3256205588", email="info@hollywok.be", postcode="8500")
        c = make("C", company_name="Unrelated Place", category="cafe", postcode="8500")
        records = [a, b, c]
        for r in records:
            compute_completeness(r, LEADS)
        groups = dedupe(records, LEADS, threshold=90, merge=True)
        assert len(groups) == 1 and groups[0].merged and groups[0].master_id == "A"
        assert b.duplicate_of == "A" and a.get("email") == "info@hollywok.be"  # gap filled from the duplicate
        assert a.fields["email"].source == "csv"
        assert "Hollywok Kortrijk" in a.fields["company_name"].candidates
        assert any("merged 1 duplicate" in f for f in a.flags)

    def test_dedupe_flag_only(self):
        a = make("A", company_name="Kostbar", email="els@kostbar-marke.be")
        b = make("B", company_name="Kostba(a)r", email="els@kostbar-marke.be")
        groups = dedupe([a, b], LEADS, merge=False)
        assert groups and not groups[0].merged and not a.duplicate_of and not b.duplicate_of
        assert any("duplicate group" in f for f in a.flags)

    def test_find_matches_blocking_scales(self):
        records = [make(f"R{i}", company_name=f"Company {i}", postcode=str(8000 + i % 7)) for i in range(300)]
        assert find_matches(records, LEADS) == []
