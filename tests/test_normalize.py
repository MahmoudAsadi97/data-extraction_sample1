"""Unit tests for value normalisation and format validation."""

import pytest

from dataharvest.processing import normalize as n


class TestText:
    def test_clean_text_collapses_whitespace(self):
        assert n.clean_text("  Brasserie \n De   Leie  ") == "Brasserie De Leie"

    def test_clean_text_empty(self):
        assert n.clean_text("   ") is None
        assert n.clean_text(None) is None

    def test_name_key_strips_legal_forms_and_accents(self):
        assert n.name_key("Café Leffe BVBA") == "cafe leffe"
        assert n.name_key("Brasserie De Leie NV") == "brasserie de leie"
        assert n.name_key("B’aristokat") == "b aristokat"

    def test_normalize_name_keeps_case(self):
        assert n.normalize_name("  d'Oude Burcht ") == "d'Oude Burcht"


class TestEmail:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Info@Example.BE", "Info@example.be"),
            ("mailto:info@hollywok.be?subject=hi", "info@hollywok.be"),
            ("info [at] brasseriedeleie [dot] be", "info@brasseriedeleie.be"),
            ("<sales@shop.com>", "sales@shop.com"),
            ("no-at-sign", None),
        ],
    )
    def test_normalize_email(self, raw, expected):
        assert n.normalize_email(raw) == expected

    def test_is_valid_email(self):
        assert n.is_valid_email("info@hollywok.be")
        assert not n.is_valid_email("nobody@")
        assert not n.is_valid_email("a..b@x.be")
        assert n.email_domain("Info@Hollywok.BE") == "hollywok.be"


class TestUrl:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("hollywok.be", "https://hollywok.be/"),
            ("HTTP://WWW.Bocca.be/locations/kortrijk.html/", "http://www.bocca.be/locations/kortrijk.html"),
            ("https://izycoffee.be?utm_source=x&page=2#top", "https://izycoffee.be/?page=2"),
            ("mailto:info@x.be", None),
            ("not a url", None),
            ("ftp://files.example.org", None),
        ],
    )
    def test_normalize_url(self, raw, expected):
        assert n.normalize_url(raw) == expected

    def test_url_domain(self):
        assert n.url_domain("https://www.goedtenhoute.be/") == "goedtenhoute.be"
        assert n.url_domain("hollywok.be/menu") == "hollywok.be"


class TestPhone:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("+32 56 20 55 88", "+3256205588"),
            ("056/20.55.88", "+3256205588"),
            ("056 20 55 88", "+3256205588"),
            ("0478 12 34 56", "+32478123456"),
            ("tel:+3256123456", "+3256123456"),
            ("+32 56 20 55 88; +32 56 20 55 89", "+3256205588"),
            ("not-a-phone", None),
        ],
    )
    def test_normalize_phone_be(self, raw, expected):
        assert n.normalize_phone(raw, "BE") == expected

    def test_phone_info(self):
        info = n.phone_info("+32478123456", "BE")
        assert info["valid"] and info["type"] == "mobile" and info["region"] == "BE"
        bad = n.phone_info("+32 1 23", "BE")
        assert bad["valid"] is False
        assert n.phone_info(None, "BE")["valid"] is False


class TestPostcodeAndVat:
    def test_postcode(self):
        assert n.normalize_postcode("B-8500") == "8500"
        assert n.is_valid_postcode("8500", "BE") is True
        assert n.is_valid_postcode("ABCD", "BE") is False
        assert n.is_valid_postcode("1234", "XX") is None

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("BE0629.985.405", "BE0629985405"),
            ("BE 0629 985 405", "BE0629985405"),
            ("0629985405", "BE0629985405"),
            ("BTW BE0629.985.405", "BE0629985405"),
            ("629985405", "BE0629985405"),  # legacy 9-digit form
        ],
    )
    def test_normalize_vat(self, raw, expected):
        assert n.normalize_vat(raw, "BE") == expected

    def test_vat_checksum(self):
        assert n.is_valid_vat("BE0629985405") is True
        assert n.is_valid_vat("BE0629985404") is False  # wrong check digits
        assert n.is_valid_vat("BE0473191041") is True  # Barco
        assert n.is_valid_vat("XX123") is None


class TestSocial:
    def test_handles_and_urls(self):
        assert n.normalize_social("bastakortrijk", "facebook") == "https://www.facebook.com/bastakortrijk"
        assert n.normalize_social("https://www.instagram.com/vesperkortrijk/", "instagram") == "https://www.instagram.com/vesperkortrijk"
        assert n.normalize_social("https://www.linkedin.com/company/barco/", "linkedin") == "https://www.linkedin.com/company/barco"
        assert n.normalize_social("https://www.facebook.com/sharer/sharer.php?u=x", "facebook") is None
        assert n.normalize_social("https://www.linkedin.com/", "linkedin") is None

    def test_social_network_detection(self):
        assert n.social_network("https://x.com/barco") == "twitter"
        assert n.social_network("https://www.barco.com") is None


class TestNumbers:
    def test_numbers(self):
        assert n.normalize_number("£51.77") == 51.77
        assert n.normalize_number("1.234,50") == 1234.50
        assert n.normalize_number("1,234.50") == 1234.50
        assert n.normalize_number("1,234") == 1234.0
        assert n.normalize_number("1,5") == 1.5
        assert n.normalize_number("3 600") == 3600.0
        assert n.normalize_integer("22 available") == 22
        assert n.normalize_year("1934-01-01T00:00:00Z") == 1934
        assert n.normalize_date("2013-04-22T00:00:00Z") == "2013-04-22"
        assert n.normalize_date("22/04/2013") == "2013-04-22"
        assert n.normalize_coordinate("50.8276", "latitude") == 50.8276
        assert n.normalize_coordinate("500", "latitude") is None

    def test_dispatcher(self):
        assert n.normalize_value(" info@X.be ", "email") == "info@x.be"
        assert n.normalize_value("nagomisushi", "social", field_name="facebook") == "https://www.facebook.com/nagomisushi"
        assert n.normalize_value("Three", "integer") is None
