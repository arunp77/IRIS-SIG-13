"""Data side of the dashboard: read the staging table and prepare the frames the charts need.

Nothing in here imports Streamlit, so it can be tested on its own (tests/test_ui.py).
"""

import json
import os
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SIGNALS = ["grid_landing", "peatland_restoration", "solar_acreage"]

# The portal's own wording for the stage of a plan, grouped into three. Anything not listed is "In procedure".
IN_FORCE = {"In Kraft getreten", "Wirksamkeit"}
ENDED = {"Ausser Kraft", "Untergegangen", "Teilweise Untergegangen", "Aufgehoben"}
STAGES = ["In force", "In procedure", "Ended"]
STAGE_COLOURS = {"In force": [46, 160, 90], "In procedure": [235, 150, 30], "Ended": [130, 130, 145]}

# Words that make a plan "unmatched": the classifier only accepts them together with a second word.
WEAK_WORDS = ["Moor", "Umspannwerk", "Offshore"]

# Coordinates are stored in EPSG:25832 (metres); the map needs longitude and latitude.
QUERY = """
SELECT source_id, signal_type, title, source_url, document_urls, published_at, legal_status, plan_type,
       municipality, district, location_status,
       ST_X(geom) AS easting, ST_Y(geom) AS northing,
       ST_X(ST_Transform(geom, 4326)) AS lon, ST_Y(ST_Transform(geom, 4326)) AS lat,
       raw_payload ->> 'published_at_basis' AS date_basis, last_seen_at
FROM iris_staging.signal_ingest
ORDER BY title
"""


def database_url():
    """DATABASE_URL from the environment, or from .env."""
    load_dotenv(ROOT / ".env")
    return os.environ.get("DATABASE_URL")


def stage_of(legal_status):
    if legal_status in IN_FORCE:
        return "In force"
    if legal_status in ENDED:
        return "Ended"
    return "In procedure"


def load_plans(url):
    """All rows of the staging table as a DataFrame (see prepare)."""
    with psycopg.connect(url) as conn:
        cursor = conn.execute(QUERY)
        columns = [c.name for c in cursor.description]
        return prepare(pd.DataFrame(cursor.fetchall(), columns=columns))


def prepare(frame):
    """Add the columns the dashboard works with: stage, colour, number of documents, year, short date basis."""
    frame = frame.copy()
    frame["stage"] = frame["legal_status"].map(stage_of)
    frame["colour"] = frame["stage"].map(STAGE_COLOURS)
    frame["documents"] = frame["document_urls"].map(len)
    published = pd.to_datetime(frame["published_at"], utc=True).dt.tz_convert("Europe/Berlin")
    frame["year"] = published.dt.year  # Berlin time: the stand-in dates are midnight there
    frame["date_basis"] = frame["date_basis"].fillna("none (page has no date)")
    frame["date_basis"] = frame["date_basis"].where(~frame["date_basis"].str.startswith("download"), "download title")
    return frame


def filter_plans(frame, text="", districts=(), stages=STAGES):
    """Rows whose title contains `text`, in the chosen districts (none chosen: all) and stages."""
    keep = frame["stage"].isin(stages)
    if text.strip():
        keep &= frame["title"].str.contains(text.strip(), case=False, regex=False)
    if districts:
        keep &= frame["district"].isin(districts)
    return frame[keep]


def missing_fields(frame):
    """How many of the shown plans lack each optional field."""
    counts = {
        "publication date": int(frame["year"].isna().sum()),
        "documents": int((frame["documents"] == 0).sum()),
        "district": int(frame["district"].isna().sum()),
        "location": int((frame["location_status"] != "resolved").sum()),
    }
    return pd.DataFrame({"field": list(counts), "plans": list(counts.values())})


def signal_counts(frame):
    """Plans per configured signal type, with zeros for the types the portal had nothing for."""
    counts = frame["signal_type"].value_counts().reindex(SIGNALS, fill_value=0)
    return pd.DataFrame({"signal": counts.index, "plans": counts.to_numpy()})


def load_report(folder=ROOT):
    """The report of the last full run (out/full_run.json), else the committed sample. Returns (report, where)."""
    for path in (folder / "out" / "full_run.json", folder / "data" / "sample" / "run_report.json"):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), str(path.relative_to(folder))
    return None, None


def unmatched_by_word(report):
    """Plans the search found but the classifier left out, grouped by the weak word in the title."""
    rows = []
    for record in report["unmatched"]:
        word = next((w for w in WEAK_WORDS if w.lower() in record["title"].lower()), "other")
        rows.append({"word": word, "title": " ".join(record["title"].split()), "id": record["id"]})
    return pd.DataFrame(rows, columns=["word", "title", "id"])
