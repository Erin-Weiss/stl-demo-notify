"""Interactive per-site notification map."""

from __future__ import annotations

import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import Search

from . import analysis

LEGEND_HTML = """
<div style="position: fixed; bottom: 30px; left: 10px; z-index: 9999;
            background: white; padding: 10px 14px; border: 1px solid #999;
            border-radius: 6px; font-size: 13px; line-height: 1.7;
            box-shadow: 0 1px 4px rgba(0,0,0,.3);">
  <b>Legend</b><br>
  <span style="display:inline-block;width:12px;height:12px;background:#cc0000;
        opacity:.85;border:1px solid #8b0000;margin-right:6px;"></span>Demolition site<br>
  <span style="display:inline-block;width:12px;height:12px;background:#cc0000;
        opacity:.15;border:1px solid #cc0000;margin-right:6px;"></span>Notification zone<br>
  <span style="display:inline-block;width:12px;height:12px;background:#1f6fb2;
        opacity:.45;border:1px solid #1f6fb2;margin-right:6px;"></span>Door hanger parcel<br>
  <span style="display:inline-block;width:12px;height:12px;background:#1f6fb2;
        opacity:.85;border:1px solid #1f6fb2;margin-right:6px;"></span>Darker blue = near multiple sites<br>
  <span style="display:inline-block;width:12px;height:12px;background:#e07b00;
        opacity:.6;border:1px solid #e07b00;margin-right:6px;"></span>Field review parcel
</div>
"""

CONTROLS_HTML = """
<div style="position: fixed; bottom: 30px; right: 10px; z-index: 9999;
            display: flex; flex-direction: column; gap: 6px;">
  <button onclick="setAllSites(true)"
          style="background: white; border: 1px solid #999; border-radius: 6px;
                 padding: 6px 12px; font-size: 13px; cursor: pointer;
                 box-shadow: 0 1px 4px rgba(0,0,0,.3);">All sites on</button>
  <button onclick="setAllSites(false)"
          style="background: white; border: 1px solid #999; border-radius: 6px;
                 padding: 6px 12px; font-size: 13px; cursor: pointer;
                 box-shadow: 0 1px 4px rgba(0,0,0,.3);">All sites off</button>
  <label style="background: white; border: 1px solid #999; border-radius: 6px;
                padding: 6px 12px; font-size: 13px; cursor: pointer;
                box-shadow: 0 1px 4px rgba(0,0,0,.3);
                display: flex; align-items: center; gap: 6px;">
    <input type="checkbox" checked onchange="setFieldReview(this.checked)">
    Field review parcels
  </label>
</div>
"""

BLUE_STYLE = {
    "color": "#1f6fb2",
    "weight": 1,
    "fillColor": "#1f6fb2",
    "fillOpacity": 0.30,
}
ORANGE_STYLE = {
    "color": "#e07b00",
    "weight": 1,
    "fillColor": "#e07b00",
    "fillOpacity": 0.55,
    "className": "field-review-path",
}


def _site_label_html(i: int, address: str) -> str:
    """Numbered badge with an address pill; JavaScript shows/hides the pill by zoom."""
    return (
        '<div style="display:flex;align-items:center;white-space:nowrap;'
        'transform:translate(-50%,-170%);">'
        '<span style="background:#8b0000;color:#fff;font-weight:700;'
        'font-size:11px;border-radius:50%;min-width:20px;height:20px;'
        "display:inline-flex;align-items:center;justify-content:center;"
        'border:1.5px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.5);">'
        f"{i}</span>"
        '<span class="site-addr" style="display:inline-flex;margin-left:4px;'
        "background:rgba(255,255,255,.88);color:#8b0000;font-weight:600;"
        "font-size:11px;padding:1px 7px;border-radius:9px;"
        f'border:1px solid #8b0000;">{address}</span>'
        "</div>"
    )


def _map_js(map_name: str, bounds: tuple[float, float, float, float]) -> str:
    miny, minx, maxy, maxx = bounds
    return f"""
    window.addEventListener('load', function () {{
        var MAP = window.{map_name};

        // Site number badges always show; full address labels appear at this
        // zoom level or closer. Lower to reveal addresses from farther out;
        // 13 = whole area, 15 = a few blocks, 17 = single block.
        var ADDRESS_ZOOM = 15;

        var HOME_BOUNDS = [[{miny}, {minx}], [{maxy}, {maxx}]];
        window.goHome = function () {{
            MAP.fitBounds(HOME_BOUNDS, {{padding: [30, 30]}});
        }};

        var HomeControl = L.Control.extend({{
            options: {{ position: 'topleft' }},
            onAdd: function () {{
                var bar = L.DomUtil.create('div', 'leaflet-bar');
                var a = L.DomUtil.create('a', '', bar);
                a.href = '#';
                a.title = 'Reset view to all sites';
                a.innerHTML = '&#8962;';
                a.style.fontSize = '16px';
                L.DomEvent.on(a, 'click', function (e) {{
                    L.DomEvent.stop(e);
                    window.goHome();
                }});
                return bar;
            }}
        }});
        MAP.addControl(new HomeControl());

        window._fieldReviewOn = true;
        function applyFieldReview() {{
            document.querySelectorAll('.field-review-path').forEach(function (el) {{
                el.style.display = window._fieldReviewOn ? '' : 'none';
            }});
        }}
        window.setFieldReview = function (on) {{
            window._fieldReviewOn = on;
            applyFieldReview();
        }};

        function refreshDynamicStyles() {{
            setTimeout(function () {{
                var show = MAP.getZoom() >= ADDRESS_ZOOM;
                document.querySelectorAll('.site-addr').forEach(function (el) {{
                    el.style.display = show ? 'inline-flex' : 'none';
                }});
                applyFieldReview();
            }}, 50);
        }}
        MAP.on('zoomend', refreshDynamicStyles);
        MAP.on('layeradd', refreshDynamicStyles);

        window.setAllSites = function (on) {{
            document.querySelectorAll(
                '.leaflet-control-layers-overlays input[type=checkbox]'
            ).forEach(function (cb) {{
                if (cb.checked !== on) {{ cb.click(); }}
            }});
        }};

        window.goHome();
        refreshDynamicStyles();
    }});
    """


def build_map(
    sites: pd.DataFrame,
    parcels_m: gpd.GeoDataFrame,
    matches: dict[int, int],
    buffers: dict[int, object],
    detail_kept: pd.DataFrame,
    excluded: pd.DataFrame,
) -> folium.Map:
    """Build the notification map: one togglable layer per site, address search,
    legend, and zoom-dependent labels."""
    matched_sites = [
        (i, site_idx) for i, site_idx in enumerate(sites.index, 1) if site_idx in matches
    ]
    simplified = parcels_m.geometry.simplify(0.5)

    demo_pts = gpd.GeoSeries(
        [parcels_m.geometry.loc[matches[site_idx]].centroid for _, site_idx in matched_sites],
        crs=parcels_m.crs,
    ).to_crs(4326)

    m = folium.Map(
        location=[demo_pts.y.mean(), demo_pts.x.mean()],
        zoom_start=14,
        tiles="CartoDB positron",
    )

    # Invisible layer that powers the address search; not a user-toggleable overlay.
    unique_kept = detail_kept.drop_duplicates("_row")
    search_gdf = gpd.GeoDataFrame(
        unique_kept[["SITEADDR"]].fillna("").astype(str),
        geometry=simplified.loc[unique_kept["_row"]].values,
        crs=parcels_m.crs,
    ).to_crs(4326)
    search_layer = folium.GeoJson(
        search_gdf,
        name="search-source",
        control=False,
        style_function=lambda f: {"opacity": 0, "fillOpacity": 0},
    )
    search_layer.add_to(m)

    near_sites_join = analysis.grouped_demo_addresses(detail_kept).apply("; ".join)
    kept_by_site = dict(tuple(detail_kept.groupby("site_index")))
    field_review_all = excluded[excluded["review_flag"] != ""]
    review_by_site = dict(tuple(field_review_all.groupby("site_index")))

    for (i, site_idx), pt in zip(matched_sites, demo_pts):
        demo_address = (
            str(sites.loc[site_idx, "address"]) if "address" in sites.columns else str(site_idx)
        )
        parcel_idx = matches[site_idx]
        fg = folium.FeatureGroup(name=f"Site {i:02d}: {demo_address}", show=True)

        zone = gpd.GeoSeries([buffers[site_idx]], crs=parcels_m.crs).to_crs(4326)
        folium.GeoJson(
            zone.__geo_interface__,
            style_function=lambda f: {
                "color": "#cc0000",
                "weight": 1,
                "fillColor": "#cc0000",
                "fillOpacity": 0.06,
            },
        ).add_to(fg)

        site_kept = kept_by_site.get(site_idx)
        if site_kept is not None and len(site_kept):
            site_kept = site_kept.drop_duplicates("neighbor_handle")
            kept_gdf = gpd.GeoDataFrame(
                {
                    "SITEADDR": site_kept["SITEADDR"].fillna("").astype(str),
                    "suggested_hangers": site_kept["suggested_hangers"].values,
                    "near_demo_sites": site_kept["neighbor_handle"]
                    .map(near_sites_join)
                    .fillna("")
                    .astype(str)
                    .values,
                    "LANDUSE_DESC": (
                        site_kept["LANDUSE_DESC"].fillna("").astype(str).values
                        if "LANDUSE_DESC" in site_kept.columns
                        else [""] * len(site_kept)
                    ),
                },
                geometry=simplified.loc[site_kept["_row"]].values,
                crs=parcels_m.crs,
            ).to_crs(4326)
            folium.GeoJson(
                kept_gdf,
                style_function=lambda f, s=BLUE_STYLE: s,
                tooltip=folium.GeoJsonTooltip(
                    fields=["SITEADDR", "suggested_hangers", "near_demo_sites", "LANDUSE_DESC"],
                    aliases=["Address", "Hangers", "Near demo sites", "Use"],
                ),
            ).add_to(fg)

        site_review = review_by_site.get(site_idx)
        if site_review is not None and len(site_review):
            site_review = site_review.drop_duplicates("neighbor_handle")
            review_gdf = gpd.GeoDataFrame(
                site_review[["SITEADDR", "exclusion_reason", "review_flag"]]
                .fillna("")
                .astype(str),
                geometry=simplified.loc[site_review["_row"]].values,
                crs=parcels_m.crs,
            ).to_crs(4326)
            folium.GeoJson(
                review_gdf,
                style_function=lambda f, s=ORANGE_STYLE: s,
                tooltip=folium.GeoJsonTooltip(
                    fields=["SITEADDR", "exclusion_reason", "review_flag"],
                    aliases=["Address", "Why excluded", "Field review reason"],
                ),
            ).add_to(fg)

        site_poly = gpd.GeoSeries(
            [parcels_m.geometry.loc[parcel_idx]], crs=parcels_m.crs
        ).to_crs(4326)
        folium.GeoJson(
            site_poly.__geo_interface__,
            style_function=lambda f: {
                "color": "#8b0000",
                "weight": 2,
                "fillColor": "#cc0000",
                "fillOpacity": 0.85,
            },
            tooltip=f"Demo site {i:02d}: {demo_address}",
        ).add_to(fg)

        folium.Marker(
            location=[pt.y, pt.x],
            icon=folium.DivIcon(html=_site_label_html(i, demo_address), icon_size=(0, 0)),
        ).add_to(fg)

        fg.add_to(m)

    Search(
        layer=search_layer,
        search_label="SITEADDR",
        collapsed=True,
        placeholder="Search an address...",
        position="topleft",
    ).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    m.get_root().html.add_child(folium.Element(LEGEND_HTML))
    m.get_root().html.add_child(folium.Element(CONTROLS_HTML))

    zones_all = gpd.GeoSeries(list(buffers.values()), crs=parcels_m.crs).to_crs(4326)
    minx, miny, maxx, maxy = zones_all.total_bounds
    # Injected alongside folium's generated code, which defines the map variable
    # after this script element appears in the page; everything must therefore
    # run inside a window 'load' listener, or referencing the map before load
    # renders a blank page.
    m.get_root().script.add_child(
        folium.Element(_map_js(m.get_name(), (miny, minx, maxy, maxx)))
    )

    return m
