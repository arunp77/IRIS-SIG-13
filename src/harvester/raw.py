"""The raw portal answers: pack them into an archive with a manifest, unpack and verify them.

data/raw/ is where the client keeps every answer of the portal, one JSON file per request
(url, time, body exactly as received). That is the raw data of this project and the provenance of
every row in the database. The files themselves are not committed to git; what is committed is

    data/raw/portal_answers_<date>.tar.gz    all raw answers of the full run, compressed (about 1 MB)
    data/raw/MANIFEST.csv                    one line per answer: file, kind, url, time, size, SHA-256 of the body

    python -m harvester.raw unpack           unpack the archive into data/raw/ and check every file against the manifest
    python -m harvester.raw pack CACHE_DIR   build archive and manifest from a cache folder

After `unpack`, `harvest --offline` replays the full run from these files without asking the portal.
"""

import argparse
import csv
import hashlib
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from harvester.source import SEARCH_URL

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
FIELDS = ["file", "kind", "url", "fetched_at_utc", "bytes", "body_sha256"]


def _entry(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(cache_dir, raw=RAW):
    """Archive every cached answer of `cache_dir` and write the manifest. Returns the number of files."""
    files = sorted(Path(cache_dir).glob("*.json"))
    rows = []
    for path in files:
        entry = _entry(path)
        rows.append(
            {
                "file": path.name,
                "kind": "search" if entry["url"].startswith(SEARCH_URL) else "detail",
                "url": entry["url"],
                "fetched_at_utc": _utc(entry["fetched_at"]),
                "bytes": path.stat().st_size,
                "body_sha256": _sha256(entry["body"]),
            }
        )
    raw.mkdir(parents=True, exist_ok=True)
    day = max(r["fetched_at_utc"] for r in rows)[:10]
    with tarfile.open(raw / f"portal_answers_{day}.tar.gz", "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.name)
    with (raw / "MANIFEST.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(files)


def unpack(raw=RAW):
    """Unpack the archive into `raw` and check every file against the manifest.

    Returns (files unpacked, problems). Only plain *.json files without a path are taken from the archive.
    """
    archives = sorted(raw.glob("portal_answers_*.tar.gz"))
    if not archives:
        return 0, [f"no portal_answers_*.tar.gz in {raw}"]
    for path in archives:
        with tarfile.open(path) as archive:
            safe = [
                m
                for m in archive.getmembers()
                if m.isfile() and m.name == Path(m.name).name and m.name.endswith(".json")
            ]
            # the "data" filter exists from Python 3.11.4; the member list above is already restricted
            options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            archive.extractall(raw, members=safe, **options)
    problems = []
    with (raw / "MANIFEST.csv").open(encoding="utf-8") as handle:
        manifest = list(csv.DictReader(handle))
    for row in manifest:
        path = raw / row["file"]
        try:
            entry = _entry(path)
        except (OSError, ValueError):
            problems.append(f"{row['file']}: missing or damaged")
            continue
        if entry.get("url") != row["url"] or _sha256(entry.get("body", "")) != row["body_sha256"]:
            problems.append(f"{row['file']}: does not match the manifest")
    return len(manifest), problems


def _utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("unpack", help="unpack data/raw/*.tar.gz and verify it")
    sub.add_parser("pack", help="build archive and manifest from a cache folder").add_argument("cache_dir")
    args = parser.parse_args(argv)
    if args.command == "pack":
        print(f"{pack(args.cache_dir)} answers packed into {RAW}")
        return 0
    count, problems = unpack()
    for problem in problems[:10]:
        print("problem:", problem, file=sys.stderr)
    print(f"{count} raw answers in {RAW}, {count - len(problems)} verified against MANIFEST.csv")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
