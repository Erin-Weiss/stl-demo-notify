"""Download City of St. Louis parcel data and build the local GeoParquet cache."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from . import config
from .matching import norm_id

logger = logging.getLogger(__name__)


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        logger.info("already downloaded: %s", dest)
        return dest
    logger.info("downloading %s", url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def extract(zip_path: Path, out_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)
    return out_dir


def find_file(folder: Path, suffix: str) -> Path | None:
    for path in folder.rglob(f"*{suffix}"):
        if path.is_file():
            return path
    return None


def build_parcel_cache(force: bool = False) -> Path:
    """Download, join, and cache parcel shapes with land record attributes."""
    if config.PARCEL_CACHE_PATH.exists() and not force:
        logger.info("cache already exists: %s", config.PARCEL_CACHE_PATH)
        return config.PARCEL_CACHE_PATH

    shp_zip = download(
        config.PARCEL_SHAPE_URL, config.DOWNLOAD_DIR / "prcl_shape.zip"
    )
    par_zip = download(config.LAND_RECORDS_URL, config.DOWNLOAD_DIR / "par.zip")

    shp_dir = extract(shp_zip, config.DOWNLOAD_DIR / "prcl_shape")
    par_dir = extract(par_zip, config.DOWNLOAD_DIR / "par")

    shp_path = find_file(shp_dir, ".shp")
    dbf_path = find_file(par_dir, ".dbf")
    if shp_path is None:
        raise FileNotFoundError("no .shp file found inside prcl_shape.zip")
    if dbf_path is None:
        raise FileNotFoundError("no .dbf file found inside par.zip")

    parcels = gpd.read_file(shp_path)
    logger.info("%d parcels loaded", len(parcels))
    parcels["_HANDLE"] = parcels["HANDLE"].map(norm_id)

    attrs = gpd.read_file(dbf_path)
    attrs = pd.DataFrame(attrs.drop(columns="geometry", errors="ignore"))
    attrs["_HANDLE"] = attrs["HANDLE"].map(norm_id)
    parcels = parcels.merge(
        attrs.drop_duplicates("_HANDLE").drop(columns=["HANDLE"]),
        on="_HANDLE",
        how="left",
    )
    logger.info(
        "joined; %d parcels have a site address", parcels["SITEADDR"].notna().sum()
    )

    keep_cols = ["_HANDLE", "geometry"] + [
        c for c in config.ID_COLUMNS + config.OUTPUT_COLS if c in parcels.columns
    ]
    parcels = parcels[list(dict.fromkeys(keep_cols))]

    config.PARCEL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    parcels.to_parquet(config.PARCEL_CACHE_PATH)
    logger.info("wrote %s", config.PARCEL_CACHE_PATH)
    return config.PARCEL_CACHE_PATH


def fetch_landuse_vocabulary(force: bool = False) -> Path:
    """Download the official Assessor Land Use vocabulary as a committed CSV."""
    if config.LANDUSE_VOCABULARY_PATH.exists() and not force:
        logger.info(
            "vocabulary already exists: %s", config.LANDUSE_VOCABULARY_PATH
        )
        return config.LANDUSE_VOCABULARY_PATH

    logger.info("downloading %s", config.LANDUSE_VOCABULARY_URL)
    response = requests.get(config.LANDUSE_VOCABULARY_URL, timeout=60)
    response.raise_for_status()

    vocab = pd.read_csv(io.StringIO(response.text))
    vocab = vocab[["IDENTIFIER", "TITLE"]].rename(
        columns={"IDENTIFIER": "code", "TITLE": "description"}
    )
    vocab["code"] = vocab["code"].map(norm_id)
    vocab = vocab.drop_duplicates("code").sort_values("code")

    config.LANDUSE_VOCABULARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    vocab.to_csv(config.LANDUSE_VOCABULARY_PATH, index=False)
    logger.info("wrote %s (%d codes)", config.LANDUSE_VOCABULARY_PATH, len(vocab))
    return config.LANDUSE_VOCABULARY_PATH


def load_landuse_lookup() -> dict[str, str]:
    """Return a land use code -> description dict for analysis.label_landuse."""
    vocab = pd.read_csv(config.LANDUSE_VOCABULARY_PATH, dtype={"code": str})
    return dict(zip(vocab["code"], vocab["description"], strict=True))
