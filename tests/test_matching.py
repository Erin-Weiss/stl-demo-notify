"""Tests for stl_demo_notify.matching."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from stl_demo_notify.matching import match_sites, norm_id, split_address


class TestNormId:
    def test_plain_digits(self):
        assert norm_id("123456") == "123456"

    def test_int_input(self):
        assert norm_id(123456) == "123456"

    def test_float_with_trailing_zero(self):
        assert norm_id(123456.0) == "123456"
        assert norm_id("123456.0") == "123456"

    def test_strips_non_digit_characters(self):
        assert norm_id("123-456 AB") == "123456"

    def test_none_returns_empty_string(self):
        assert norm_id(None) == ""

    def test_nan_returns_empty_string(self):
        assert norm_id(float("nan")) == ""

    def test_surrounding_whitespace_stripped(self):
        assert norm_id("  123456  ") == "123456"


class TestSplitAddress:
    def test_basic(self):
        assert split_address("4615 Labadie Avenue") == ("4615", "LABADIE")

    def test_strips_directional_abbreviation(self):
        assert split_address("3010 N Newstead Avenue") == ("3010", "NEWSTEAD")

    def test_strips_full_directional_word(self):
        assert split_address("100 North Main Street") == ("100", "MAIN")

    def test_abbreviated_suffix(self):
        assert split_address("100 Main St") == ("100", "MAIN")

    def test_no_leading_house_number_raises(self):
        with pytest.raises(ValueError):
            split_address("Main Street")


@pytest.fixture
def parcels() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "_HANDLE": ["100", "200", "300"],
            "HANDLE": ["100", "200", "300"],
            "ASRPARCEL": ["11111111111", "22222222222", "33333333333"],
            "PARCEL10": ["", "", ""],
            "PARCEL": ["", "", ""],
            "SITEADDR": ["100 MAIN ST", "200 OAK AV", "300 ELM DR"],
        },
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:26996",
    )


class TestMatchSites:
    def test_exact_id_match(self, parcels):
        sites = pd.DataFrame({"apn": ["11111111111"]})
        matches, records = match_sites(sites, parcels)
        assert matches == {0: 0}
        assert records[0]["matched"] is True
        assert records[0]["method"] == "exact match on ASRPARCEL"

    def test_address_fallback_when_apn_unmatched(self, parcels):
        sites = pd.DataFrame(
            {"apn": ["99999999999"], "address": ["200 Oak Avenue"]}
        )
        matches, records = match_sites(sites, parcels)
        assert matches == {0: 1}
        assert records[0]["method"] == "address (SITEADDR)"

    def test_not_found_when_neither_matches(self, parcels):
        sites = pd.DataFrame(
            {"apn": ["00000000000"], "address": ["999 Fake Street"]}
        )
        matches, records = match_sites(sites, parcels)
        assert matches == {}
        assert records[0]["matched"] is False

    def test_requires_apn_or_address_column(self, parcels):
        sites = pd.DataFrame({"foo": ["bar"]})
        with pytest.raises(ValueError):
            match_sites(sites, parcels)

    def test_unparseable_address_does_not_crash(self, parcels):
        # A stray header row read as data has no leading house number.
        sites = pd.DataFrame({"address": ["address", "100 Main Street"]})
        matches, records = match_sites(sites, parcels)
        assert records[0]["matched"] is False
        assert matches == {1: 0}
