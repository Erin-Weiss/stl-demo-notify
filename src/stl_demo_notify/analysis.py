"""Buffer analysis, structure filtering, and hanger counts for matched sites."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from . import config
from .matching import norm_id


def find_neighbors(
    sites: pd.DataFrame,
    parcels_m: gpd.GeoDataFrame,
    matches: dict[int, int],
    buffer_feet: float = config.DEFAULT_BUFFER_FEET,
) -> tuple[pd.DataFrame, dict[int, object]]:
    """Buffer each matched site and collect every parcel that intersects it.

    `parcels_m` must already be in the projected CRS (config.CRS_EPSG); the
    caller reprojects once and reuses the same GeoDataFrame for checklist
    and map rendering rather than reprojecting per call.

    Returns a detail DataFrame (one row per site-neighbor pair, sorted by
    site then distance) and a dict of site index to buffer geometry, both
    in the projected CRS, for later map rendering.
    """
    buffer_m = buffer_feet / config.FEET_PER_METER
    sindex = parcels_m.sindex
    out_cols = [c for c in config.OUTPUT_COLS if c in parcels_m.columns]

    detail_rows = []
    buffers: dict[int, object] = {}
    for site_idx, parcel_idx in matches.items():
        geom = parcels_m.geometry.loc[parcel_idx]
        buf = geom.buffer(buffer_m)
        buffers[site_idx] = buf

        cand_idx = list(sindex.intersection(buf.bounds))
        hits = parcels_m.iloc[cand_idx]
        hits = hits[hits.intersects(buf)]

        demo_apn = sites.loc[site_idx, "apn"] if "apn" in sites.columns else ""
        demo_address = (
            sites.loc[site_idx, "address"] if "address" in sites.columns else ""
        )

        for neighbor_idx, row in hits.iterrows():
            if neighbor_idx == parcel_idx:
                continue
            dist_ft = geom.distance(row.geometry) * config.FEET_PER_METER
            rec = {
                "site_index": site_idx,
                "demo_apn": demo_apn,
                "demo_address": demo_address,
                "neighbor_handle": row["_HANDLE"],
                "distance_ft": round(dist_ft, 1),
                "_row": neighbor_idx,
            }
            for c in out_cols:
                rec[c] = row.get(c, "")
            detail_rows.append(rec)

    detail = pd.DataFrame(detail_rows)
    if not detail.empty:
        detail = detail.sort_values(["site_index", "distance_ft"]).reset_index(
            drop=True
        )
    return detail, buffers


def label_landuse(detail: pd.DataFrame, landuse_lookup: dict[str, str]) -> pd.DataFrame:
    """Add a LANDUSE_DESC column by resolving LANDUSE1 codes against the vocabulary."""
    if "LANDUSE1" not in detail.columns:
        return detail
    detail = detail.copy()

    def _label(code: object) -> str:
        digits = norm_id(code) or "0"
        return landuse_lookup.get(
            str(int(digits)), f"Code {digits} (not in city vocabulary)"
        )

    detail["LANDUSE_DESC"] = detail["LANDUSE1"].map(_label)
    return detail


def suggested_hangers(numunits: pd.Series) -> pd.Series:
    """Assessor dwelling unit count, floored at 1 hanger per address."""
    units = pd.to_numeric(numunits, errors="coerce").fillna(0)
    return units.clip(lower=1).astype(int)


STRUCTURE_FILTER_METHOD = "NUMBLDGS > 0 (assessor's building count)"


def _review_reason(vacant_value: str, improved_value: float) -> str:
    """Describe which conflicting field(s) triggered a field-review flag."""
    reasons = []
    if vacant_value == "N":
        reasons.append("VACANTLAND marked 'N' (not vacant)")
    if improved_value > 0:
        reasons.append(f"ASMTIMPROV recorded at {improved_value:g} (> 0)")
    return "CHECK: " + "; ".join(reasons)


def apply_structure_filter(
    detail: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split neighbors into kept (has a structure) and excluded (likely vacant).

    A parcel is kept if the assessor's building count (NUMBLDGS) is greater
    than zero. Excluded parcels whose VACANTLAND/ASMTIMPROV fields conflict
    with that exclusion go on a separate field-review list for in-person
    verification.
    """
    numbldgs = pd.to_numeric(detail["NUMBLDGS"], errors="coerce").fillna(0)
    has_structure = numbldgs > 0

    excluded = detail[~has_structure].drop(
        columns=["suggested_hangers"], errors="ignore"
    ).copy()
    kept = detail[has_structure].copy()

    excluded["exclusion_reason"] = (
        "NUMBLDGS = 0 (assessor records no buildings on this parcel)"
    )
    vacant = excluded.get("VACANTLAND", pd.Series("", index=excluded.index))
    vacant = vacant.astype(str).str.strip().str.upper()
    improved = pd.to_numeric(excluded.get("ASMTIMPROV", 0), errors="coerce").fillna(0)
    conflict = (vacant == "N") | (improved > 0)
    excluded["review_flag"] = ""
    excluded.loc[conflict, "review_flag"] = [
        _review_reason(v, imp) for v, imp in zip(vacant[conflict], improved[conflict])
    ]

    field_review = excluded[conflict].drop_duplicates("neighbor_handle").copy()
    field_review["near_demo_sites"] = field_review["neighbor_handle"].map(
        excluded.groupby("neighbor_handle")["demo_address"].apply(
            lambda s: "; ".join(sorted(set(s)))
        )
    )
    return kept, excluded, field_review


def grouped_demo_addresses(detail_kept: pd.DataFrame) -> pd.Series:
    """Map each neighbor_handle to the sorted, deduplicated demo addresses near it."""
    return detail_kept.groupby("neighbor_handle")["demo_address"].apply(
        lambda s: sorted(set(s))
    )


def parse_siteaddr(siteaddr: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Split a SITEADDR column into (street name, house number) for sorting/grouping."""
    addr = siteaddr.astype(str)
    street = addr.str.replace(r"^\d+[-\d]*\s*", "", regex=True)
    house_num = pd.to_numeric(addr.str.extract(r"^(\d+)")[0], errors="coerce")
    return street, house_num


def dedupe_and_totals(detail_kept: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Collapse neighbor rows to one per unique address, with print-order totals.

    Returns the deduplicated door hanger list, the single-pass hanger total
    (each address noticed once), and the separate-events total (each address
    noticed once per nearby site it falls within, summed).
    """
    out_cols = [c for c in config.OUTPUT_COLS if c in detail_kept.columns]

    near_sites = grouped_demo_addresses(detail_kept)
    near_sites_join = near_sites.apply("; ".join)

    dedup = detail_kept.drop_duplicates("neighbor_handle").copy()
    keep_cols = ["neighbor_handle"] + out_cols + [
        c for c in ["LANDUSE_DESC", "suggested_hangers"] if c in dedup.columns
    ]
    dedup = dedup[keep_cols + ["_row"]]
    dedup["near_demo_sites"] = dedup["neighbor_handle"].map(near_sites_join)
    dedup["notifications_needed"] = dedup["neighbor_handle"].map(near_sites.apply(len))

    if "SITEADDR" in dedup.columns:
        street, house_num = parse_siteaddr(dedup["SITEADDR"])
        dedup = (
            dedup.assign(_street=street, _num=house_num)
            .sort_values(["_street", "_num"])
            .drop(columns=["_street", "_num"])
        )

    total_single_pass = int(dedup["suggested_hangers"].sum())
    total_separate_events = int(
        (dedup["suggested_hangers"] * dedup["notifications_needed"]).sum()
    )
    return dedup, total_single_pass, total_separate_events


def walking_order(
    site_rows: pd.DataFrame, parcels_m: gpd.GeoDataFrame, start_point: object
) -> pd.DataFrame:
    """Order a site's neighbor addresses for an on-foot notification walk.

    Heuristic only: visits the nearest street first (by centroid distance
    from start_point), then orders house numbers within each street. Not an
    optimized route.
    """
    df = site_rows.copy()
    centroids = parcels_m.geometry.loc[df["_row"]].centroid
    df["_x"] = centroids.x.values
    df["_y"] = centroids.y.values
    df["_street"], df["_num"] = parse_siteaddr(df["SITEADDR"])

    street_centroid = df.groupby("_street")[["_x", "_y"]].mean()
    remaining = set(street_centroid.index)
    cx, cy = start_point.x, start_point.y
    rank: dict[str, int] = {}
    i = 0
    while remaining:
        nearest = min(
            remaining,
            key=lambda s: (street_centroid.loc[s, "_x"] - cx) ** 2
            + (street_centroid.loc[s, "_y"] - cy) ** 2,
        )
        rank[nearest] = i
        i += 1
        remaining.remove(nearest)
        cx, cy = street_centroid.loc[nearest, "_x"], street_centroid.loc[nearest, "_y"]

    df["_street_rank"] = df["_street"].map(rank)
    df = df.sort_values(["_street_rank", "_num"])
    return df.drop(columns=["_x", "_y", "_street", "_num", "_street_rank"])
