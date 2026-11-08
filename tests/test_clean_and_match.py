import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from etl.transform import clean as C  # noqa: E402
from etl.transform.match import build_match_frame, resolve_customers  # noqa: E402


def test_normalize_name_strips_suffixes_and_case():
    assert C.normalize_name("  MINH  THINH FASHION Co., Ltd ") == "Minh Thinh Fashion"
    assert C.normalize_name("an tien retail company limited") == "An Tien Retail"
    assert C.normalize_name("Sao Mai Trading JSC") == "Sao Mai Trading"
    assert C.normalize_name("") is None and C.normalize_name(None) is None
    assert C.name_match_key("Hà Nội Fashion") == "hanoifashion"


def test_normalize_phone_formats():
    for raw in ["0973215801", "+84 973 215 801", "(0973) 215-801", "84973215801", "0973.215.801", "973215801"]:
        assert C.normalize_phone(raw) == "0973215801", raw
    assert C.normalize_phone("12345") is None
    assert C.normalize_phone("") is None


def test_normalize_email():
    assert C.normalize_email(" Contact@Example.VN ") == "contact@example.vn"
    for bad in ["n/a", "contact@", "noemail", "", None]:
        assert C.normalize_email(bad) is None


def test_parse_date_known_formats_only():
    for raw in ["2025-01-05", "05/01/2025", "Jan 5, 2025", "2025-01-05 00:00:00", "05-01-2025"]:
        assert C.parse_date(raw) == pd.Timestamp("2025-01-05"), raw
    assert C.parse_date("01/05/25") is None          # ambiguous two-digit year: refuse, don't guess
    assert C.parse_date("") is None


def test_parse_amount_variants():
    assert C.parse_amount("12,500,000") == 12_500_000
    assert C.parse_amount("12.500.000") == 12_500_000
    assert C.parse_amount("12.5M") == 12_500_000
    assert C.parse_amount("12500000 VND") == 12_500_000
    assert C.parse_amount("45000000") == 45_000_000
    assert C.parse_amount("TBD") is None and C.parse_amount("") is None


def test_city_and_stage_maps():
    assert C.normalize_city("TP.HCM") == "Ho Chi Minh City"
    assert C.normalize_city("Hà Nội") == "Ha Noi"
    assert C.normalize_city("Vinh") == "Vinh"
    assert C.normalize_stage("Closed-Won") == "Closed Won"
    assert C.normalize_stage("Propsal") == "Proposal"
    assert C.normalize_stage("Maybe") is None


def _frames():
    crm = pd.DataFrame({
        "crm_id": ["C1", "C2", "C3", "C4"],
        "name_clean": ["Alpha Trading", "Alpha Trading", "Beta Foods", "Gamma Ltd"],
        "name_key": ["alphatrading", "alphatrading", "betafoods", "gammaltd"],
        "city_clean": ["Ha Noi", "Ha Noi", "Da Nang", "Hue"],
        "email_clean": ["a@alpha.vn", None, "b@beta.vn", None],
        "phone_clean": [None, "0901111111", None, "0905555555"],
        "industry_clean": ["Retail", "Retail", "F&B", None],
        "created_at_clean": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]),
    })
    erp = pd.DataFrame({
        "account_code": ["E1", "E2", "E3"],
        "name_clean": ["Alpha Trading", "Beta Foods", "Delta Co"],
        "name_key": ["alphatrading", "betafoods", "deltaco"],
        "city_clean": ["Ha Noi", "Da Nang", "Can Tho"],
        "email_clean": [None, None, None],
        "phone_clean": ["0901111111", None, None],
        "tax_code_clean": ["0100000001", "0100000002", "0100000003"],
    })
    return crm, erp


def test_resolution_clusters_transitively_and_keeps_singletons():
    crm, erp = _frames()
    dim, bridge = resolve_customers(build_match_frame(crm, erp))
    key = bridge.set_index(bridge["system"] + ":" + bridge["source_id"])["customer_key"]
    # C1 (email) ~ C2 (name+city) ~ E1 (phone with C2): one customer
    assert key["crm:C1"] == key["crm:C2"] == key["erp:E1"]
    assert key["crm:C3"] == key["erp:E2"]            # Beta via name+city
    assert key["crm:C4"] != key["erp:E3"]            # unrelated singletons
    assert len(dim) == 4
    alpha = dim[dim["customer_key"] == key["crm:C1"]].iloc[0]
    assert alpha["in_crm"] and alpha["in_erp"] and alpha["n_source_records"] == 3
    assert alpha["tax_code"] == "0100000001" and alpha["industry"] == "Retail"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
