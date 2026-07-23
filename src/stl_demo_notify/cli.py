"""Command-line entry point for stl-demo-notify."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd

from . import analysis, citydata, config, mapping, matching, outputs, siteinput

logger = logging.getLogger(__name__)

_OUTPUT_FILENAMES = [
    "match_report.txt",
    "doorhanger_list.csv",
    "doorhanger_list.xlsx",
    "field_review_list.csv",
    "site_checklists.xlsx",
    "assumptions_log.txt",
    "demo_notification_map.html",
]


def _parse_groups(group_specs: list[str], n_sites: int) -> dict[int, int]:
    """Turn --group specs of 1-based site numbers into a {site_index: group_id} map.

    Site numbers match the map badges and checklist sheets (1-based). Sites not
    named in any group are left out and stand alone.
    """
    groups: dict[int, int] = {}
    for group_id, spec in enumerate(group_specs):
        for token in spec.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                number = int(token)
            except ValueError:
                raise SystemExit(
                    f"--group takes site numbers; got {token!r}"
                ) from None
            if not 1 <= number <= n_sites:
                raise SystemExit(
                    f"--group site number {number} is out of range 1..{n_sites}"
                )
            site_index = number - 1
            if site_index in groups:
                raise SystemExit(f"site {number} appears in more than one --group")
            groups[site_index] = group_id
    return groups


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

    try:
        sites = siteinput.load_sites(
            Path(args.input), args.apn_column, args.address_column
        )
    except ValueError as exc:
        raise SystemExit(
            f"{exc} in {args.input}. Pass --apn-column or --address-column."
        ) from exc
    groups = _parse_groups(args.group, len(sites)) if args.group else None
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
    dedup, single_pass, separate_events = analysis.dedupe_and_totals(
        kept, groups=groups
    )

    schedule_note = None
    if groups:
        schedule_note = "; ".join("+".join(spec.split(",")) for spec in args.group)

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
        schedule_groups=schedule_note,
    )
    if not args.no_map:
        m = mapping.build_map(sites, parcels_m, matches, buffers, kept, excluded)
        m.save(output_dir / "demo_notification_map.html")

    events_label = (
        "Hangers, separate events (with schedule groups)"
        if groups
        else "Hangers, separate per-site events"
    )
    print(f"Matched {len(matches)} of {len(sites)} sites (see match_report.txt)")
    print(f"Unique addresses on door hanger list: {len(dedup)}")
    print(f"Hangers, single-pass notification: {single_pass}")
    print(f"{events_label}: {separate_events}")
    if schedule_note:
        print(f"Schedule groups (site numbers): {schedule_note}")
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
    run.add_argument(
        "--group",
        action="append",
        metavar="N,M,...",
        help="sites demolished together as one notification pass, by 1-based "
        "site number; repeatable (e.g. --group 1,13 --group 5,7)",
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
