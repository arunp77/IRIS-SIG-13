"""Quick look at the loaded points, the way a GIS or EO analyst would check them.

Writes, to --out (default out/):
  signal_ingest.geojson       the points as GeoJSON in EPSG:4326 (WGS84, as RFC 7946 requires), for QGIS
  signal_ingest_25832.csv     the same rows with easting/northing in EPSG:25832 (metres)
and a quick-look map (--png, default out/quicklook.png) in EPSG:25832 on an outline of Mecklenburg-Vorpommern.
(docs/figures/quicklook.png is the map of the full run; to refresh it: --png docs/figures/quicklook.png.)

It also checks every point against the state outline (data/reference/mv_outline.geojson, a simplified
Natural Earth boundary, public domain) with a 3 km tolerance for the simplified coast.

    python qa/quicklook.py
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import numpy as np
import psycopg
from dotenv import load_dotenv
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from pyproj import Transformer
from shapely import transform as shapely_transform
from shapely.geometry import Point, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
TOLERANCE_M = 3000
STAGES = {
    "In Kraft getreten": "in force",
    "Wirksamkeit": "in force",
    "Ausser Kraft": "ended",
    "Aufgehoben": "ended",
    "Untergegangen": "ended",
    "Teilweise Untergegangen": "ended",
}  # everything else: in procedure
COLOURS = {"in force": "#0072B2", "in procedure": "#E69F00", "ended": "#999999"}
COLUMNS = [
    "source_id",
    "signal_type",
    "title",
    "legal_status",
    "plan_type",
    "municipality",
    "district",
    "published_at",
    "source_url",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--png", type=Path, default=ROOT / "out" / "quicklook.png")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)}, ST_X(geom), ST_Y(geom) FROM iris_staging.signal_ingest "
            "WHERE geom IS NOT NULL ORDER BY source_id"
        ).fetchall()
    if not rows:
        sys.exit("no points in the table: run the harvester first")

    to_wgs84 = Transformer.from_crs(25832, 4326, always_xy=True)
    args.out.mkdir(parents=True, exist_ok=True)
    features, csv_rows = [], []
    for *values, x, y in rows:
        props = dict(zip(COLUMNS, [str(v) if v is not None else None for v in values]))
        lon, lat = to_wgs84.transform(x, y)
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            }
        )
        csv_rows.append({**props, "easting_25832": x, "northing_25832": y})
    (args.out / "signal_ingest.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8"
    )
    with (args.out / "signal_ingest_25832.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS + ["easting_25832", "northing_25832"])
        writer.writeheader()
        writer.writerows(csv_rows)

    # outline: Natural Earth lon/lat -> EPSG:25832, then compare in metres
    to_utm = Transformer.from_crs(4326, 25832, always_xy=True)
    outline = shape(
        json.loads((ROOT / "data" / "reference" / "mv_outline.geojson").read_text(encoding="utf-8"))["features"][0][
            "geometry"
        ]
    )
    outline = shapely_transform(outline, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))
    zone = unary_union(outline).buffer(TOLERANCE_M)
    outside = [r for r in rows if not zone.contains(Point(r[-2], r[-1]))]
    strictly = sum(unary_union(outline).contains(Point(r[-2], r[-1])) for r in rows)
    print(
        f"{len(rows)} points: {strictly} inside the outline, "
        f"{len(rows) - strictly - len(outside)} within {TOLERANCE_M // 1000} km of it, {len(outside)} further out"
    )
    for r in outside[:5]:
        print("  outside:", r[2][:70])

    draw(args.png, outline, rows)
    print(f"wrote {args.out}/signal_ingest.geojson, signal_ingest_25832.csv and {args.png}")


def draw(png, outline, rows):
    fig = Figure(figsize=(8, 7), dpi=130)
    ax = fig.add_subplot()
    for part in getattr(outline, "geoms", [outline]):
        ring = list(part.exterior.coords)
        ax.add_patch(
            PathPatch(
                MplPath(ring, [MplPath.MOVETO] + [MplPath.LINETO] * (len(ring) - 2) + [MplPath.CLOSEPOLY]),
                facecolor="#f4f1ea",
                edgecolor="#777777",
                linewidth=0.8,
            )
        )
    for name, colour in COLOURS.items():
        pts = [(r[-2], r[-1]) for r in rows if STAGES.get(r[3], "in procedure") == name]
        if pts:
            ax.scatter(
                *zip(*pts), s=14, c=colour, edgecolors="white", linewidths=0.3, label=f"{name} ({len(pts)})", alpha=0.85
            )
    ax.set_aspect("equal")
    ax.autoscale()
    ax.set_xlabel("Easting, EPSG:25832 (m)")
    ax.set_ylabel("Northing, EPSG:25832 (m)")
    ax.set_title(f"Loaded planning records (n={len(rows)})", fontsize=11)
    ax.legend(title="Stage of the plan", loc="lower right", frameon=False)
    fig.text(
        0.01,
        0.01,
        "Centroids of the plan areas. Plans: gaia-mv.de, bauportal-mv.de. Outline: Natural Earth (public domain).",
        fontsize=7,
    )
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, bbox_inches="tight")


if __name__ == "__main__":
    main()
