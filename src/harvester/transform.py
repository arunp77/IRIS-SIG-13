"""From a portal record to a clean staging row: classify, locate, date, normalise."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pyproj import Transformer

from harvester.source import detail_url, first

SOURCE = "bplan_mv"

# Generous box around Mecklenburg-Vorpommern in WGS84 (lon_min, lat_min, lon_max, lat_max). It
# catches values that cannot be in MV, not the exact border: a point just across the border
# would still pass.
MV_BOX = (10.5, 53.0, 14.5, 54.8)
_to_wgs84 = Transformer.from_crs(25832, 4326, always_xy=True)


# ---------------------------------------------------------------- classification


def load_rules(path="config/signals.yaml"):
    """Read config/signals.yaml; patterns are compiled once. Returns {signal: rule}."""
    rules = {}
    for signal, spec in yaml.safe_load(Path(path).read_text(encoding="utf-8")).items():
        rules[signal] = {
            "search_terms": list(spec["search_terms"]),
            "any": [re.compile(p, re.I) for p in spec.get("any", [])],
            "all_of": [[re.compile(p, re.I) for p in group] for group in spec.get("all_of", [])],
        }
    return rules


def classify(title, rules):
    """(signal, matched words) for a title, or (None, []) if no signal matches."""
    for signal, rule in rules.items():  # the order of the file is the priority
        hits = [m.group(0) for p in rule["any"] if (m := p.search(title))]
        for group in rule["all_of"]:
            found = [p.search(title) for p in group]
            if all(found):
                hits += [m.group(0) for m in found]
        if hits:
            return signal, list(dict.fromkeys(hits))
    return None, []


# ---------------------------------------------------------------- location


def locate(record):
    """Resolve a record's centroid to a point in EPSG:25832.

    Returns (status, x, y, reason). Three outcomes, none of them silent:
      resolved    a usable point (x = easting, y = northing, metres)
      unresolved  no centroid at all: the record is kept without a point
      invalid     a centroid that cannot be right: the record is rejected, with the reason

    The portal gives the centroid as "northing,easting", the reverse of the usual x,y order. A
    value in the usual order, in degrees, or outside MV ends up outside the box and is invalid.
    """
    raw = first(record.get("x_centroid"))
    if raw is None:
        return "unresolved", None, None, "record has no centroid"
    try:
        northing, easting = (float(part) for part in raw.split(","))
    except ValueError:
        return "invalid", None, None, f"centroid is not 'northing,easting': {raw!r}"
    lon, lat = _to_wgs84.transform(easting, northing)
    inside = MV_BOX[0] <= lon <= MV_BOX[2] and MV_BOX[1] <= lat <= MV_BOX[3]  # False for nan/inf
    if not inside:
        return (
            "invalid",
            None,
            None,
            f"centroid outside Mecklenburg-Vorpommern: easting={easting}, northing={northing} (from {raw!r})",
        )
    return "resolved", round(easting, 3), round(northing, 3), None


# ---------------------------------------------------------------- date

# The portal has no publication date. The detail page has dates of legal steps, which ones exist
# depends on the stage of the plan. The first one found, in this order, stands in for it:
DATE_ORDER = [
    "auslegungsstartdatum",  # start of the public display
    "inkrafttretensdatum",  # entry into force
    "aufstellungsbeschlussdatum",
]  # council decision (comes before any publication)


def choose_date(detail):
    """(midnight Europe/Berlin as a datetime, label it came from), or (None, None)."""
    by_label = {label.casefold().replace("ß", "ss"): (label, day) for label, day in detail["dates"].items()}
    for wanted in DATE_ORDER:
        if wanted in by_label:
            label, day = by_label[wanted]
            return _midnight(day), label
    for doc in detail["documents"]:  # fallback: "Aufstellungsbeschluss (16.06.2026)"
        if doc["date"] and doc["title"].casefold().startswith("aufstellungsbeschluss"):
            return _midnight(doc["date"]), f"download: {doc['title']}"
    return None, None


def _midnight(day):
    return datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Europe/Berlin"))


# ---------------------------------------------------------------- normalise


def normalise(candidate, detail, signal, matched):
    """Build the staging row for one candidate. Returns (row, warnings, reject_reason).

    `row` is None when the record must be rejected (invalid location); `warnings` list what is
    missing or looked odd, so that nothing is dropped silently.
    """
    record, warnings = candidate["record"], []
    status, x, y, reason = locate(record)
    if status == "invalid":
        return None, warnings, f"invalid location: {reason}"
    if status == "unresolved":
        warnings.append(f"location unresolved: {reason}")

    published_at, basis = choose_date(detail)
    if published_at is None:
        warnings.append("detail page has no usable date, published_at left empty")

    links = [str(u).strip() for u in record.get("x_dokumentURL") or []] + [d["url"] for d in detail["documents"]]
    record_id = first(record.get("x_gmlid"))
    row = {
        "source": SOURCE,
        "source_id": record_id,
        "signal_type": signal,
        "matched_terms": matched,
        "title": " ".join(str(first(record.get("title")) or "").split()),
        "source_url": detail_url(record),
        # the portal does not encode spaces in file names; make the links usable
        "document_urls": list(dict.fromkeys(u.replace(" ", "%20") for u in links if u)),
        "published_at": published_at,
        "legal_status": first(record.get("x_rechtsstandtext")),
        "plan_type": first(record.get("x_planarthauptText")),
        "municipality": first(record.get("x_gemeinde")),
        "district": first(record.get("x_landkreis")),
        "x": x,
        "y": y,
        "location_status": status,
    }
    if not row["title"]:
        return None, warnings, "record has no title"
    # the hash covers the content, not the raw payload: a re-index by the portal is no change
    row["content_hash"] = hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    row["raw_payload"] = {
        "search_record": record,  # exactly as received
        "detail": detail,  # the parsed detail page
        "found_by": candidate["found_by"],  # (signal, search term) pairs
        "published_at_basis": basis,  # which portal date stands in for the publication date
    }
    return row, warnings, None
