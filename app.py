"""Streamlit interface for stl-demo-notify.

A thin wrapper over the package; analysis logic lives there, not here.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from stl_demo_notify import (
    analysis,
    citydata,
    config,
    mapping,
    matching,
    outputs,
    siteinput,
)

st.set_page_config(
    page_title="Demolition Neighbor Notification",
    layout="wide",
    initial_sidebar_state="expanded",
)

_APP_DIR = Path(__file__).parent
_HERO_IMAGE = _APP_DIR / "docs" / "images" / "map-overview.jpg"
_CASE_STUDY_URL = "https://erin-weiss.github.io/stl-demo-notify/"
_REPO_URL = "https://github.com/Erin-Weiss/stl-demo-notify"

_GROUP_DEPENDENT_FILES = [
    "doorhanger_list.csv",
    "doorhanger_list.xlsx",
    "field_review_list.csv",
    "assumptions_log.txt",
]


@st.cache_resource(show_spinner="Loading city parcel data...")
def load_parcels() -> tuple[gpd.GeoDataFrame, dict[str, str]]:
    """Load and reproject the parcel cache once, shared across reruns and sessions."""
    parcels = gpd.read_parquet(config.PARCEL_CACHE_PATH)
    parcels_m = parcels.to_crs(epsg=config.CRS_EPSG)
    return parcels_m, citydata.load_landuse_lookup()


def _secret(name: str) -> str | None:
    try:
        return st.secrets[name]
    except Exception:
        return None


def _column_label(col: object) -> str:
    """'Column A' for an unnamed (headerless) column, else the real header text."""
    if isinstance(col, int) and col < 26:
        return f"Column {chr(65 + col)}"
    return str(col)


def _resolve_sites(
    raw_bytes: bytes, filename: str, no_header: bool
) -> pd.DataFrame | None:
    """Parse uploaded bytes into standardized sites, asking for columns if needed.

    Reads from a fresh buffer each call so re-reads across reruns are safe.
    """
    header = None if no_header else "infer"
    raw = siteinput.read_site_table(
        io.BytesIO(raw_bytes), filename=filename, header=header
    )
    if no_header and len(raw) and siteinput.looks_like_header_row(list(raw.iloc[0])):
        st.sidebar.warning(
            "The first row looks like a header. If it is, uncheck 'File has no "
            "header row'."
        )
    try:
        return siteinput.standardize_columns(raw)
    except ValueError:
        st.sidebar.warning("Could not detect the columns. Choose them below.")
        by_label = {_column_label(c): c for c in raw.columns}
        options = ["(none)", *by_label]
        apn_pick = st.sidebar.selectbox("Parcel number column", options, index=0)
        addr_pick = st.sidebar.selectbox("Address column", options, index=0)
        apn = by_label.get(apn_pick)
        addr = by_label.get(addr_pick)
        if apn is None and addr is None:
            return None
        return siteinput.standardize_columns(raw, apn, addr)


def _load_input(
    upload, use_sample: bool, no_header: bool
) -> tuple[pd.DataFrame | None, str | None]:
    """Return (sites, input_id). input_id changes when the source file changes."""
    if upload is not None:
        raw_bytes = upload.getvalue()
        input_id = f"upload:{upload.name}:{len(raw_bytes)}:{no_header}"
        return _resolve_sites(raw_bytes, upload.name, no_header), input_id
    if use_sample:
        return siteinput.load_sites(config.DATA_DIR / "sample_input.csv"), "sample"
    return None, None


def _run_analysis(sites: pd.DataFrame, buffer_feet: float) -> dict:
    """Run the spatial pipeline once and return the group-independent results."""
    parcels_m, lookup = load_parcels()
    matches, records = matching.match_sites(sites, parcels_m)
    if not matches:
        st.error("No sites matched a city parcel. Check the input file.")
        st.stop()

    detail, buffers = analysis.find_neighbors(
        sites, parcels_m, matches, buffer_feet=buffer_feet
    )
    detail = analysis.label_landuse(detail, lookup)
    detail["suggested_hangers"] = analysis.suggested_hangers(detail["NUMUNITS"])
    kept, excluded, field_review = analysis.apply_structure_filter(detail)

    site_map = mapping.build_map(sites, parcels_m, matches, buffers, kept, excluded)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        outputs.write_site_checklists(
            tmp / "site_checklists.xlsx", sites, matches, kept, excluded, parcels_m
        )
        outputs.write_match_report(tmp / "match_report.txt", records)
        checklist_bytes = (tmp / "site_checklists.xlsx").read_bytes()
        match_report_text = (tmp / "match_report.txt").read_text()

    return {
        "kept": kept,
        "excluded": excluded,
        "field_review": field_review,
        "sites": sites,
        "matches": matches,
        "buffer_feet": buffer_feet,
        "map_html": site_map.get_root().render(),
        "checklist_bytes": checklist_bytes,
        "match_report_text": match_report_text,
    }


def _site_labels(sites: pd.DataFrame) -> dict[int, str]:
    """Map each site index to a '1. address' label using its badge number."""
    labels = {}
    for badge, site_index in enumerate(sites.index, 1):
        if "address" in sites.columns:
            label = str(sites.loc[site_index, "address"])
        elif "apn" in sites.columns:
            label = str(sites.loc[site_index, "apn"])
        else:
            label = f"site {badge}"
        labels[site_index] = f"{badge}. {label}"
    return labels


def _grouping_controls(sites: pd.DataFrame) -> tuple[dict | None, str | None]:
    """Render the add-a-group UI and return the {site_index: group_id} map and note."""
    labels = _site_labels(sites)
    st.session_state.setdefault("groups_list", [])
    st.session_state.setdefault("picker_version", 0)

    st.caption(
        "Group sites demolished together so an address near several of them "
        "is counted once per group. Changes only the separate-events total."
    )
    claimed = {si for group in st.session_state["groups_list"] for si in group}
    available = [si for si in labels if si not in claimed]
    picked = st.multiselect(
        "Pick two or more sites, then Add group",
        options=available,
        format_func=lambda si: labels[si],
        key=f"group_picker_{st.session_state['picker_version']}",
    )
    left, right = st.columns(2)
    if left.button("Add group", disabled=len(picked) < 2, width="stretch"):
        st.session_state["groups_list"].append(list(picked))
        st.session_state["picker_version"] += 1
        st.rerun()
    if right.button(
        "Clear groups",
        disabled=not st.session_state["groups_list"],
        width="stretch",
    ):
        st.session_state["groups_list"] = []
        st.session_state["picker_version"] += 1
        st.rerun()

    for i, group in enumerate(st.session_state["groups_list"], 1):
        st.markdown(f"**Group {i}:** " + ", ".join(labels[si] for si in group))

    groups: dict[int, int] = {}
    notes = []
    for group_id, group in enumerate(st.session_state["groups_list"]):
        notes.append("+".join(str(sites.index.get_loc(si) + 1) for si in group))
        for si in group:
            groups[si] = group_id
    return (groups or None), ("; ".join(notes) or None)


def _group_dependent_bytes(results, dedup, single_pass, separate, note) -> dict:
    """Regenerate the deliverables whose contents depend on the schedule groups."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        outputs.write_doorhanger_outputs(
            tmp, dedup, results["kept"], results["field_review"], results["excluded"]
        )
        outputs.write_assumptions_log(
            tmp / "assumptions_log.txt",
            buffer_feet=results["buffer_feet"],
            structure_filter_method=analysis.STRUCTURE_FILTER_METHOD,
            matched_count=len(results["matches"]),
            total_sites=len(results["sites"]),
            unique_addresses=len(dedup),
            total_single_pass=single_pass,
            total_separate_events=separate,
            field_review_count=len(results["field_review"]),
            schedule_groups=note,
        )
        return {name: (tmp / name).read_bytes() for name in _GROUP_DEPENDENT_FILES}


def _all_deliverables(results, group_files) -> dict[str, bytes]:
    return {
        **group_files,
        "site_checklists.xlsx": results["checklist_bytes"],
        "match_report.txt": results["match_report_text"].encode(),
        "demo_notification_map.html": results["map_html"].encode(),
    }


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _getting_started() -> None:
    if _HERO_IMAGE.exists():
        st.image(
            str(_HERO_IMAGE),
            caption="Example run: 30 demolition sites with 500 ft notification "
            "zones across north St. Louis.",
            width="stretch",
        )
    st.markdown(
        """
        <div style="padding:16px 20px;border-radius:10px;background:#f4f7fa;
                    border-left:5px solid #1d3557;margin:8px 0 18px;">
          <h3 style="margin:0 0 6px;color:#1d3557;">Getting started</h3>
          <p style="margin:0;font-size:15px;">
            Upload a demolition site list, or use the bundled sample, set the
            buffer distance, and click Run analysis. Optional schedule grouping
            is step 3 in the sidebar.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "1. Upload a **CSV or Excel** list of demolition sites in the sidebar, "
        "or check **Use the bundled sample**.\n"
        "2. Set the **buffer distance** (default 500 ft).\n"
        "3. Optionally **group** sites that will be demolished together.\n"
        "4. Click **Run analysis**."
    )
    st.markdown("**Expected input**")
    st.caption(
        "Your file needs a column of street addresses, a column of parcel "
        "numbers, or both; either one on its own is enough. A parcel number is "
        "the city's 11-digit parcel identifier, like the 56389420000 in the "
        "example below, and these match most reliably because they are checked "
        "against several of the city's parcel ID fields. Addresses are matched "
        "against the city's address records. Common column names are recognized "
        "automatically, and if none are recognized the app asks you to choose "
        "the columns. A header row is recommended; if your file has none, check "
        "'File has no header row' in step 1."
    )
    st.dataframe(
        pd.DataFrame(
            {
                "apn": ["56389420000", "36199060000"],
                "address": ["1825 Cora Avenue", "3010 N Newstead Avenue"],
            }
        ),
        hide_index=True,
        width="content",
    )


def _about_expander() -> None:
    with st.expander("About"):
        st.markdown(
            "This tool identifies every occupied structure within a buffer "
            "distance of a demolition site and produces the door hanger lists, "
            "walking checklists, and interactive map a crew needs to notify "
            "neighbors. Built for a client and generalized to work across the "
            "City of St. Louis.\n\n"
            f"[Case study]({_CASE_STUDY_URL}) · [Source]({_REPO_URL})"
        )


def _footer() -> None:
    st.divider()
    st.caption(
        f"Built by Erin Weiss · [Case study]({_CASE_STUDY_URL}) · "
        f"[Source]({_REPO_URL})"
    )


def _render_results(groups, note) -> None:
    results = st.session_state["results"]
    sites = results["sites"]
    dedup, single_pass, separate = analysis.dedupe_and_totals(
        results["kept"], groups=groups
    )

    with st.container(border=True):
        metrics = st.columns(5)
        metrics[0].metric("Sites matched", f"{len(results['matches'])} / {len(sites)}")
        metrics[1].metric("Notification addresses", len(dedup))
        metrics[2].metric("Hangers, single pass", single_pass)
        metrics[3].metric("Hangers, separate events", separate)
        metrics[4].metric("Field review parcels", len(results["field_review"]))

    st.markdown("[Jump to downloads](#downloads)")

    st.subheader("Notification map")
    components.html(results["map_html"], height=620)

    st.subheader("Door hanger list (first 50 rows)")
    st.dataframe(
        dedup.drop(columns=["_row"]).head(50), width="stretch", hide_index=True
    )

    field_review = results["field_review"]
    st.subheader(f"Field review parcels ({len(field_review)})")
    st.caption(
        "Parcels the assessor records as having no building, but whose vacancy "
        "or improvement fields disagree. A crew verifies these in person."
    )
    review_cols = [
        c
        for c in ["SITEADDR", "review_flag", "near_demo_sites", "distance_ft"]
        if c in field_review.columns
    ]
    st.dataframe(
        field_review[review_cols].head(50) if review_cols else field_review,
        width="stretch",
        hide_index=True,
    )

    group_files = _group_dependent_bytes(results, dedup, single_pass, separate, note)
    with st.expander("Methodology and assumptions"):
        st.text(group_files["assumptions_log.txt"].decode())

    st.subheader("Downloads")
    everything = _all_deliverables(results, group_files)
    stamp = date.today().isoformat()
    st.download_button(
        "Download all (zip)",
        _zip_bytes(everything),
        f"demo_notification_outputs_{stamp}.zip",
        mime="application/zip",
        type="primary",
    )
    names = list(everything)
    for start in range(0, len(names), 4):
        columns = st.columns(4)
        for column, name in zip(columns, names[start : start + 4], strict=False):
            mime = "text/html" if name.endswith(".html") else None
            stem, _, ext = name.rpartition(".")
            saved_name = f"{stem}_{stamp}.{ext}"
            column.download_button(
                name, everything[name], saved_name, mime=mime, width="stretch"
            )


def _refresh_panel() -> None:
    with st.sidebar.expander("Refresh parcel data (password required)"):
        secret = _secret("refresh_password")
        if not secret:
            st.caption(
                "Not configured. Set a 'refresh_password' secret to enable this."
            )
            return
        password = st.text_input("Password", type="password")
        if not password:
            return
        if password != secret:
            st.error("Incorrect password.")
            return
        st.caption(
            "Re-download the city data and rebuild the cache. On the deployed "
            "app this lasts until the container restarts; the committed cache "
            "is the permanent copy."
        )
        if st.button("Refresh parcel data"):
            with st.spinner("Downloading fresh city data..."):
                citydata.fetch_landuse_vocabulary(force=True)
                citydata.build_parcel_cache(force=True)
            load_parcels.clear()
            st.success("Parcel data refreshed. Run the analysis again to use it.")


def main() -> None:
    st.title("Demolition Neighbor Notification")
    st.write(
        "Before a building comes down, the occupants of every nearby structure "
        "need notice. Give this tool a list of demolition sites in the City of "
        "St. Louis and it finds every occupied parcel within the buffer "
        "distance, then produces the door hanger lists, per-site walking "
        "checklists, and an interactive map a crew needs to do the notifying. "
        "Each run also documents how every site was matched and the assumptions "
        "behind the results."
    )

    st.sidebar.title("Steps")
    st.sidebar.markdown("**1 · Site list**")
    upload = st.sidebar.file_uploader(
        "CSV or Excel (one file)", type=["csv", "xlsx", "xls"]
    )
    with st.sidebar.expander("Accepted format"):
        st.caption(
            "A column of street addresses, a column of parcel numbers, or "
            "both; either one alone works. A parcel number is the city's "
            "11-digit parcel identifier, like 56389420000, and matches most "
            "reliably because it is checked against several of the city's "
            "parcel ID fields. St. Louis situs addresses."
        )
    no_header = st.sidebar.checkbox("File has no header row", value=False)
    use_sample = st.sidebar.checkbox(
        "Use the bundled 5-site sample", value=upload is None
    )

    st.sidebar.markdown("**2 · Buffer distance**")
    buffer_feet = float(
        st.sidebar.slider(
            "Feet from each site", min_value=100, max_value=1000, value=500, step=50
        )
    )

    sites, input_id = _load_input(upload, use_sample, no_header)
    if sites is None:
        with st.sidebar:
            _about_expander()
            _refresh_panel()
        _getting_started()
        _footer()
        return

    # A new source file starts fresh: drop stale results and any groups.
    if st.session_state.get("input_id") != input_id:
        st.session_state["input_id"] = input_id
        st.session_state.pop("results", None)
        st.session_state["groups_list"] = []
        st.session_state["picker_version"] = (
            st.session_state.get("picker_version", 0) + 1
        )

    # Changing the buffer invalidates the spatial run until re-run.
    run_sig = (input_id, buffer_feet)
    if st.session_state.get("run_sig") != run_sig:
        st.session_state.pop("results", None)

    with st.sidebar:
        st.markdown("**3 · Schedule grouping (optional)**")
        groups, note = _grouping_controls(sites)
        st.markdown("**4 · Run**")
        run_clicked = st.button("Run analysis", type="primary", width="stretch")
        st.caption(
            "Changing the site list or buffer clears the results; click Run to "
            "update. Schedule grouping updates live without re-running."
        )
        _about_expander()
        _refresh_panel()

    if run_clicked:
        with st.spinner("Running analysis..."):
            st.session_state["results"] = _run_analysis(sites, buffer_feet)
        st.session_state["run_sig"] = run_sig

    if "results" in st.session_state:
        _render_results(groups, note)
    else:
        _getting_started()
    _footer()


main()
