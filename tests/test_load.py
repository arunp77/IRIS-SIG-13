"""Loading into PostGIS, and loading again (needs the database, marker `db`)."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.rows import dict_row

from harvester.load import upsert
from harvester.source import parse_detail
from harvester.transform import normalise
from tests.helpers import fixture_detail_html, record_by_id

pytestmark = pytest.mark.db

PLAN = "5b611dce-4bd4-11ea-afa7-87b6b3c7cd7c"
RUN1 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
RUN2 = RUN1 + timedelta(days=1)

ROW = """SELECT title, location_status, ST_AsText(geom) AS wkt, ST_SRID(geom) AS srid, first_seen_at, last_seen_at,
                updated_at, signal_type, matched_terms, document_urls, raw_payload
         FROM iris_staging.signal_ingest WHERE source_id = %s"""


def make_row(**record_changes):
    record = deepcopy(record_by_id(PLAN))
    record.update(record_changes)
    candidate = {"record": record, "found_by": [["solar_acreage", "Solarpark"]]}
    row, _, _ = normalise(candidate, parse_detail(fixture_detail_html(PLAN)), "solar_acreage", ["Solar"])
    return row


def fetch(conn, plan_id=PLAN):
    cursor = conn.cursor(row_factory=dict_row)
    return cursor.execute(ROW, (plan_id,)).fetchone()


def count(conn):
    return conn.execute("SELECT count(*) FROM iris_staging.signal_ingest").fetchone()[0]


def test_a_new_row_is_inserted_with_a_point_in_epsg_25832(conn):
    stats = upsert(conn, [make_row()], RUN1)
    assert (stats["inserted"], stats["updated"], stats["unchanged"], stats["failed"]) == (1, 0, 0, [])
    row = fetch(conn)
    assert row["location_status"] == "resolved" and row["wkt"].startswith("POINT(") and row["srid"] == 25832
    assert row["first_seen_at"] == row["last_seen_at"] == row["updated_at"] == RUN1
    assert row["signal_type"] == "solar_acreage" and row["matched_terms"] == ["Solar"]
    assert len(row["document_urls"]) >= 6 and row["raw_payload"]["search_record"]["x_gmlid"] == [PLAN]


def test_a_second_run_adds_no_row_and_only_moves_last_seen(conn):
    upsert(conn, [make_row()], RUN1)
    stats = upsert(conn, [make_row()], RUN2)  # built again, like a real second run
    assert (stats["inserted"], stats["updated"], stats["unchanged"]) == (0, 0, 1)
    row = fetch(conn)
    assert (
        count(conn) == 1 and row["first_seen_at"] == RUN1 and row["updated_at"] == RUN1 and row["last_seen_at"] == RUN2
    )


def test_a_changed_plan_is_updated_in_place(conn):
    upsert(conn, [make_row()], RUN1)
    stats = upsert(conn, [make_row(title=["BPlan Nr. 23 Friedland, geändert"])], RUN2)
    assert (stats["inserted"], stats["updated"], stats["unchanged"]) == (0, 1, 0)
    row = fetch(conn)
    assert count(conn) == 1 and row["title"].endswith("geändert")
    assert row["first_seen_at"] == RUN1 and row["updated_at"] == row["last_seen_at"] == RUN2


def test_a_reindex_by_the_portal_is_not_a_change(conn):
    upsert(conn, [make_row()], RUN1)
    stats = upsert(conn, [make_row(_resourceIndexed="2031-01-01T00:00:00+00:00")], RUN2)
    assert stats["unchanged"] == 1 and stats["updated"] == 0


def test_a_row_without_location_is_stored_without_a_point(conn):
    record = deepcopy(record_by_id(PLAN))
    record.pop("x_centroid")
    row, _, _ = normalise(
        {"record": record, "found_by": []}, parse_detail(fixture_detail_html(PLAN)), "solar_acreage", []
    )
    upsert(conn, [row], RUN1)
    row = fetch(conn)
    assert row["location_status"] == "unresolved" and row["wkt"] is None


def test_the_same_plan_twice_in_one_batch_is_loaded_once(conn):
    stats = upsert(conn, [make_row(), make_row()], RUN1)
    assert stats["inserted"] == 1 and count(conn) == 1


def test_a_refused_row_is_listed_and_does_not_stop_the_others(conn):
    bad = {**make_row(), "signal_type": "wind", "source_id": "bad"}  # not an allowed signal type
    stats = upsert(conn, [bad, make_row()], RUN1)
    assert stats["inserted"] == 1 and count(conn) == 1
    assert [f["source_id"] for f in stats["failed"]] == ["bad"] and "signal_type" in stats["failed"][0]["error"]


def test_the_database_itself_refuses_a_point_without_resolved_status(conn):
    with pytest.raises(Exception, match="signal_ingest"):
        conn.execute(
            """INSERT INTO iris_staging.signal_ingest
                   (source, source_id, signal_type, title, location_status, content_hash, raw_payload, geom)
               VALUES ('t', 't', 'solar_acreage', 't', 'unresolved', 'h', '{}',
                       ST_SetSRID(ST_MakePoint(650000, 5950000), 25832))"""
        )
