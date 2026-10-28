"""Loading rows into iris_staging.signal_ingest, safe to repeat.

A plan is identified by (source, source_id). Loading it again does one of three things, decided
by its content hash:

    new        the row is inserted
    changed    the content columns are replaced and updated_at moves
    unchanged  only last_seen_at moves

A plan that disappears from the portal stays in the table (nothing is deleted); its
last_seen_at simply stops moving.
"""

import logging

from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

# content columns, i.e. everything that is replaced when a plan has changed
CONTENT = [
    "signal_type",
    "matched_terms",
    "title",
    "source_url",
    "document_urls",
    "published_at",
    "legal_status",
    "plan_type",
    "municipality",
    "district",
    "location_status",
    "content_hash",
    "raw_payload",
]

UPSERT = f"""
INSERT INTO iris_staging.signal_ingest
    (source, source_id, {", ".join(CONTENT)}, geom, first_seen_at, last_seen_at, updated_at)
VALUES
    (%(source)s, %(source_id)s, {", ".join(f"%({c})s" for c in CONTENT)},
     ST_SetSRID(ST_MakePoint(%(x)s::float8, %(y)s::float8), 25832), %(run_at)s, %(run_at)s, %(run_at)s)
ON CONFLICT (source, source_id) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in CONTENT)},
    geom = EXCLUDED.geom, last_seen_at = EXCLUDED.last_seen_at, updated_at = EXCLUDED.updated_at
"""

TOUCH = """UPDATE iris_staging.signal_ingest SET last_seen_at = %s WHERE source = %s AND source_id = %s"""


def upsert(conn, rows, run_at):
    """Insert or update every row. `run_at` must be timezone-aware.

    Each row is written in its own savepoint: a row the database refuses is listed under
    "failed" and the others are still loaded. The caller commits.
    """
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "failed": []}
    known = dict(
        conn.execute("SELECT source_id, content_hash FROM iris_staging.signal_ingest WHERE source = 'bplan_mv'")
    )
    seen = set()
    for row in rows:
        if row["source_id"] in seen:  # the same plan twice in one batch: load it once
            continue
        seen.add(row["source_id"])
        try:
            with conn.transaction():
                if known.get(row["source_id"]) == row["content_hash"]:
                    conn.execute(TOUCH, (run_at, row["source"], row["source_id"]))
                    stats["unchanged"] += 1
                else:
                    conn.execute(UPSERT, {**row, "raw_payload": Jsonb(row["raw_payload"]), "run_at": run_at})
                    stats["updated" if row["source_id"] in known else "inserted"] += 1
        except Exception as exc:  # e.g. a CHECK constraint
            log.error("could not load %s: %s", row["source_id"], exc)
            stats["failed"].append({"source_id": row["source_id"], "error": str(exc).splitlines()[0]})
    return stats
