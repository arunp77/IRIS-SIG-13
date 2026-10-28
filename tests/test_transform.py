"""Classifying, locating, dating and normalising records."""

from copy import deepcopy
from datetime import timedelta

import pytest

from harvester.source import parse_detail
from harvester.transform import choose_date, classify, load_rules, locate, normalise
from tests.helpers import fixture_detail_html, record_by_id

RULES = load_rules("config/signals.yaml")
PLAN = "5b611dce-4bd4-11ea-afa7-87b6b3c7cd7c"
NO_DATES = "acce6a96-4794-11f1-b715-87b47a647849"


def signal_of(title):
    return classify(title, RULES)[0]


# --- classification ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "BPlan Nr. 4 Musterdorf Solarpark Nord",
        "Sondergebiet Freiflächenphotovoltaikanlage Zweedorf",  # the word sits inside a compound
        "Agri-PV Anlage Beispiel",
        "Agri PV Anlage Beispiel",
        "Vorhabenbezogener BPlan Nr. 4 Leizen PV-Freiflächenanlage",
        "Gewerbepark Friedländer Stärke_PV",
        "SO Fotovoltaik am Gewerbegebiet",
        "Bürgersolaranlage Bahndreieck",
        "Vorhabenbezogener BPlan PVA Voigtsdorf",
    ],
)
def test_solar_spellings(title):
    assert signal_of(title) == "solar_acreage"


@pytest.mark.parametrize(
    "title",
    [
        "BPlan Nr. 2 Damshagen OT Moor",  # Moor is a place name
        "BPlan Nr. 15 Leezen Wohnbebauung Groth Moor",
        "Gewerbegebiet am Umspannwerk",  # a substation alone is not a landing point
        "Fährhafen Sassnitz Erweiterungsgebiet Offshore Süd",  # neither is a ferry port
        "BPlan Nr. 2 PVC-Recycling Lager",  # PV inside another word
        "BPlan Broderstorf Wohnen",
    ],
)
def test_place_names_and_neighbours_are_no_signal(title):
    assert signal_of(title) is None


@pytest.mark.parametrize(
    ("title", "signal"),
    [
        ("Anlandung Seekabel Ostsee", "grid_landing"),
        ("Sondergebiet Umspannwerk für Offshore-Windpark", "grid_landing"),
        ("Wiedervernässung Teilfläche Beispiel", "peatland_restoration"),
        ("Renaturierung des Moores", "peatland_restoration"),
        ("Solarpark mit Wiedervernässung", "peatland_restoration"),  # the first signal in the file wins
    ],
)
def test_the_rarer_signals(title, signal):
    assert signal_of(title) == signal


def test_matched_words_are_the_words_of_the_title():
    signal, matched = classify("Solarparks und Photovoltaikanlage", RULES)
    assert signal == "solar_acreage" and set(matched) == {"Solar", "Photovoltaik"}


# --- location ----------------------------------------------------------------------------


def with_centroid(value):
    record = deepcopy(record_by_id(NO_DATES))
    if value is None:
        record.pop("x_centroid")
    else:
        record["x_centroid"] = [value]
    return record


def test_real_centroid_is_resolved_with_easting_as_x():
    status, x, y, reason = locate(record_by_id(NO_DATES))
    assert (status, x, y, reason) == ("resolved", 644583.578, 5938120.287, None)


def test_centroid_may_come_as_plain_string_or_list():
    assert locate({"x_centroid": "5938120.2,644583.5"})[0] == "resolved"
    assert locate({"x_centroid": ["5938120.2,644583.5"]})[0] == "resolved"


def test_missing_centroid_is_unresolved_not_invalid():
    status, x, y, reason = locate(with_centroid(None))
    assert (status, x, y) == ("unresolved", None, None) and "no centroid" in reason


@pytest.mark.parametrize(
    "value",
    [
        "644583.578,5938120.287",  # the usual x,y order
        "54.1,12.4",  # degrees instead of metres
        "5886000,457000",  # Szczecin, Poland: inside the UTM zone, outside the state
        "0,0",
        "abc,def",
        "5938120.2",
        "1,2,3",
    ],
)
def test_invalid_centroid_is_refused_and_says_why(value):
    status, x, y, reason = locate(with_centroid(value))
    assert status == "invalid" and x is None and reason


def test_an_empty_centroid_counts_as_missing():
    assert locate(with_centroid(""))[0] == "unresolved"


# --- date --------------------------------------------------------------------------------


def detail(dates=None, documents=()):
    return {"fields": {}, "dates": dates or {}, "documents": list(documents)}


def test_start_of_display_beats_entry_into_force_beats_council_decision():
    all_three = detail(
        {
            "Aufstellungsbeschlußdatum": "2025-01-01",
            "Inkrafttretensdatum": "2025-06-01",
            "Auslegungsstartdatum": "2025-03-01",
        }
    )
    assert choose_date(all_three)[1] == "Auslegungsstartdatum"
    del all_three["dates"]["Auslegungsstartdatum"]
    assert choose_date(all_three)[1] == "Inkrafttretensdatum"
    del all_three["dates"]["Inkrafttretensdatum"]
    assert choose_date(all_three)[1] == "Aufstellungsbeschlußdatum"


def test_end_of_display_and_other_dates_are_not_a_publication_date():
    assert choose_date(detail({"Auslegungsenddatum": "2025-06-13", "Ausfertigungsdatum": "2025-06-14"})) == (None, None)


def test_a_download_titled_aufstellungsbeschluss_is_the_fallback():
    docs = [
        {"title": "Aufstellungsbeschluss (16.06.2026)", "url": "u", "date": "2026-06-16"},
        {"title": "Stellungnahme 25.01.2024", "url": "v", "date": "2024-01-25"},
    ]
    when, basis = choose_date(detail(documents=docs))
    assert when.date().isoformat() == "2026-06-16" and basis.startswith("download:")


def test_the_date_is_midnight_in_berlin_summer_and_winter():
    summer = choose_date(detail({"Inkrafttretensdatum": "2025-07-01"}))[0]
    winter = choose_date(detail({"Inkrafttretensdatum": "2025-01-01"}))[0]
    assert summer.utcoffset() == timedelta(hours=2) and winter.utcoffset() == timedelta(hours=1)


# --- normalising -------------------------------------------------------------------------


def candidate(plan_id=PLAN, record=None):
    return {"record": record or record_by_id(plan_id), "found_by": [["solar_acreage", "Solarpark"]]}


def build(plan_id=PLAN, record=None):
    return normalise(candidate(plan_id, record), parse_detail(fixture_detail_html(plan_id)), "solar_acreage", ["Solar"])


def test_a_complete_record_becomes_a_row():
    row, warnings, rejected = build()
    assert rejected is None and warnings == []
    assert row["source"] == "bplan_mv" and row["source_id"] == PLAN
    assert row["location_status"] == "resolved" and row["x"] and row["y"]
    assert row["published_at"].date().isoformat() == "2017-11-29"
    assert row["source_url"] == f"https://www.bauportal-mv.de/bauportal/Uebersicht/Details?type=bplan&id={PLAN}"
    assert row["legal_status"] and row["municipality"] and row["district"]
    assert len(row["document_urls"]) >= 6 and all(" " not in u for u in row["document_urls"])
    assert row["raw_payload"]["search_record"] == record_by_id(PLAN)  # as received
    assert row["raw_payload"]["published_at_basis"] == "Inkrafttretensdatum"


def test_a_missing_date_and_a_missing_location_are_warnings_not_silence():
    record = with_centroid(None)
    row, warnings, _ = normalise(
        candidate(NO_DATES, record), parse_detail(fixture_detail_html(NO_DATES)), "solar_acreage", []
    )
    assert row["published_at"] is None and row["location_status"] == "unresolved" and row["x"] is None
    assert any("no usable date" in w for w in warnings) and any("location unresolved" in w for w in warnings)


def test_an_invalid_location_rejects_the_record_with_the_reason():
    row, _, rejected = build(NO_DATES, with_centroid("54.1,12.4"))
    assert row is None and rejected.startswith("invalid location:")


def test_a_record_without_a_title_is_rejected():
    record = deepcopy(record_by_id(PLAN))
    record["title"] = ["   "]
    assert build(record=record)[2] == "record has no title"


def test_the_hash_is_stable_and_ignores_the_raw_payload():
    first, second = build()[0], build()[0]
    assert first["content_hash"] == second["content_hash"] and len(first["content_hash"]) == 64
    reindexed = deepcopy(record_by_id(PLAN))
    reindexed["_resourceIndexed"] = "2031-01-01T00:00:00+00:00"  # the portal re-indexed the plan
    assert build(record=reindexed)[0]["content_hash"] == first["content_hash"]


def test_the_hash_changes_when_the_content_changes():
    changed = deepcopy(record_by_id(PLAN))
    changed["title"] = ["A different title"]
    assert build(record=changed)[0]["content_hash"] != build()[0]["content_hash"]
