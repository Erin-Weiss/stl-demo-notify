"""End-to-end geometry tests for stl_demo_notify.analysis.find_neighbors."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from stl_demo_notify import config
from stl_demo_notify.analysis import find_neighbors


def _square(cx: float, cy: float, half: float = 5.0):
    return box(cx - half, cy - half, cx + half, cy + half)


@pytest.fixture
def grid_parcels() -> gpd.GeoDataFrame:
    """A 3x3 grid of 10x10 unit squares, 20 units apart center-to-center.

    Index 4 is the center square (the demo site). Indices 1, 3, 5, 7 are its
    orthogonal neighbors (10-unit edge gap). Indices 0, 2, 6, 8 are diagonal
    neighbors (~14.14-unit corner gap).
    """
    centers = [
        (-20, -20),
        (0, -20),
        (20, -20),
        (-20, 0),
        (0, 0),
        (20, 0),
        (-20, 20),
        (0, 20),
        (20, 20),
    ]
    n = len(centers)
    return gpd.GeoDataFrame(
        {
            "_HANDLE": [f"H{i}" for i in range(n)],
            "SITEADDR": [f"{i} TEST ST" for i in range(n)],
            "ZIP": ["63101"] * n,
            "NUMBLDGS": [1] * n,
            "NUMUNITS": [1] * n,
            "LANDUSE1": ["1110"] * n,
            "VACANTLAND": ["N"] * n,
            "ASMTIMPROV": [1000] * n,
        },
        geometry=[_square(cx, cy) for cx, cy in centers],
        crs=f"EPSG:{config.CRS_EPSG}",
    )


# 10-unit gap < buffer_m (~12.2) < 14.14-unit diagonal gap.
_BUFFER_FEET = 40.0


def test_only_orthogonal_neighbors_fall_within_buffer(grid_parcels):
    sites = pd.DataFrame({"apn": ["TARGET"]})
    matches = {0: 4}
    detail, _ = find_neighbors(sites, grid_parcels, matches, buffer_feet=_BUFFER_FEET)

    assert set(detail["neighbor_handle"]) == {"H1", "H3", "H5", "H7"}


def test_distances_match_the_known_gap_within_tolerance(grid_parcels):
    sites = pd.DataFrame({"apn": ["TARGET"]})
    matches = {0: 4}
    detail, _ = find_neighbors(sites, grid_parcels, matches, buffer_feet=_BUFFER_FEET)

    expected_ft = 10.0 * config.FEET_PER_METER
    for dist in detail["distance_ft"]:
        assert dist == pytest.approx(expected_ft, abs=0.5)


def test_demo_site_excluded_from_its_own_neighbor_list(grid_parcels):
    sites = pd.DataFrame({"apn": ["TARGET"]})
    matches = {0: 4}
    detail, _ = find_neighbors(sites, grid_parcels, matches, buffer_feet=_BUFFER_FEET)

    assert "H4" not in set(detail["neighbor_handle"])
