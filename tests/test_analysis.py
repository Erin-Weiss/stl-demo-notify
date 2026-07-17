"""Tests for stl_demo_notify.analysis."""

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from stl_demo_notify.analysis import (
    apply_structure_filter,
    dedupe_and_totals,
    suggested_hangers,
    walking_order,
)


class TestSuggestedHangers:
    def test_zero_units_floors_to_one(self):
        assert suggested_hangers(pd.Series([0])).iloc[0] == 1

    def test_four_units_stays_four(self):
        assert suggested_hangers(pd.Series([4])).iloc[0] == 4

    def test_missing_value_treated_as_zero_then_floored(self):
        assert suggested_hangers(pd.Series([None])).iloc[0] == 1

    def test_non_numeric_value_treated_as_zero_then_floored(self):
        assert suggested_hangers(pd.Series(["garbage"])).iloc[0] == 1


class TestApplyStructureFilter:
    def _detail(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "neighbor_handle": ["A", "B", "C"],
                "demo_address": ["Site 1", "Site 1", "Site 1"],
                "NUMBLDGS": [1, 0, 0],
                "VACANTLAND": ["Y", "Y", "N"],
                "ASMTIMPROV": [0, 0, 500],
            }
        )

    def test_kept_has_a_structure(self):
        kept, _, _ = apply_structure_filter(self._detail())
        assert list(kept["neighbor_handle"]) == ["A"]

    def test_excluded_has_no_structure(self):
        _, excluded, _ = apply_structure_filter(self._detail())
        assert set(excluded["neighbor_handle"]) == {"B", "C"}

    def test_field_review_only_flags_conflicting_rows(self):
        # B: VACANTLAND=Y, ASMTIMPROV=0 -> no conflict, not field review.
        # C: VACANTLAND=N -> conflicts with having no structure on record.
        _, _, field_review = apply_structure_filter(self._detail())
        assert list(field_review["neighbor_handle"]) == ["C"]

    def test_review_flag_names_the_specific_condition(self):
        _, _, field_review = apply_structure_filter(self._detail())
        assert "VACANTLAND" in field_review.iloc[0]["review_flag"]


class TestDedupeAndTotals:
    def test_dedupe_and_hanger_totals(self):
        detail_kept = pd.DataFrame(
            {
                "neighbor_handle": ["A", "A", "B"],
                "demo_address": ["Site 1", "Site 2", "Site 1"],
                "SITEADDR": ["100 MAIN ST", "100 MAIN ST", "200 OAK AV"],
                "suggested_hangers": [2, 2, 1],
                "_row": [0, 0, 1],
            }
        )
        dedup, single_pass, separate_events = dedupe_and_totals(detail_kept)

        assert len(dedup) == 2
        assert single_pass == 3
        # A is near 2 sites (2 hangers x 2 sites = 4) + B near 1 site (1 x 1 = 1).
        assert separate_events == 5


class TestWalkingOrder:
    def test_nearest_street_first_then_house_number_order(self):
        parcels_m = gpd.GeoDataFrame(
            {
                "SITEADDR": ["10 A ST", "20 A ST", "5 B ST", "15 B ST"],
            },
            geometry=[
                Point(0, 1),
                Point(0, 2),
                Point(100, 100),
                Point(100, 101),
            ],
            crs="EPSG:26996",
        )
        site_rows = pd.DataFrame(
            {
                "SITEADDR": ["15 B ST", "20 A ST", "5 B ST", "10 A ST"],
                "_row": [3, 1, 2, 0],
            }
        )
        start_point = Point(0, 0)

        ordered = walking_order(site_rows, parcels_m, start_point)

        assert list(ordered["SITEADDR"]) == [
            "10 A ST",
            "20 A ST",
            "5 B ST",
            "15 B ST",
        ]
