"""Command-line entry point for stl-demo-notify."""

from __future__ import annotations

import argparse
import logging
import sys

from . import citydata


def _prepare_data(args: argparse.Namespace) -> None:
    citydata.fetch_landuse_vocabulary(force=args.force)
    citydata.build_parcel_cache(force=args.force)


def _run(args: argparse.Namespace) -> None:
    raise NotImplementedError("the run command is implemented in Phase 3")


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
    run.add_argument("--input", required=True, help="path to a site list CSV")
    run.add_argument("--buffer", type=float, default=500.0)
    run.add_argument("--output-dir", default="output")
    run.add_argument("--apn-column")
    run.add_argument("--address-column")
    run.add_argument("--no-map", action="store_true")
    run.set_defaults(func=_run)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
