"""Match client site records (APN and/or address) to City of St. Louis parcels."""

from __future__ import annotations

import logging
import re

import geopandas as gpd
import pandas as pd

from . import config

logger = logging.getLogger(__name__)

STREET_SUFFIXES = (
    "AVENUE|AVE|BOULEVARD|BLVD|STREET|ST|DRIVE|DR|PLACE|PL|COURT|CT|ROAD|RD|"
    "LANE|LN|TERRACE|TER|WAY"
)


def norm_id(value: object) -> str:
    """Reduce an ID to digits only, tolerating float, string, or missing input."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = re.sub(r"\.0+$", "", str(value).strip())
    return re.sub(r"[^0-9]", "", s)


def split_address(address: str) -> tuple[str, str]:
    """Split '4615 Labadie Avenue' into ('4615', 'LABADIE')."""
    match = re.match(r"^(\d+)\s+(.*)$", address.strip())
    if match is None:
        raise ValueError(f"address has no leading house number: {address!r}")
    num, rest = match.group(1), match.group(2).upper()
    rest = re.sub(r"^(N|S|E|W|NORTH|SOUTH|EAST|WEST)\s+", "", rest)
    rest = re.sub(rf"\s+({STREET_SUFFIXES})\.?$", "", rest)
    return num, rest.strip()


def _build_id_lookups(parcels: gpd.GeoDataFrame) -> list[tuple[str, dict[str, int]]]:
    """Map each ID_COLUMNS candidate to {normalized id: parcel index}."""
    lookups = []
    for col in config.ID_COLUMNS:
        if col == "HANDLE":
            series = parcels["_HANDLE"]
        elif col in parcels.columns:
            series = parcels[col].map(norm_id)
        else:
            continue
        d: dict[str, int] = {}
        for idx, value in series.items():
            if value and value not in d:
                d[value] = idx
        lookups.append((col, d))
    return lookups


def match_sites(
    sites: pd.DataFrame, parcels: gpd.GeoDataFrame
) -> tuple[dict[int, int], list[dict[str, object]]]:
    """Match each site row to a parcel by APN first, address second.

    `sites` must have an "apn" column, an "address" column, or both. Returns
    a dict of site index to matched parcel index, and one match-report
    record per site row.
    """
    has_apn = "apn" in sites.columns
    has_address = "address" in sites.columns
    if not has_apn and not has_address:
        raise ValueError(
            "sites must have an 'apn' column, an 'address' column, or both"
        )

    lookups = _build_id_lookups(parcels)
    blob = None
    if has_address:
        blob = (
            parcels[["SITEADDR"]]
            .fillna("")
            .astype(str)
            .agg(" ".join, axis=1)
            .str.upper()
        )

    matches: dict[int, int] = {}
    records: list[dict[str, object]] = []
    for site_idx, row in sites.iterrows():
        apn = row["apn"] if has_apn else None
        address = row["address"] if has_address else None
        parcel_idx = None
        method = None

        if apn is not None and pd.notna(apn):
            apn_n = norm_id(apn)
            for col, d in lookups:
                if apn_n in d:
                    parcel_idx, method = d[apn_n], f"exact match on {col}"
                    break

        if parcel_idx is None and address is not None and pd.notna(address):
            try:
                num, name = split_address(str(address))
            except ValueError:
                # An unparseable value (e.g. a stray header row) simply skips matching.
                num = name = None
            if num is not None:
                candidates = parcels.index[
                    blob.str.contains(rf"\b{num}\b", na=False)
                    & blob.str.contains(rf"\b{re.escape(name)}\b", na=False)
                ]
                if len(candidates) > 1:
                    logger.warning(
                        "address %r matched %d city parcels; using the first",
                        address,
                        len(candidates),
                    )
                if len(candidates) >= 1:
                    parcel_idx, method = candidates[0], "address (SITEADDR)"

        city_siteaddr = None
        if parcel_idx is not None:
            matches[site_idx] = parcel_idx
            if "SITEADDR" in parcels.columns:
                city_siteaddr = parcels.at[parcel_idx, "SITEADDR"]

        records.append(
            {
                "site_index": site_idx,
                "apn": apn,
                "address": address,
                "matched": parcel_idx is not None,
                "method": method,
                "city_siteaddr": city_siteaddr,
            }
        )

    return matches, records
