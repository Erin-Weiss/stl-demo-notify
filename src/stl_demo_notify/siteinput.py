"""Standardize a client site list's APN and address columns for matching.

Column detection failure raises ValueError; callers decide how to handle it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .matching import STREET_SUFFIXES

APN_COLUMN_CANDIDATES = [
    "apn",
    "parcel",
    "parcelid",
    "parcelno",
    "parcelnum",
    "parcelnumber",
    "asrparcel",
    "taxid",
]
ADDRESS_COLUMN_CANDIDATES = [
    "address",
    "siteaddress",
    "streetaddress",
    "propertyaddress",
    "situsaddress",
    "fulladdress",
]


def find_column(columns: list[object], candidates: list[str]) -> object | None:
    normalized = {
        str(c).lower().replace("_", "").replace(" ", ""): c for c in columns
    }
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def looks_like_address(value: str) -> bool:
    return bool(re.match(r"^\d+\s", value)) and bool(
        re.search(rf"\b({STREET_SUFFIXES})\b", value, re.IGNORECASE)
    )


def find_address_column_by_content(
    df: pd.DataFrame, exclude: set[object]
) -> object | None:
    """Fallback when no column name matches ADDRESS_COLUMN_CANDIDATES."""
    for col in df.columns:
        if col in exclude:
            continue
        values = df[col].dropna().astype(str)
        if values.empty:
            continue
        if values.map(looks_like_address).mean() > 0.5:
            return col
    return None


def looks_like_parcel_id(value: str) -> bool:
    digits = re.sub(r"[^0-9]", "", str(value))
    return 9 <= len(digits) <= 12


def find_id_column_by_content(df: pd.DataFrame, exclude: set[object]) -> object | None:
    """Fallback when no column name matches APN_COLUMN_CANDIDATES."""
    for col in df.columns:
        if col in exclude:
            continue
        values = df[col].dropna().astype(str)
        if values.empty:
            continue
        if values.map(looks_like_parcel_id).mean() > 0.5:
            return col
    return None


def looks_like_header_row(values: list) -> bool:
    """True if any value matches a known column name (a header read as data)."""
    known = set(APN_COLUMN_CANDIDATES) | set(ADDRESS_COLUMN_CANDIDATES)
    return any(
        str(v).lower().replace("_", "").replace(" ", "") in known for v in values
    )


def read_site_table(
    source: object, filename: str | None = None, header: object = "infer"
) -> pd.DataFrame:
    """Read a CSV or Excel site list; header=None reads a file with no header row."""
    name = filename or (str(source) if isinstance(source, (str, Path)) else "")
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(source, header=header)
    return pd.read_csv(source, header=header)


def standardize_columns(
    raw: pd.DataFrame,
    apn_column: object | None = None,
    address_column: object | None = None,
) -> pd.DataFrame:
    """Rename the detected APN/address columns; raises ValueError if neither found."""
    apn_col = apn_column
    if apn_col is None:
        apn_col = find_column(list(raw.columns), APN_COLUMN_CANDIDATES)
    address_col = address_column
    if address_col is None:
        address_col = find_column(list(raw.columns), ADDRESS_COLUMN_CANDIDATES)
    if address_col is None:
        claimed = {apn_col} if apn_col is not None else set()
        address_col = find_address_column_by_content(raw, claimed)
    if apn_col is None:
        claimed = {address_col} if address_col is not None else set()
        apn_col = find_id_column_by_content(raw, claimed)

    if apn_col is None and address_col is None:
        raise ValueError("Could not find an APN or address column")

    sites = pd.DataFrame(index=raw.index)
    if apn_col is not None:
        sites["apn"] = raw[apn_col].values
    if address_col is not None:
        sites["address"] = raw[address_col].values
    return sites


def load_sites(
    path: Path,
    apn_column: str | None = None,
    address_column: str | None = None,
) -> pd.DataFrame:
    """Read a site list file and standardize it to apn/address columns."""
    return standardize_columns(read_site_table(path), apn_column, address_column)
