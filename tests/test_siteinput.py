"""Tests for stl_demo_notify.siteinput column detection."""

import pandas as pd
import pytest

from stl_demo_notify.siteinput import (
    load_sites,
    looks_like_header_row,
    looks_like_parcel_id,
    standardize_columns,
)


class TestParcelIdDetection:
    def test_eleven_digit_number_is_parcel_id(self):
        assert looks_like_parcel_id("56389420000")

    def test_leading_zero_parcel_id(self):
        assert looks_like_parcel_id("00019005000")

    def test_five_digit_zip_is_not_parcel_id(self):
        assert not looks_like_parcel_id("63113")

    def test_address_text_is_not_parcel_id(self):
        assert not looks_like_parcel_id("1825 Cora Avenue")


class TestStandardizeColumns:
    def test_named_columns_detected(self):
        raw = pd.DataFrame({"apn": ["56389420000"], "address": ["1825 Cora Ave"]})
        sites = standardize_columns(raw)
        assert list(sites.columns) == ["apn", "address"]

    def test_headerless_detects_both_parcel_and_address(self):
        # Columns 0 and 1 with no names, as a headerless read produces.
        raw = pd.DataFrame(
            {
                0: ["56389420000", "36199060000"],
                1: ["1825 Cora Avenue", "3010 N Newstead Avenue"],
            }
        )
        sites = standardize_columns(raw)
        assert "apn" in sites.columns
        assert "address" in sites.columns
        assert sites["apn"].iloc[0] == "56389420000"

    def test_unrecognized_parcel_header_detected_by_content(self):
        raw = pd.DataFrame({"Locator Number": ["56389420000", "36199060000"]})
        sites = standardize_columns(raw)
        assert "apn" in sites.columns

    def test_raises_when_no_usable_column(self):
        raw = pd.DataFrame({"note": ["hello", "world"]})
        with pytest.raises(ValueError):
            standardize_columns(raw)


class TestLoadSites:
    def test_recovers_headerless_file(self, tmp_path):
        # Without recovery, pandas would consume the first row as the header.
        path = tmp_path / "sites.csv"
        path.write_text(
            "56389420000,1825 Cora Avenue\n36199060000,3010 N Newstead Avenue\n"
        )
        sites = load_sites(path)
        assert len(sites) == 2
        assert "apn" in sites.columns
        assert sites["apn"].iloc[0] == 56389420000

    def test_keeps_normal_header(self, tmp_path):
        path = tmp_path / "sites.csv"
        path.write_text("apn,address\n56389420000,1825 Cora Avenue\n")
        sites = load_sites(path)
        assert len(sites) == 1
        assert list(sites.columns) == ["apn", "address"]


class TestHeaderRowDetection:
    def test_apn_address_row_looks_like_header(self):
        assert looks_like_header_row(["apn", "address"])

    def test_parcel_number_row_looks_like_header(self):
        assert looks_like_header_row(["Parcel Number", "Site Address"])

    def test_real_data_row_does_not_look_like_header(self):
        assert not looks_like_header_row(["56389420000", "1825 Cora Avenue"])
