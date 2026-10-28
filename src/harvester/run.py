"""One harvest run, from the search to the database, and its report.

    harvest --init-db             create the table          (same as: python -m harvester.run ...)
    harvest --dry-run             fetch and check everything, write nothing
    harvest --offline             answer only from data/raw (no network, no contact address needed)
    harvest --report out.json     harvest and load

search -> classify by title -> read the detail page -> normalise -> load

Every record that is dropped, skipped or incomplete is written to the report with the reason.
Exit code: 0 clean, 2 finished but something failed that a re-run may fix, 1 could not start.
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from harvester import load, source, transform
from harvester.client import Client, FetchError

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_CONTACT = "your.name@example.com"  # from .env.example


def harvest(client, rules, *, signals=None, max_details=None, page_size=100):
    """Search, classify, fetch and normalise. Returns (rows, report); nothing is written."""
    terms = {s: r["search_terms"] for s, r in rules.items() if not signals or s in signals}
    candidates, seen, problems = source.collect(client, terms, page_size)
    report = {
        "records_seen": seen,
        "candidates": len(candidates),
        "search_problems": problems,
        "unmatched": [],
        "detail_errors": [],
        "rejected": [],
        "warnings": [],
        "deferred": 0,
    }
    rows, fetched = [], 0
    for record_id, candidate in candidates.items():
        title = " ".join(str(source.first(candidate["record"].get("title")) or "").split())
        signal, matched = transform.classify(title, rules)
        if signal is None or (signals and signal not in signals):
            report["unmatched"].append({"id": record_id, "title": title})
            continue
        if max_details is not None and fetched >= max_details:
            report["deferred"] += 1  # --max-details: left for a later, longer run
            continue
        fetched += 1
        entry = {"id": record_id, "title": title}
        try:  # a failed detail page skips the plan: loading it without its date and links would
            detail = source.fetch_detail(client, candidate["record"])  # overwrite a complete row
        except (FetchError, source.SourceError) as exc:
            report["detail_errors"].append({**entry, "reason": str(exc)})
            continue
        row, warnings, reject = transform.normalise(candidate, detail, signal, matched)
        report["warnings"] += [{**entry, "warning": w} for w in warnings]
        if row is None:
            report["rejected"].append({**entry, "reason": reject})
        else:
            rows.append(row)
    report["quality"] = {
        "rows": len(rows),
        "by_signal": dict(Counter(r["signal_type"] for r in rows)),
        "without_published_at": sum(r["published_at"] is None for r in rows),
        "without_documents": sum(not r["document_urls"] for r in rows),
        "location_unresolved": sum(r["location_status"] != "resolved" for r in rows),
    }
    report["requests"] = {"network": client.network_requests, "cache_hits": client.cache_hits}
    return rows, report


def summary(report, dry_run):
    q, load_stats = report["quality"], report.get("load")
    lines = [
        f"Harvest report{' (dry run, nothing written)' if dry_run else ''}",
        f"  search      {report['records_seen']} records seen, {report['candidates']} distinct "
        f"({report['requests']['network']} requests, {report['requests']['cache_hits']} from cache)",
        f"  classified  {q['rows']} kept {q['by_signal']}, {len(report['unmatched'])} matched no signal"
        + (f", {report['deferred']} deferred (--max-details)" if report["deferred"] else ""),
        f"  skipped     {len(report['detail_errors'])} detail page errors, {len(report['rejected'])} rejected, "
        f"{len(report['search_problems'])} search problems",
        f"  quality     {q['without_published_at']} without date, {q['location_unresolved']} without point, "
        f"{q['without_documents']} without documents",
    ]
    if load_stats:
        lines.append(
            f"  database    {load_stats['inserted']} inserted, {load_stats['updated']} updated, "
            f"{load_stats['unchanged']} unchanged, {len(load_stats['failed'])} failed"
        )
    for title, items in (
        ("Search problems", report["search_problems"]),
        ("Detail page errors", report["detail_errors"]),
        ("Rejected", report["rejected"]),
        ("Load errors", load_stats["failed"] if load_stats else []),
    ):
        for item in items[:3]:
            lines.append(f"  {title}: " + "; ".join(f"{k}={str(v)[:70]}" for k, v in item.items()))
        if len(items) > 3:
            lines.append(f"  ... {len(items) - 3} more {title.lower()} in the JSON report")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Harvest Mecklenburg-Vorpommern planning records.")
    parser.add_argument("--init-db", action="store_true", help="create the schema and the table, then stop")
    parser.add_argument("--dry-run", action="store_true", help="fetch and check, write nothing")
    parser.add_argument("--signal", action="append", help="only this signal (repeatable)")
    parser.add_argument("--max-details", type=int, metavar="N", help="fetch at most N detail pages")
    parser.add_argument("--report", type=Path, help="write the full report as JSON")
    parser.add_argument("--cache-dir", default=str(ROOT / "data" / "raw"))
    parser.add_argument("--cache-ttl-hours", type=float, default=24.0)
    parser.add_argument("--min-interval", type=float, default=2.0)
    parser.add_argument(
        "--offline", action="store_true", help="answer only from the cache (data/raw), never ask the portal"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load_dotenv(ROOT / ".env")
    db_url, contact = os.environ.get("DATABASE_URL"), os.environ.get("HARVESTER_CONTACT", "").strip()

    if args.init_db:
        if not db_url:
            return _fail("DATABASE_URL is not set")
        with psycopg.connect(db_url) as conn:
            conn.execute((ROOT / "sql" / "01_schema.sql").read_text(encoding="utf-8"))
        print("schema and table are ready")
        return 0
    if not args.offline and (not contact or contact.lower() == PLACEHOLDER_CONTACT):
        return _fail("set HARVESTER_CONTACT in .env to your own e-mail address (it is sent to the portal)")
    if not args.dry_run and not db_url:
        return _fail("DATABASE_URL is not set (or use --dry-run)")

    rules = transform.load_rules(ROOT / "config" / "signals.yaml")
    if args.signal and set(args.signal) - set(rules):
        return _fail(f"unknown signal, choose from {', '.join(rules)}")
    client = Client(
        contact or "offline",
        cache_dir=args.cache_dir,
        cache_ttl_hours=args.cache_ttl_hours,
        min_interval=0 if args.offline else args.min_interval,
        offline=args.offline,
    )

    rows, report = harvest(client, rules, signals=args.signal, max_details=args.max_details)
    if not args.dry_run:
        with psycopg.connect(db_url) as conn:  # one transaction: a crash writes nothing
            report["load"] = load.upsert(conn, rows, datetime.now(timezone.utc))
    print(summary(report, args.dry_run))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    failed = report["search_problems"] or report["detail_errors"] or (report.get("load") or {}).get("failed")
    return 2 if failed else 0


def _fail(message):
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
