"""The dashboard: its data functions (no database) and the page itself (Streamlit's AppTest, needs the database)."""

from datetime import datetime, timezone

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tests.test_load import make_row
from ui import plans

APP = str(plans.ROOT / "ui" / "app.py")


def frame(*rows):
    """A small frame with the columns of the staging query; each row overrides the defaults."""
    default = {
        "signal_type": "solar_acreage",
        "title": "BPlan Solarpark Test",
        "document_urls": ["https://example.org/a.pdf"],
        "published_at": datetime(2026, 5, 1, 10, tzinfo=timezone.utc),
        "legal_status": "In Kraft getreten",
        "district": "Vorpommern-Rügen",
        "location_status": "resolved",
        "date_basis": "Inkrafttretensdatum",
    }
    return plans.prepare(pd.DataFrame([{**default, **row} for row in rows]))


def test_stages_group_the_portals_wording():
    assert plans.stage_of("In Kraft getreten") == "In force"
    assert plans.stage_of("Wirksamkeit") == "In force"
    assert plans.stage_of("Untergegangen") == "Ended"
    assert plans.stage_of("Entwurf") == "In procedure"
    assert plans.stage_of(None) == "In procedure"


def test_the_year_is_taken_in_berlin_time_where_the_stand_in_dates_are_midnight():
    # 1 January 1990 00:00 in Berlin is 31 December 1989 23:00 in UTC
    shown = frame({"published_at": datetime(1989, 12, 31, 23, tzinfo=timezone.utc)})
    assert shown["year"].iloc[0] == 1990


def test_date_basis_is_shortened_and_a_missing_one_is_named():
    shown = frame({"date_basis": "download: Aufstellungsbeschluss (16.06.2026) (Dokument)"}, {"date_basis": None})
    assert list(shown["date_basis"]) == ["download title", "none (page has no date)"]


def test_filters_combine():
    shown = frame(
        {"title": "BPlan Agri-PV Alt", "district": "A"},
        {"title": "BPlan Solarpark Neu", "district": "B", "legal_status": "Entwurf"},
    )
    assert len(plans.filter_plans(shown, "agri-pv")) == 1  # case does not matter
    assert list(plans.filter_plans(shown, districts=["B"])["title"]) == ["BPlan Solarpark Neu"]
    assert list(plans.filter_plans(shown, stages=["In procedure"])["district"]) == ["B"]
    assert plans.filter_plans(shown, stages=[]).empty
    assert len(plans.filter_plans(shown, "  ")) == 2  # blank search is no search


def test_a_filter_text_is_not_a_pattern():
    assert plans.filter_plans(frame({"title": "BPlan (Solar)"}), "(").shape[0] == 1


def test_missing_fields_are_counted():
    shown = frame(
        {},
        {"published_at": None, "document_urls": [], "district": None, "location_status": "unresolved"},
    )
    counts = plans.missing_fields(shown).set_index("field")["plans"]
    assert counts.to_dict() == {"publication date": 1, "documents": 1, "district": 1, "location": 1}


def test_the_year_chart_has_every_year_between_the_first_and_the_last():
    shown = frame(
        {"published_at": datetime(2020, 5, 1, 10, tzinfo=timezone.utc)},
        {"published_at": datetime(2023, 5, 1, 10, tzinfo=timezone.utc), "legal_status": "Entwurf"},
        {"published_at": None},  # no date: left out
    )
    years = plans.per_year(shown)
    assert sorted(set(years["year"])) == ["2020", "2021", "2022", "2023"]  # 2021 and 2022 have no plans, but are there
    assert set(years["stage"]) == set(plans.STAGES)
    assert years["plans"].sum() == 2
    assert plans.per_year(frame({"published_at": None})).empty


def test_counts_per_label_and_stage_are_sorted_by_total_and_can_be_cut_off():
    shown = frame(
        {"district": "A"},
        {"district": "A", "legal_status": "Entwurf"},
        {"district": "A"},
        {"district": "B"},
        {"district": None},
    )
    counts = plans.stage_counts(shown, "district")
    assert list(counts["label"].drop_duplicates()) == ["A", "(none)", "B"]  # ties are sorted by name
    assert counts.drop_duplicates("label").set_index("label")["total"].to_dict() == {"A": 3, "B": 1, "(none)": 1}
    assert set(plans.stage_counts(shown, "district", top=1)["label"]) == {"A"}


def test_every_configured_signal_is_listed_even_with_zero_plans():
    counts = plans.signal_counts(frame({}, {})).set_index("signal")["plans"]
    assert counts.to_dict() == {"grid_landing": 0, "peatland_restoration": 0, "solar_acreage": 2}


def test_unmatched_plans_are_grouped_by_their_weak_word():
    report = {
        "unmatched": [
            {"id": "1", "title": "FPlan  Damshagen Teilplan Moor Nr. 0"},
            {"id": "2", "title": "BPlan Gewerbegebiet am Umspannwerk"},
            {"id": "3", "title": "FPlan Parin 1. Änderung"},
        ]
    }
    grouped = plans.unmatched_by_word(report)
    assert list(grouped["word"]) == ["Moor", "Umspannwerk", "other"]
    assert grouped["title"].iloc[0] == "FPlan Damshagen Teilplan Moor Nr. 0"  # runs of spaces collapsed


def test_the_run_report_is_summarised_for_the_page():
    report = {
        "search_problems": [],
        "detail_errors": [{"id": "1"}],
        "rejected": [],
        "warnings": [{"warning": "no date"}, {"warning": "no date"}, {"warning": "no point"}],
        "load": {"inserted": 5, "updated": 1, "unchanged": 2, "failed": [{"id": "9"}]},
    }
    problems = plans.problem_counts(report).set_index("problem")["count"]
    assert problems.to_dict() == {
        "Search terms that failed": 0,
        "Detail pages that failed": 1,
        "Plans rejected for a bad location": 0,
        "Rows the database refused": 1,
    }
    assert plans.warning_counts(report).set_index("warning")["plans"].to_dict() == {"no date": 2, "no point": 1}
    assert plans.loaded_rows(report) == 8
    dry_run = {key: value for key, value in report.items() if key != "load"}  # a report of a run that wrote nothing
    assert plans.loaded_rows(dry_run) is None
    assert plans.problem_counts(dry_run)["count"].sum() == 1  # only the failed detail page


def test_the_run_report_prefers_the_last_run_and_falls_back_to_the_sample(tmp_path):
    assert plans.load_report(tmp_path) == (None, None)
    (tmp_path / "data" / "sample").mkdir(parents=True)
    (tmp_path / "data" / "sample" / "run_report.json").write_text('{"from": "sample"}', encoding="utf-8")
    assert plans.load_report(tmp_path)[0] == {"from": "sample"}
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "full_run.json").write_text('{"from": "run"}', encoding="utf-8")
    report, where = plans.load_report(tmp_path)
    assert report == {"from": "run"} and where.replace("\\", "/") == "out/full_run.json"


# the page itself: it reads the database named by DATABASE_URL, so these use the test database


@pytest.fixture
def page(test_db_url, monkeypatch):
    """An AppTest on the test database, which is emptied before and after (the page reads committed rows)."""
    import psycopg
    import streamlit as st

    def empty():
        with psycopg.connect(test_db_url, autocommit=True) as conn:
            conn.execute("TRUNCATE iris_staging.signal_ingest")

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    st.cache_data.clear()
    empty()
    yield lambda: AppTest.from_file(APP, default_timeout=30).run()
    empty()
    st.cache_data.clear()


@pytest.mark.db
def test_an_empty_table_says_what_to_do(page):
    at = page()
    assert not at.exception
    assert at.warning and "reproduce" in at.warning[0].value


@pytest.mark.db
def test_the_page_shows_the_loaded_plans_and_filters_them(page, test_db_url):
    import psycopg

    from harvester.load import upsert

    row = make_row()
    with psycopg.connect(test_db_url) as conn:
        upsert(conn, [row], datetime(2026, 10, 27, tzinfo=timezone.utc))
        conn.commit()
    at = page()
    assert not at.exception and not at.error
    assert {m.label: m.value for m in at.metric}["Plans shown (of 1)"] == "1"
    assert at.info[0].proto.title == "Empty: grid landing, peatland restoration"  # the signals without plans
    at.text_input[0].set_value("no such title").run()
    assert not at.exception and at.info[0].value == "No plan matches the filters."


def test_an_unreachable_database_is_reported_not_a_traceback(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@localhost:1/none?connect_timeout=2")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.error and "Database not reachable" in at.error[0].proto.title
