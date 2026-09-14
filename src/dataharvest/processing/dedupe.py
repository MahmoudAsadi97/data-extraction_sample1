"""Duplicate detection and merging.

Two records are the same entity when they share a hard identifier
(phone, e-mail, website domain, VAT number) *and* their names are similar,
or when their names are near-identical and they sit at the same postcode /
street. Records that share only a website domain but have different
addresses are flagged as *possible* duplicates (branches, chains) and left
for a human to decide.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..models import DuplicateGroup, FieldStatus, Record
from ..schema import Schema
from .normalize import name_key, url_domain

STATUS_RANK = {FieldStatus.VERIFIED: 3, FieldStatus.UNVERIFIED: 2, FieldStatus.CONFLICT: 1, FieldStatus.INVALID: 0, FieldStatus.MISSING: -1}


@dataclass
class Match:
    a: str
    b: str
    similarity: float
    reason: str
    confident: bool


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _identifiers(rec: Record) -> dict[str, str]:
    ids: dict[str, str] = {}
    if rec.get("phone"):
        ids["phone"] = str(rec.get("phone"))
    if rec.get("email"):
        ids["email"] = str(rec.get("email")).lower()
    if rec.get("website"):
        dom = url_domain(str(rec.get("website")))
        if dom:
            ids["website"] = dom
    if rec.get("vat_number"):
        ids["vat_number"] = str(rec.get("vat_number"))
    for key in ("product_url", "upc"):
        if rec.get(key):
            ids[key] = str(rec.get(key)).lower()
    return ids


def _address_key(rec: Record) -> str:
    street = name_key(str(rec.get("street") or ""))
    number = str(rec.get("house_number") or "").lower().strip()
    postcode = str(rec.get("postcode") or "").strip()
    return f"{postcode}|{street}|{number}".strip("|")


def _identity_conflicts(a: Record, b: Record) -> bool:
    """Different locations or legal identifiers must survive as separate records."""
    return any(a.get(key) and b.get(key) and name_key(str(a.get(key))) != name_key(str(b.get(key)))
               for key in ("country", "postcode", "street", "house_number", "vat_number", "upc"))


def compare(a: Record, b: Record, name_field: str, threshold: int) -> Match | None:
    name_a, name_b = name_key(str(a.get(name_field) or "")), name_key(str(b.get(name_field) or ""))
    sim = fuzz.token_set_ratio(name_a, name_b) if name_a and name_b else 0
    ids_a, ids_b = _identifiers(a), _identifiers(b)
    shared = [k for k in ids_a if k in ids_b and ids_a[k] == ids_b[k]]
    addr_a, addr_b = _address_key(a), _address_key(b)
    same_address = bool(addr_a) and addr_a == addr_b and addr_a.count("|") >= 1
    same_postcode = bool(a.get("postcode")) and a.get("postcode") == b.get("postcode")

    hard_ids = [k for k in shared if k in ("phone", "email", "vat_number", "upc", "product_url")]
    if _identity_conflicts(a, b) and (shared or sim >= threshold):
        return Match(a.record_id, b.record_id, sim, "conflicting location or legal identifier (branch or chain?) - review", False)
    if hard_ids and sim >= 60:
        return Match(a.record_id, b.record_id, sim, f"same {', '.join(hard_ids)}; names {sim:.0f}% similar", True)
    if "website" in shared and sim >= threshold and same_address:
        return Match(a.record_id, b.record_id, sim, f"same website domain and address; names {sim:.0f}% similar", True)
    if sim >= threshold and (same_address or same_postcode or (not a.get("postcode") and not b.get("postcode"))):
        where = "same address" if same_address else ("same postcode" if same_postcode else "no address on either record")
        return Match(a.record_id, b.record_id, sim, f"names {sim:.0f}% similar; {where}", same_address)
    if "website" in shared:
        return Match(a.record_id, b.record_id, sim, f"same website domain but different name/address (branch or chain?) - names {sim:.0f}% similar", False)
    if hard_ids:
        return Match(a.record_id, b.record_id, sim, f"same {', '.join(hard_ids)} but different names - review", False)
    return None


def find_matches(records: list[Record], schema: Schema, threshold: int = 90) -> list[Match]:
    """Blocked pairwise comparison: only records sharing an identifier, postcode or name prefix are compared."""
    name_field = schema.name_field or "company_name"
    blocks: dict[str, list[Record]] = defaultdict(list)
    for rec in records:
        for k, v in _identifiers(rec).items():
            blocks[f"{k}:{v}"].append(rec)
        if rec.get("postcode"):
            blocks[f"pc:{rec.get('postcode')}"].append(rec)
        key = name_key(str(rec.get(name_field) or ""))
        if key:
            blocks[f"name:{key[:6]}"].append(rec)
    seen_pairs: set[tuple[str, str]] = set()
    matches: list[Match] = []
    for members in blocks.values():
        if len(members) < 2:
            continue
        for a, b in itertools.combinations(members, 2):
            pair = tuple(sorted((a.record_id, b.record_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if len(seen_pairs) > 250_000:
                raise ValueError("Duplicate comparison budget exceeded (250,000 pairs). Split the input into smaller, disjoint batches.")
            m = compare(a, b, name_field, threshold)
            if m:
                matches.append(m)
    return matches


def _quality(rec: Record) -> tuple[int, float, int]:
    verified = sum(1 for fv in rec.fields.values() if fv.status == FieldStatus.VERIFIED)
    filled = sum(1 for fv in rec.fields.values() if not fv.is_empty)
    return (verified, rec.completeness, filled)


def merge_into(master: Record, other: Record) -> list[str]:
    """Fill gaps in ``master`` from ``other``; record conflicts. Returns conflict notes."""
    conflicts: list[str] = []
    for name, ofv in other.fields.items():
        if ofv.is_empty:
            continue
        mfv = master.fields.get(name)
        if mfv is None or mfv.is_empty:
            master.set(name, ofv.value, source=ofv.source, status=ofv.status, note=ofv.note)
            continue
        if str(mfv.value).strip().lower() == str(ofv.value).strip().lower():
            if STATUS_RANK[ofv.status] > STATUS_RANK[mfv.status]:
                mfv.status, mfv.note, mfv.source = ofv.status, ofv.note, ofv.source
            elif mfv.status == FieldStatus.UNVERIFIED and ofv.status == FieldStatus.UNVERIFIED and ofv.source != mfv.source:
                mfv.status = FieldStatus.VERIFIED
                mfv.note = f"same value in {mfv.source} and {ofv.source}"
            continue
        master.add_candidate(name, ofv.value)
        if name in ("latitude", "longitude", "description", "opening_hours", "subcategory", "district"):
            continue  # cosmetic differences are not conflicts
        if STATUS_RANK[ofv.status] > STATUS_RANK[mfv.status]:
            old = mfv.value
            master.set(name, ofv.value, source=ofv.source, status=ofv.status, note=ofv.note)
            master.add_candidate(name, old)
        master.mark(name, FieldStatus.CONFLICT, f"differs between sources: {mfv.value!s} vs {ofv.value!s}")
        conflicts.append(f"{name}: {mfv.value!s} vs {ofv.value!s}")
    for f in other.flags:
        if f not in master.flags and not f.startswith("missing"):
            master.flag(f)
    return conflicts


def dedupe(records: list[Record], schema: Schema, *, threshold: int = 90, merge: bool = True) -> list[DuplicateGroup]:
    """Detect duplicates; merge confident groups into one master record. Returns the groups."""
    matches = find_matches(records, schema, threshold)
    by_id = {r.record_id: r for r in records}
    uf = _UnionFind()
    confident_pairs: dict[tuple[str, str], Match] = {}
    possible: list[Match] = []
    for m in matches:
        if m.confident:
            left = [rid for rid in uf.parent if uf.find(rid) == uf.find(m.a)] if m.a in uf.parent else [m.a]
            right = [rid for rid in uf.parent if uf.find(rid) == uf.find(m.b)] if m.b in uf.parent else [m.b]
            if any(_identity_conflicts(by_id[a], by_id[b]) for a in left for b in right):
                m.confident = False
                m.reason = "transitive duplicate group contains conflicting locations or legal identifiers - review"
                possible.append(m)
                continue
            uf.union(m.a, m.b)
            confident_pairs[tuple(sorted((m.a, m.b)))] = m
        else:
            possible.append(m)

    groups: list[DuplicateGroup] = []
    clusters: dict[str, list[str]] = defaultdict(list)
    for rid in list(uf.parent):
        clusters[uf.find(rid)].append(rid)
    n = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        n += 1
        group_id = f"DUP-{n:03d}"
        recs = sorted((by_id[m] for m in members), key=_quality, reverse=True)
        master = recs[0]
        reasons = sorted({confident_pairs[p].reason for p in confident_pairs if p[0] in members and p[1] in members})
        sims = [confident_pairs[p].similarity for p in confident_pairs if p[0] in members and p[1] in members]
        for rec in recs:
            rec.duplicate_group = group_id
        if merge:
            all_conflicts: list[str] = []
            for other in recs[1:]:
                all_conflicts.extend(merge_into(master, other))
                other.duplicate_of = master.record_id
                other.flag(f"duplicate of {master.record_id} (merged)")
            master.flag(f"note: merged {len(recs) - 1} duplicate record(s) - {'; '.join(reasons)}")
            if all_conflicts:
                master.flag("merged duplicates disagree on: " + "; ".join(all_conflicts[:4]) + " - review")
        else:
            for rec in recs:
                rec.flag(f"duplicate group {group_id}: {'; '.join(reasons)} - review")
        groups.append(DuplicateGroup(group_id=group_id, master_id=master.record_id, member_ids=[r.record_id for r in recs],
                                     reason="; ".join(reasons), similarity=max(sims) if sims else 0.0, merged=merge))

    for m in possible:
        a, b = by_id[m.a], by_id[m.b]
        if a.duplicate_group and a.duplicate_group == b.duplicate_group:
            continue
        n += 1
        group_id = f"DUP-{n:03d}"
        for rec in (a, b):
            if not rec.duplicate_group:
                rec.duplicate_group = group_id
            rec.flag(f"possible duplicate ({group_id}): {m.reason.removesuffix(' - review')} - review")
        groups.append(DuplicateGroup(group_id=group_id, master_id=a.record_id, member_ids=[a.record_id, b.record_id],
                                     reason=m.reason, similarity=m.similarity, merged=False))
    return groups
