"""
Standardisation functions for the staging layer.

Every function is pure (value in, value out) so it can be unit-tested and
reused in Pentaho's "User Defined Java Expression" / Python steps, or in SQL
once the rules are stable.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------- names

_SUFFIXES = [
    r"\bcompany\s+limited\b", r"\bco\.?,?\s*ltd\.?\b", r"\bltd\.?\b", r"\bllc\b",
    r"\bjoint\s+stock\s+company\b", r"\bjsc\b", r"\bcorp(oration)?\b", r"\binc\.?\b",
]
_SUFFIX_RE = re.compile("|".join(_SUFFIXES), flags=re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def normalize_name(raw) -> str | None:
    """Trim, collapse whitespace, title-case, canonical legal suffix removed.
    'MINH  THINH FASHION Co., Ltd ' -> 'Minh Thinh Fashion'"""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = _WS_RE.sub(" ", str(raw)).strip()
    if not s:
        return None
    s = _SUFFIX_RE.sub("", s)
    s = re.sub(r"[,\.\-]+$", "", s).strip()
    s = _WS_RE.sub(" ", s)
    return s.title() if s else None


def name_match_key(name: str | None) -> str | None:
    """Aggressive key for matching: accents and non-letters removed, lower-case."""
    if not name:
        return None
    return re.sub(r"[^a-z0-9]", "", strip_accents(name).lower()) or None


# ---------------------------------------------------------------------------- phones

def normalize_phone(raw) -> str | None:
    """Vietnamese mobile/landline to '0XXXXXXXXX'. Returns None when not a valid 10-digit number."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) == 9 and not digits.startswith("0"):
        digits = "0" + digits
    return digits if re.fullmatch(r"0\d{9}", digits) else None


# ---------------------------------------------------------------------------- emails

_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")


def normalize_email(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = str(raw).strip().lower()
    return s if _EMAIL_RE.match(s) else None


# ---------------------------------------------------------------------------- dates

_DATE_FORMATS = ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d"]


def parse_date(raw) -> pd.Timestamp | None:
    """Try the formats the source systems are known to emit. Unknown -> None (never guess month/day order)."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return pd.Timestamp(datetime.strptime(s, fmt).date())
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------- amounts

def parse_amount(raw) -> float | None:
    """'12,500,000' | '12.500.000' | '12.5M' | '12500000 VND' | '45000000' -> 12500000.0 (VND).
    Returns None for blanks and non-numeric placeholders ('TBD')."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = str(raw).strip().upper().replace("VND", "").replace("₫", "").strip()
    if not s:
        return None
    mult = 1.0
    if s.endswith("M"):
        mult, s = 1e6, s[:-1]
    elif s.endswith("K"):
        mult, s = 1e3, s[:-1]
    # thousands separators: "12,500,000" or "12.500.000" (groups of three)
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", s):
        s = re.sub(r"[,.]", "", s)
    s = s.replace(",", "")
    try:
        return float(s) * mult
    except ValueError:
        return None


# ---------------------------------------------------------------------------- categorical

_CITY_MAP = {
    "hanoi": "Ha Noi", "ha noi": "Ha Noi", "hà nội": "Ha Noi",
    "hcmc": "Ho Chi Minh City", "ho chi minh": "Ho Chi Minh City", "ho chi minh city": "Ho Chi Minh City",
    "sai gon": "Ho Chi Minh City", "saigon": "Ho Chi Minh City", "tp.hcm": "Ho Chi Minh City", "tphcm": "Ho Chi Minh City",
    "haiphong": "Hai Phong", "hai phong": "Hai Phong", "hp": "Hai Phong",
    "danang": "Da Nang", "da nang": "Da Nang",
}


def normalize_city(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = _WS_RE.sub(" ", str(raw)).strip()
    if not s:
        return None
    return _CITY_MAP.get(s.lower(), s.title())


_STAGE_MAP = {
    "lead": "Lead", "qualified": "Qualified", "proposal": "Proposal", "propsal": "Proposal",
    "negotiation": "Negotiation", "negotation": "Negotiation",
    "closed won": "Closed Won", "closed-won": "Closed Won", "won": "Closed Won",
    "closed lost": "Closed Lost", "closed-lost": "Closed Lost", "lost": "Closed Lost",
}


def normalize_stage(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    return _STAGE_MAP.get(str(raw).strip().lower())


def normalize_upper(raw, allowed: set[str] | None = None, default: str | None = None) -> str | None:
    """Trim + upper-case a code; optionally restrict to an allowed set."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return default
    s = str(raw).strip().upper()
    if not s:
        return default
    if allowed is not None and s not in allowed:
        return default
    return s


def normalize_currency(raw) -> str:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return "VND"
    s = str(raw).strip().upper().replace("₫", "VND")
    return s or "VND"


# ---------------------------------------------------------------------------- frame-level helpers

def apply_column(df: pd.DataFrame, col: str, fn, new_col: str | None = None) -> pd.DataFrame:
    df[new_col or col] = df[col].map(fn)
    return df
