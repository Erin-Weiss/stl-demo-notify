"""Writers for door hanger lists, checklists, and client-facing run logs."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import analysis, config

CHECKLIST_COLUMNS = [
    "done",
    "SITEADDR",
    "suggested_hangers",
    "LANDUSE_DESC",
    "distance_ft",
]

ASSUMPTIONS = [
    "{buffer_feet:g} ft is measured outward from the demolition parcel's "
    "boundary (not its center and not the building footprint), per client "
    "confirmation.",
    "A neighbor counts if any part of its parcel touches the buffer, even "
    "if the house itself sits farther back on the lot.",
    "Only parcels believed to have a structure are included in the main "
    "list (assessor building count NUMBLDGS > 0). Excluded parcels are "
    "kept on a review sheet; rows with conflicting fields (VACANTLAND, "
    "ASMTIMPROV) are listed separately as a Field Review list for "
    "in-person verification.",
    "suggested_hangers = the assessor's dwelling unit count (NUMUNITS), "
    "but never less than 1 per listed address. Non-residential buildings "
    "(NUMUNITS = 0) therefore get 1. The client may adjust this policy.",
    "DEMOLITION TIMING: sites may be demolished at different times, so an "
    "address near multiple sites may need notification multiple times. "
    "Two totals are reported. The SINGLE-PASS total counts each address "
    "once and applies only if all sites are noticed together. The "
    "SEPARATE-EVENTS total (sum of the per-site checklist totals) counts "
    "overlap addresses once per site and is the correct print quantity if "
    "each site is noticed on its own schedule. The client should choose "
    "based on the demolition schedule; sites demolished together can "
    "share one notification pass.",
    "Checklist walking order is a heuristic (visit the nearest street "
    "first, then house-number order within each street), not an "
    "optimized route.",
    "Output contains physical (situs) addresses only, no owner names or "
    "mailing addresses, per client confirmation.",
    "The demolition parcels themselves are excluded from the neighbor "
    "list.",
    "Client APNs are matched against the city's ASRPARCEL field first, "
    "then HANDLE and other parcel ID fields. See match_report.txt.",
    "City SITEADDR values may show address ranges (e.g., '4613-5 LABADIE "
    "AV') for buildings spanning multiple street numbers; these are the "
    "same properties the client listed by a single number.",
    "LANDUSE_DESC labels come from the City of St. Louis's official "
    "Assessor Land Use vocabulary (vocabulary id 24), refreshed each time "
    "prepare-data runs.",
    "Parcel geometry and attributes come from City of St. Louis Open Data "
    "(prcl_shape.zip and par.zip) as of the download date, and are "
    "assumed current and accurate. Assessor fields can lag reality; the "
    "Field Review list exists for exactly that reason.",
    "Condo buildings can contain many parcel records at one street "
    "address. The deduplicated list collapses exact duplicate site "
    "addresses.",
]


def _match_line(record: dict[str, object]) -> str:
    label = record["apn"] or record["address"] or f"site {record['site_index']}"
    addr_note = (
        f"  ({record['address']})" if record["address"] and record["apn"] else ""
    )
    if record["matched"]:
        return (
            f"matched {label}{addr_note}  via {record['method']}  "
            f"[city record: {record['city_siteaddr']}]"
        )
    return f"NOT FOUND: {label}{addr_note}"


def write_match_report(path: Path, records: list[dict[str, object]]) -> None:
    """Write a human-readable log of how each site record matched a city parcel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [_match_line(r) for r in records]
    header = (
        f"Run date: {date.today().isoformat()}\n"
        "How each site record was matched to a City of St. Louis parcel.\n"
        "Match order tried: " + ", ".join(config.ID_COLUMNS) + ", then address.\n\n"
    )
    path.write_text(header + "\n".join(lines) + "\n")


def write_doorhanger_outputs(
    output_dir: Path,
    dedup: pd.DataFrame,
    detail_kept: pd.DataFrame,
    field_review: pd.DataFrame,
    excluded: pd.DataFrame,
) -> None:
    """Write the master door hanger list, field review list, and combined workbook."""
    output_dir.mkdir(parents=True, exist_ok=True)
    internal_cols = ["_row", "site_index"]
    dedup_out = dedup.drop(columns=internal_cols, errors="ignore")
    field_review_out = field_review.drop(columns=internal_cols, errors="ignore")

    dedup_out.to_csv(output_dir / "doorhanger_list.csv", index=False)
    field_review_out.to_csv(output_dir / "field_review_list.csv", index=False)

    with pd.ExcelWriter(output_dir / "doorhanger_list.xlsx") as xw:
        dedup_out.to_excel(xw, sheet_name="Doorhanger List", index=False)
        detail_kept.drop(columns=internal_cols, errors="ignore").to_excel(
            xw, sheet_name="Detail by Demo Site", index=False
        )
        field_review_out.to_excel(xw, sheet_name="Field Review", index=False)
        excluded.drop_duplicates("neighbor_handle").drop(
            columns=internal_cols, errors="ignore"
        ).to_excel(xw, sheet_name="Excluded (likely vacant)", index=False)


def _safe_sheet_name(i: int, label: str) -> str:
    name = re.sub(r"[\\/*?\[\]:]", "", f"{i:02d} {label}")
    return name[:31]


def write_site_checklists(
    path: Path,
    sites: pd.DataFrame,
    matches: dict[int, int],
    detail_kept: pd.DataFrame,
    excluded: pd.DataFrame,
    parcels_m: gpd.GeoDataFrame,
) -> None:
    """Write one printable walking checklist per matched site, plus a summary sheet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    field_review_all = excluded[excluded["review_flag"] != ""]

    summary_rows = []
    sheet_frames = []
    for i, site_idx in enumerate(sites.index, 1):
        if site_idx not in matches:
            continue
        demo_apn = sites.loc[site_idx, "apn"] if "apn" in sites.columns else ""
        demo_address = (
            sites.loc[site_idx, "address"]
            if "address" in sites.columns
            else str(site_idx)
        )

        site_rows = detail_kept[
            detail_kept["site_index"] == site_idx
        ].drop_duplicates("neighbor_handle")
        start_point = parcels_m.geometry.loc[matches[site_idx]].centroid
        ordered = analysis.walking_order(site_rows, parcels_m, start_point)
        table = ordered.copy()
        table["done"] = ""
        table = table[[c for c in CHECKLIST_COLUMNS if c in table.columns]]

        site_review = field_review_all[
            field_review_all["site_index"] == site_idx
        ].drop_duplicates("neighbor_handle")

        summary_rows.append(
            {
                "demo_apn": demo_apn,
                "demo_address": demo_address,
                "addresses": len(table),
                "suggested_hangers": int(table["suggested_hangers"].sum()),
                "field_review_parcels": len(site_review),
            }
        )
        sheet_frames.append((i, demo_apn, demo_address, table, site_review))

    with pd.ExcelWriter(path) as xw:
        summary_df = pd.DataFrame(summary_rows)
        if not summary_df.empty:
            totals = {
                "demo_apn": "",
                "demo_address": "TOTAL",
                "addresses": int(summary_df["addresses"].sum()),
                "suggested_hangers": int(summary_df["suggested_hangers"].sum()),
                "field_review_parcels": int(summary_df["field_review_parcels"].sum()),
            }
            summary_df = pd.concat(
                [summary_df, pd.DataFrame([totals])], ignore_index=True
            )
        summary_df = summary_df.rename(
            columns={"suggested_hangers": "suggested_hangers (separate-events basis)"}
        )
        summary_df.to_excel(xw, sheet_name="Summary", index=False)

        for i, demo_apn, demo_address, table, site_review in sheet_frames:
            name = _safe_sheet_name(i, str(demo_address))
            table.to_excel(xw, sheet_name=name, index=False, startrow=3)
            ws = xw.sheets[name]
            header = f"Demo site: {demo_address}"
            if demo_apn:
                header += f"  (APN {demo_apn})"
            ws["A1"] = header
            ws["A2"] = (
                f"Addresses: {len(table)}   "
                f"Suggested hangers: {int(table['suggested_hangers'].sum())}"
            )
            ws["A3"] = (
                "Walking order: nearest street first, house-number order within street"
            )
            if len(site_review):
                start = 3 + len(table) + 3
                ws.cell(
                    row=start,
                    column=1,
                    value=(
                        "Field review parcels near this site "
                        "(no building per assessor; verify in person):"
                    ),
                )
                for k, (_, r) in enumerate(site_review.iterrows(), start=start + 1):
                    ws.cell(row=k, column=2, value=str(r.get("SITEADDR", "")))


def write_assumptions_log(
    path: Path,
    buffer_feet: float,
    structure_filter_method: str,
    matched_count: int,
    total_sites: int,
    unique_addresses: int,
    total_single_pass: int,
    total_separate_events: int,
    field_review_count: int,
    schedule_groups: str | None = None,
) -> None:
    """Write the run's parameters and client-facing methodology assumptions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    separate_label = (
        "Hangers if each site noticed SEPARATELY"
        if schedule_groups is None
        else "Hangers with the schedule groups below"
    )
    lines = [
        f"Run date: {date.today().isoformat()}",
        f"Buffer distance: {buffer_feet:g} ft",
        f"Structure filter method: {structure_filter_method}",
        f"Demolition sites matched: {matched_count} of {total_sites} "
        "(see match_report.txt)",
        f"Unique addresses on door hanger list: {unique_addresses}",
        f"Hangers if all sites noticed in a SINGLE PASS: {total_single_pass}",
        f"{separate_label}: {total_separate_events}",
        f"Parcels on field review list: {field_review_count}",
    ]
    if schedule_groups is not None:
        lines.append(
            f"Schedule groups (sites demolished together, by site number): "
            f"{schedule_groups}. Per-site checklists are unchanged; this only "
            f"affects the separate-events hanger total above."
        )
    lines += [
        "",
        "Assumptions:",
    ]
    lines += [
        f"{i}. {a.format(buffer_feet=buffer_feet)}"
        for i, a in enumerate(ASSUMPTIONS, 1)
    ]
    path.write_text("\n".join(lines) + "\n")
