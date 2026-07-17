"""Command-line entry point for stl-demo-notify."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import analysis, citydata, config, mapping, matching, outputs

logger = logging.getLogger(__name__)

_APN_COLUMN_CANDIDATES = [
    "apn",
    "parcel",
    "parcelid",
    "parcelno",
    "parcelnum",
    "parcelnumber",
    "asrparcel",
    "taxid",
]
_ADDRESS_COLUMN_CANDIDATES = [
    "address",
    "siteaddress",
    "streetaddress",
    "propertyaddress",
    "situsaddress",
    "fulladdress",
]

_OUTPUT_FILENAMES = [
    "match_report.txt",
    "doorhanger_list.csv",
    "doorhanger_list.xlsx",
    "field_review_list.csv",
    "site_checklists.xlsx",
    "assumptions_log.txt",
    "demo_notification_map.html",
]


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized = {c.lower().replace("_", "").replace(" ", ""): c for c in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _looks_like_address(value: str) -> bool:
    return bool(re.match(r"^\d+\s", value)) and bool(
        re.search(rf"\b({matching.STREET_SUFFIXES})\b", value, re.IGNORECASE)
    )


def _find_address_column_by_content(df: pd.DataFrame, exclude: set[str]) -> str | None:
    """Fall back to sniffing column values for address-shaped text.

    Only used when no column name matches _ADDRESS_COLUMN_CANDIDATES, e.g. a
    client CSV with an unrecognized header like "Property Location".
    """
    for col in df.columns:
        if col in exclude:
            continue
        values = df[col].dropna().astype(str)
        if values.empty:
            continue
        if values.map(_looks_like_address).mean() > 0.5:
            return col
    return None


def _load_sites(
    path: Path, apn_column: str | None, address_column: str | None
) -> pd.DataFrame:
    """Read a site list (CSV or Excel) and standardize its APN/address columns."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path)
    else:
        raw = pd.read_csv(path)

    apn_col = apn_column or _find_column(list(raw.columns), _APN_COLUMN_CANDIDATES)
    address_col = address_column or _find_column(
        list(raw.columns), _ADDRESS_COLUMN_CANDIDATES
    )
    if address_col is None:
        claimed = {apn_col} if apn_col else set()
        address_col = _find_address_column_by_content(raw, claimed)

    if apn_col is None and address_col is None:
        raise SystemExit(
            "Could not find an APN or address column in "
            f"{path}. Pass --apn-column or --address-column explicitly."
        )

    sites = pd.DataFrame(index=raw.index)
    if apn_col:
        sites["apn"] = raw[apn_col]
    if address_col:
        sites["address"] = raw[address_col]
    return sites


def _prepare_data(args: argparse.Namespace) -> None:
    citydata.fetch_landuse_vocabulary(force=args.force)
    citydata.build_parcel_cache(force=args.force)


def _run(args: argparse.Namespace) -> None:
    if not config.PARCEL_CACHE_PATH.exists():
        raise SystemExit(
            f"{config.PARCEL_CACHE_PATH} not found. "
            "Run 'stl-demo-notify prepare-data' first."
        )

    output_dir = Path(args.output_dir)
    existing = [f for f in _OUTPUT_FILENAMES if (output_dir / f).exists()]
    if existing and not args.overwrite:
        raise SystemExit(
            f"{output_dir} already has output from a previous run "
            f"({', '.join(existing)}). Pass --overwrite to replace it."
        )

    sites = _load_sites(Path(args.input), args.apn_column, args.address_column)
    parcels = gpd.read_parquet(config.PARCEL_CACHE_PATH)
    parcels_m = parcels.to_crs(epsg=config.CRS_EPSG)
    landuse_lookup = citydata.load_landuse_lookup()

    output_dir.mkdir(parents=True, exist_ok=True)

    matches, records = matching.match_sites(sites, parcels_m)
    outputs.write_match_report(output_dir / "match_report.txt", records)
    if not matches:
        raise SystemExit(
            "No sites matched a city parcel; see "
            f"{output_dir / 'match_report.txt'} for details."
        )

    detail, buffers = analysis.find_neighbors(
        sites, parcels_m, matches, buffer_feet=args.buffer
    )
    detail = analysis.label_landuse(detail, landuse_lookup)
    detail["suggested_hangers"] = analysis.suggested_hangers(detail["NUMUNITS"])
    kept, excluded, field_review = analysis.apply_structure_filter(detail)
    dedup, single_pass, separate_events = analysis.dedupe_and_totals(kept)

    outputs.write_doorhanger_outputs(output_dir, dedup, kept, field_review, excluded)
    outputs.write_site_checklists(
        output_dir / "site_checklists.xlsx", sites, matches, kept, excluded, parcels_m
    )
    outputs.write_assumptions_log(
        output_dir / "assumptions_log.txt",
        buffer_feet=args.buffer,
        structure_filter_method=analysis.STRUCTURE_FILTER_METHOD,
        matched_count=len(matches),
        total_sites=len(sites),
        unique_addresses=len(dedup),
        total_single_pass=single_pass,
        total_separate_events=separate_events,
        field_review_count=len(field_review),
    )
    if not args.no_map:
        m = mapping.build_map(sites, parcels_m, matches, buffers, kept, excluded)
        m.save(output_dir / "demo_notification_map.html")

    print(f"Matched {len(matches)} of {len(sites)} sites (see match_report.txt)")
    print(f"Unique addresses on door hanger list: {len(dedup)}")
    print(f"Hangers, single-pass notification: {single_pass}")
    print(f"Hangers, separate per-site events: {separate_events}")
    print(f"Field review parcels: {len(field_review)}")
    print(f"Files written to {output_dir}/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stl-demo-notify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare-data", help="build the local parcel cache and land use vocabulary"
    )
    prepare.add_argument(
        "--force", action="store_true", help="rebuild even if the cache exists"
    )
    prepare.set_defaults(func=_prepare_data)

    run = subparsers.add_parser("run", help="run the notification analysis")
    run.add_argument(
        "--input", required=True, help="path to a site list CSV or Excel file"
    )
    run.add_argument("--buffer", type=float, default=config.DEFAULT_BUFFER_FEET)
    run.add_argument("--output-dir", default="output")
    run.add_argument("--apn-column")
    run.add_argument("--address-column")
    run.add_argument("--no-map", action="store_true")
    run.add_argument(
        "--overwrite",
        action="store_true",
        help="allow overwriting an existing output directory",
    )
    run.set_defaults(func=_run)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
