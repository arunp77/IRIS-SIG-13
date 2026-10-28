"""Run the whole pipeline without the live portal, twice, into the local database.

The portal is replaced by the real saved answers in tests/fixtures/portal (the same fake that the
tests use). The second run must change nothing.

    python scripts/demo_offline.py        (needs the database: docker compose up -d, then --init-db)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import responses
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from harvester import load, run, transform  # noqa: E402
from tests.helpers import quick_client, serve_portal  # noqa: E402


@responses.activate
def main():
    load_dotenv(ROOT / ".env")
    serve_portal()
    rules = transform.load_rules(ROOT / "config" / "signals.yaml")
    # the two runs get different times, both in the past, so a real run right afterwards is never "older"
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for number in (1, 2):
            rows, report = run.harvest(quick_client(), rules)
            report["load"] = load.upsert(conn, rows, start + timedelta(minutes=number))
            print(f"--- run {number} " + "-" * 50)
            print(run.summary(report, dry_run=False))
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "demo_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print("\nreport of the second run: out/demo_report.json")


if __name__ == "__main__":
    main()
