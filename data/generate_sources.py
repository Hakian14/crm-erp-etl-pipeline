"""
Generate realistic, *messy* CRM and ERP extracts for the pipeline to clean.

    python data/generate_sources.py      -> data/raw/crm/*.csv, data/raw/erp/*.csv

The two systems describe the same customers with different keys, naming and
formats - exactly the situation the pipeline exists to solve. Planted problems:

CRM (sales system)
  * customer names with inconsistent case, suffixes ("Co., Ltd" / "Company Limited"), extra spaces
  * phone numbers in 4 formats, emails with case / whitespace noise, some invalid
  * duplicates (same company entered twice by different reps)
  * dates as dd/mm/yyyy, yyyy-mm-dd and "Jan 5, 2025"
  * deals with amounts as "12,500,000", "12.5M", "12500000 VND"; stages misspelled
ERP (finance system)
  * account codes instead of names as keys; tax codes present for ~85%
  * orders referencing accounts that do not exist (orphans)
  * invoices with negative/zero amounts, future dates, duplicated invoice numbers
  * currency column with 'VND', 'vnd', '₫'

Deterministic (fixed seed). No real company data.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

SEED = 21
RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
rng = np.random.default_rng(SEED)

WORDS = ["Minh", "An", "Phat", "Thinh", "Hung", "Viet", "Hoa", "Binh", "Thanh", "Long", "Tien", "Phuc", "Nam", "Hai", "Dong",
         "Sao", "Mai", "Tan", "Loc", "Khang", "Duc", "Quang", "Son", "Vinh", "Lan", "Hoang", "Gia", "Kim", "Bao", "Anh"]
TYPES = ["Fashion", "Retail", "Trading", "Food", "Cosmetics", "Electronics", "Logistics", "Furniture", "Pharma", "Textile"]
SUFFIX_VARIANTS = [" Co., Ltd", " Company Limited", " Co Ltd", " JSC", " Joint Stock Company", "", " LLC"]
CITIES = ["Ha Noi", "Ho Chi Minh City", "Hai Phong", "Da Nang", "Can Tho", "Bac Ninh", "Hai Duong", "Hue", "Nha Trang", "Bien Hoa"]
CITY_VARIANTS = {"Ha Noi": ["Ha Noi", "Hanoi", "HANOI", "Hà Nội"], "Ho Chi Minh City": ["Ho Chi Minh City", "HCMC", "Ho Chi Minh", "Sai Gon", "TP.HCM"],
                 "Hai Phong": ["Hai Phong", "Haiphong", "HP"], "Da Nang": ["Da Nang", "Danang"]}
INDUSTRIES = ["Retail", "Wholesale", "Manufacturing", "Services", "F&B", "E-commerce"]
STAGES = ["Lead", "Qualified", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
STAGE_TYPOS = {"Closed Won": ["Closed Won", "closed won", "Closed-Won", "Won"], "Closed Lost": ["Closed Lost", "Lost", "closed lost"],
               "Proposal": ["Proposal", "proposal", "Propsal"], "Negotiation": ["Negotiation", "Negotation"]}


def company_name(i: int) -> str:
    """Unique for i < 9000: 30 x 30 word pairs x 10 types."""
    return f"{WORDS[i % len(WORDS)]} {WORDS[(i // len(WORDS)) % len(WORDS)]} {TYPES[(i // 900) % len(TYPES)]}"


def vary_name(base: str) -> str:
    name = base + rng.choice(SUFFIX_VARIANTS)
    r = rng.random()
    if r < 0.15:
        name = name.upper()
    elif r < 0.25:
        name = name.lower()
    if rng.random() < 0.12:
        name = "  " + name + " "
    if rng.random() < 0.08:
        name = name.replace(" ", "  ", 1)
    return name


def vary_phone(digits: str) -> str:
    fmt = rng.choice(["0{a}{b}{c}", "+84 {a} {b} {c}", "(0{a}) {b}-{c}", "84{a}{b}{c}", "0{a}.{b}.{c}"])
    return fmt.format(a=digits[:3], b=digits[3:6], c=digits[6:])


def vary_date(d: date) -> str:
    fmt = rng.choice(["%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"], p=[0.45, 0.3, 0.1, 0.1, 0.05])
    return d.strftime(fmt)


def vary_amount(v: int) -> str:
    r = rng.random()
    if r < 0.5:
        return str(v)
    if r < 0.75:
        return f"{v:,}"
    if r < 0.85:
        return f"{v} VND"
    if r < 0.93:
        return f"{v / 1e6:.1f}M"
    return f"{v:,}".replace(",", ".")


def main():
    os.makedirs(os.path.join(RAW, "crm"), exist_ok=True)
    os.makedirs(os.path.join(RAW, "erp"), exist_ok=True)

    # ---- the "true" customer universe ------------------------------------------------
    n_true = 2000
    truth = pd.DataFrame({
        "true_id": np.arange(n_true),
        "name": [company_name(i) for i in range(n_true)],
        "city": rng.choice(CITIES, size=n_true, p=[0.26, 0.30, 0.08, 0.08, 0.05, 0.05, 0.05, 0.04, 0.05, 0.04]),
        "phone": [f"{rng.choice(['90','91','93','96','97','98','32','33','35','70','76','77','79','81','83','85','86','88','89'])}{rng.integers(1000000, 9999999)}" for _ in range(n_true)],
        "industry": rng.choice(INDUSTRIES, size=n_true),
        "tax_code": [f"0{rng.integers(100000000, 999999999)}" for _ in range(n_true)],
    })
    truth["email_domain"] = truth["name"].str.lower().str.replace(r"[^a-z]", "", regex=True) + ".vn"
    assert truth["name"].is_unique and truth["email_domain"].is_unique
    truth["email"] = "contact@" + truth["email_domain"]

    # ---- CRM customers: 85% of truth + 6% duplicates ---------------------------------
    in_crm = truth[rng.random(n_true) < 0.85].copy()
    dupes = in_crm.sample(frac=0.07, random_state=SEED).copy()
    crm = pd.concat([in_crm, dupes], ignore_index=True)
    created = [date(2022, 1, 1) + timedelta(days=int(x)) for x in rng.integers(0, 1400, size=len(crm))]
    # ~4% of CRM rows are "hard cases": no email, no phone, and a typo in the name,
    # so no strong key survives - these end up in the manual review queue.
    hard = rng.random(len(crm)) < 0.04

    def typo(name: str) -> str:
        i = int(rng.integers(1, len(name) - 2))
        return name[:i] + name[i + 1] + name[i] + name[i + 2:]

    crm_out = pd.DataFrame({
        "crm_id": [f"CRM-{100000 + i}" for i in range(len(crm))],
        "account_name": [vary_name(typo(n) if h else n) for n, h in zip(crm["name"], hard)],
        "email": ["" if h else ((e if rng.random() < 0.9 else e.upper()) if rng.random() < 0.93 else rng.choice(["", "n/a", "contact@", "noemail"])) for e, h in zip(crm["email"], hard)],
        "phone": ["" if h else (vary_phone(p) if rng.random() < 0.95 else "") for p, h in zip(crm["phone"], hard)],
        "city": [rng.choice(CITY_VARIANTS.get(c, [c])) for c in crm["city"]],
        "industry": [i if rng.random() < 0.9 else i.lower() for i in crm["industry"]],
        "owner": rng.choice(["Linh Tran", "Minh Le", "Hoa Pham", "Duc Nguyen", "Thu Vo"], size=len(crm)),
        "created_at": [vary_date(d) for d in created],
        "status": rng.choice(["Active", "active", "Inactive", "Prospect", ""], size=len(crm), p=[0.6, 0.1, 0.15, 0.13, 0.02]),
        "_true_id": crm["true_id"].to_numpy(),
    })

    # deals
    n_deals = 4000
    deal_cust = crm_out.sample(n=n_deals, replace=True, random_state=SEED)
    stages = rng.choice(STAGES, size=n_deals, p=[0.15, 0.15, 0.15, 0.1, 0.3, 0.15])
    closed = [date(2024, 1, 1) + timedelta(days=int(x)) for x in rng.integers(0, 700, size=n_deals)]
    deals = pd.DataFrame({
        "deal_id": [f"D{300000 + i}" for i in range(n_deals)],
        "crm_id": deal_cust["crm_id"].to_numpy(),
        "deal_name": [f"{t} - {rng.choice(['Q1', 'Q2', 'Q3', 'Q4'])} order" for t in rng.choice(TYPES, size=n_deals)],
        "stage": [rng.choice(STAGE_TYPOS.get(s, [s])) for s in stages],
        "amount": [vary_amount(int(rng.lognormal(np.log(45_000_000), 0.9) // 100_000 * 100_000)) if rng.random() < 0.97 else rng.choice(["", "TBD", "0"]) for _ in range(n_deals)],
        "close_date": [vary_date(d) if rng.random() < 0.96 else "" for d in closed],
        "owner": deal_cust["owner"].to_numpy(),
    })

    # ---- ERP accounts: 80% of truth, keyed by account code ---------------------------
    in_erp = truth[rng.random(n_true) < 0.80].copy()
    erp_accounts = pd.DataFrame({
        "account_code": [f"AC{20000 + i}" for i in range(len(in_erp))],
        "account_name": [vary_name(n) if rng.random() < 0.5 else n + " Co., Ltd" for n in in_erp["name"]],
        "tax_code": [t if rng.random() < 0.85 else "" for t in in_erp["tax_code"]],
        "phone": [vary_phone(p) if rng.random() < 0.8 else "" for p in in_erp["phone"]],
        "email": [e if rng.random() < 0.7 else "" for e in in_erp["email"]],
        "city": in_erp["city"].to_numpy(),
        "payment_terms": rng.choice(["NET15", "NET30", "NET45", "COD", ""], size=len(in_erp), p=[0.2, 0.5, 0.15, 0.1, 0.05]),
        "credit_limit": rng.choice([50_000_000, 100_000_000, 200_000_000, 500_000_000, 0], size=len(in_erp)),
        "_true_id": in_erp["true_id"].to_numpy(),
    })

    # sales orders (some orphans)
    n_orders = 6000
    acct = erp_accounts["account_code"].to_numpy()
    order_acct = rng.choice(acct, size=n_orders).astype(object)
    orphan = rng.random(n_orders) < 0.03                         # orders pointing at accounts the ERP extract lacks
    order_acct[orphan] = [f"AC{rng.integers(90000, 99999)}" for _ in range(int(orphan.sum()))]
    odates = [date(2024, 1, 1) + timedelta(days=int(x)) for x in rng.integers(0, 700, size=n_orders)]
    orders = pd.DataFrame({
        "order_no": [f"SO{500000 + i}" for i in range(n_orders)],
        "account_code": order_acct,
        "order_date": [d.isoformat() for d in odates],
        "status": rng.choice(["DELIVERED", "Delivered", "SHIPPED", "CANCELLED", "OPEN"], size=n_orders, p=[0.6, 0.1, 0.12, 0.08, 0.1]),
        "currency": rng.choice(["VND", "vnd", "₫", "VND "], size=n_orders, p=[0.8, 0.1, 0.05, 0.05]),
        "net_amount": (rng.lognormal(np.log(38_000_000), 0.8, size=n_orders) // 1000 * 1000).astype(int),
        "sales_rep": rng.choice(["Linh Tran", "Minh Le", "Hoa Pham", "Duc Nguyen", "Thu Vo", "L. Tran"], size=n_orders),
    })

    # invoices: 1 per non-cancelled order, plus duplicates and bad rows
    inv_base = orders[orders["status"] != "CANCELLED"].copy()
    invoices = pd.DataFrame({
        "invoice_no": [f"INV{700000 + i}" for i in range(len(inv_base))],
        "order_no": inv_base["order_no"].to_numpy(),
        "account_code": inv_base["account_code"].to_numpy(),
        "invoice_date": [(date.fromisoformat(d) + timedelta(days=int(rng.integers(0, 15)))).isoformat() for d in inv_base["order_date"]],
        "due_date": [(date.fromisoformat(d) + timedelta(days=int(rng.choice([15, 30, 45])))).isoformat() for d in inv_base["order_date"]],
        "amount": inv_base["net_amount"].to_numpy() * 1.08 // 1000 * 1000,
        "vat_rate": rng.choice([0.08, 0.1, 0.08], size=len(inv_base)),
        "paid_date": [None] * len(inv_base),
    })
    # payment behaviour: almost everything older than 60 days is paid; recent invoices are still open
    snapshot = date(2025, 12, 15)
    age_days = invoices["due_date"].map(lambda d: (snapshot - date.fromisoformat(d)).days)
    p_paid = np.where(age_days > 60, 0.96, np.where(age_days > 0, 0.55, 0.15))
    paid = rng.random(len(invoices)) < p_paid
    invoices.loc[paid, "paid_date"] = [(date.fromisoformat(d) + timedelta(days=int(rng.integers(-5, 40)))).isoformat() for d in invoices.loc[paid, "due_date"]]
    # planted problems
    dup = invoices.sample(n=40, random_state=SEED)
    invoices = pd.concat([invoices, dup], ignore_index=True)
    bad_idx = rng.choice(len(invoices), size=35, replace=False)
    invoices.loc[bad_idx[:15], "amount"] = -invoices.loc[bad_idx[:15], "amount"]
    invoices.loc[bad_idx[15:25], "amount"] = 0
    invoices.loc[bad_idx[25:], "invoice_date"] = "2027-03-01"

    # write
    crm_out.drop(columns="_true_id").to_csv(os.path.join(RAW, "crm", "accounts.csv"), index=False)
    deals.to_csv(os.path.join(RAW, "crm", "deals.csv"), index=False)
    erp_accounts.drop(columns="_true_id").to_csv(os.path.join(RAW, "erp", "accounts.csv"), index=False, sep=";")
    orders.to_csv(os.path.join(RAW, "erp", "sales_orders.csv"), index=False, sep=";")
    invoices.to_csv(os.path.join(RAW, "erp", "invoices.csv"), index=False, sep=";")
    # evaluation key (NOT used by the pipeline) so matching quality can be measured in tests/docs
    pd.concat([crm_out[["crm_id", "_true_id"]].rename(columns={"crm_id": "source_id"}).assign(system="crm"),
               erp_accounts[["account_code", "_true_id"]].rename(columns={"account_code": "source_id"}).assign(system="erp")]) \
        .to_csv(os.path.join(RAW, "_evaluation_key.csv"), index=False)
    for name, df in [("crm/accounts", crm_out), ("crm/deals", deals), ("erp/accounts", erp_accounts),
                     ("erp/sales_orders", orders), ("erp/invoices", invoices)]:
        print(f"{name:18s} {len(df):>6,} rows")


if __name__ == "__main__":
    main()
