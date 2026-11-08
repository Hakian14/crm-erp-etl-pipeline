"""
Customer identity resolution across CRM and ERP.

Records from both systems are linked when they share any *strong* key, and the
links are clustered with union-find so that A~B and B~C puts A, B, C in one
customer. Each cluster becomes one row in dim_customer; a bridge table keeps
every source record -> customer_key mapping for lineage.

Match keys, in order of trust:
    1. tax_code                (ERP only, but authoritative when present)
    2. email                   (normalised)
    3. phone                   (normalised)
    4. name_key + city         (normalised name without legal suffix, plus city)

Name+city alone is deliberately NOT enough without city because company-name
collisions are common ("An Phat Trading" exists in every province).
"""

from __future__ import annotations

import pandas as pd

MATCH_KEYS = ["tax_code", "email", "phone", "name_city"]


class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_match_frame(crm: pd.DataFrame, erp: pd.DataFrame) -> pd.DataFrame:
    """Stack both systems into one frame with a uniform set of key columns."""
    c = pd.DataFrame({
        "system": "crm", "source_id": crm["crm_id"], "name": crm["name_clean"], "name_key": crm["name_key"],
        "city": crm["city_clean"], "email": crm["email_clean"], "phone": crm["phone_clean"], "tax_code": None,
        "industry": crm["industry_clean"], "created_at": crm["created_at_clean"],
    })
    e = pd.DataFrame({
        "system": "erp", "source_id": erp["account_code"], "name": erp["name_clean"], "name_key": erp["name_key"],
        "city": erp["city_clean"], "email": erp["email_clean"], "phone": erp["phone_clean"], "tax_code": erp["tax_code_clean"],
        "industry": None, "created_at": None,
    })
    m = pd.concat([c, e], ignore_index=True)
    m["name_city"] = m.apply(lambda r: f"{r['name_key']}|{r['city']}" if r["name_key"] and r["city"] else None, axis=1)
    m["record_id"] = m["system"] + ":" + m["source_id"].astype(str)
    return m


def resolve_customers(match_frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (dim_customer, bridge). Every source record gets exactly one customer_key."""
    uf = UnionFind()
    link_reason: dict[str, set] = {}
    for key in MATCH_KEYS:
        groups = match_frame.dropna(subset=[key]).groupby(key)["record_id"].apply(list)
        for members in groups:
            if len(members) < 2:
                continue
            for other in members[1:]:
                uf.union(members[0], other)
                link_reason.setdefault(other, set()).add(key)
                link_reason.setdefault(members[0], set()).add(key)

    match_frame = match_frame.copy()
    match_frame["cluster"] = match_frame["record_id"].map(uf.find)
    cluster_ids = {c: i + 1 for i, c in enumerate(sorted(match_frame["cluster"].unique()))}
    match_frame["customer_key"] = match_frame["cluster"].map(cluster_ids)
    match_frame["matched_on"] = match_frame["record_id"].map(lambda r: ",".join(sorted(link_reason.get(r, set()))) or None)

    # survivorship: prefer ERP values for tax/payment fields, CRM for industry; first non-null otherwise
    def pick(group: pd.DataFrame, col: str, prefer: str | None = None):
        if prefer is not None:
            vals = group.loc[group["system"] == prefer, col].dropna()
            if len(vals):
                return vals.iloc[0]
        vals = group[col].dropna()
        return vals.iloc[0] if len(vals) else None

    rows = []
    for ck, g in match_frame.groupby("customer_key", sort=True):
        rows.append({
            "customer_key": ck,
            "customer_name": pick(g, "name", prefer="erp"),
            "city": pick(g, "city"),
            "email": pick(g, "email"),
            "phone": pick(g, "phone"),
            "tax_code": pick(g, "tax_code"),
            "industry": pick(g, "industry", prefer="crm"),
            "first_seen_crm": g.loc[g["system"] == "crm", "created_at"].min(),
            "in_crm": bool((g["system"] == "crm").any()),
            "in_erp": bool((g["system"] == "erp").any()),
            "n_source_records": len(g),
            "crm_ids": ",".join(g.loc[g["system"] == "crm", "source_id"].astype(str)) or None,
            "erp_account_codes": ",".join(g.loc[g["system"] == "erp", "source_id"].astype(str)) or None,
        })
    dim = pd.DataFrame(rows)
    bridge = match_frame[["customer_key", "system", "source_id", "matched_on"]].sort_values(["customer_key", "system"]).reset_index(drop=True)
    return dim, bridge


def suggest_review_queue(dim: pd.DataFrame, threshold: float = 0.90, max_candidates: int = 3) -> pd.DataFrame:
    """Fuzzy name similarity between CRM-only and ERP-only customers in the same city.
    These are *suggestions* for a human to confirm, never auto-merged: a 0.9 similarity
    between 'An Phat Trading' and 'An Phat Trading 2' is a different company."""
    from difflib import SequenceMatcher

    crm_only = dim[dim["in_crm"] & ~dim["in_erp"]]
    erp_only = dim[~dim["in_crm"] & dim["in_erp"]]
    rows = []
    for _, c in crm_only.iterrows():
        cands = erp_only[erp_only["city"] == c["city"]] if c["city"] else erp_only
        scored = []
        for _, e in cands.iterrows():
            if not (c["customer_name"] and e["customer_name"]):
                continue
            sim = SequenceMatcher(None, c["customer_name"].lower(), e["customer_name"].lower()).ratio()
            if sim >= threshold:
                scored.append((sim, e))
        for sim, e in sorted(scored, key=lambda x: -x[0])[:max_candidates]:
            rows.append({"crm_customer_key": c["customer_key"], "crm_name": c["customer_name"], "crm_ids": c["crm_ids"],
                         "erp_customer_key": e["customer_key"], "erp_name": e["customer_name"], "erp_account_codes": e["erp_account_codes"],
                         "city": c["city"], "name_similarity": round(sim, 3)})
    return pd.DataFrame(rows).sort_values("name_similarity", ascending=False).reset_index(drop=True) if rows else pd.DataFrame(
        columns=["crm_customer_key", "crm_name", "crm_ids", "erp_customer_key", "erp_name", "erp_account_codes", "city", "name_similarity"])


def evaluate_matching(bridge: pd.DataFrame, evaluation_key: pd.DataFrame) -> dict:
    """Pairwise precision/recall of the resolved clusters against a known truth key.
    Used in tests and docs only - the pipeline never sees the truth."""
    m = bridge.merge(evaluation_key, on=["system", "source_id"], how="inner")
    truth_pairs, pred_pairs = set(), set()
    for col, store in (("_true_id", truth_pairs), ("customer_key", pred_pairs)):
        for _, g in m.groupby(col):
            ids = sorted(g["system"] + ":" + g["source_id"].astype(str))
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    store.add((ids[i], ids[j]))
    tp = len(truth_pairs & pred_pairs)
    precision = tp / len(pred_pairs) if pred_pairs else 1.0
    recall = tp / len(truth_pairs) if truth_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"pairs_truth": len(truth_pairs), "pairs_predicted": len(pred_pairs), "true_positive_pairs": tp,
            "precision": precision, "recall": recall, "f1": f1,
            "customers_resolved": int(m["customer_key"].nunique()), "customers_truth": int(m["_true_id"].nunique())}
