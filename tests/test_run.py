"""A whole run on the fake portal: search, classify, detail pages, load, and the report."""

from datetime import datetime, timedelta, timezone

import pytest
import responses

from harvester import run
from harvester.load import upsert
from harvester.transform import load_rules
from tests.helpers import quick_client, serve_portal

RULES = load_rules("config/signals.yaml")
RUN1 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def harvest(**options):
    return run.harvest(quick_client(), RULES, **options)


@responses.activate
def test_the_demo_data_gives_twelve_solar_plans_and_five_that_match_nothing():
    serve_portal()
    rows, report = harvest()
    assert len(rows) == 12 and {r["signal_type"] for r in rows} == {"solar_acreage"}
    assert report["candidates"] == 17 and report["records_seen"] > report["candidates"]  # repeats merged
    assert len(report["unmatched"]) == 5  # place names with "Moor", plans next to a substation
    assert report["detail_errors"] == report["rejected"] == report["search_problems"] == []
    assert report["quality"]["without_published_at"] == 2 and report["quality"]["without_documents"] == 3


@responses.activate
def test_a_vanished_detail_page_skips_the_plan_and_says_so():
    serve_portal(missing_details={"cf1b05c0-2732-11ef-87bb-47c1e8e0114e"})
    rows, report = harvest()
    assert len(rows) == 11
    assert [e["id"] for e in report["detail_errors"]] == ["cf1b05c0-2732-11ef-87bb-47c1e8e0114e"]
    assert "no plan table" in report["detail_errors"][0]["reason"]


@responses.activate
def test_only_the_wanted_signal_is_searched():
    serve_portal()
    rows, report = harvest(signals=["grid_landing"])
    assert rows == [] and report["candidates"] == 2  # the two "Umspannwerk" plans, which are no landing point


@responses.activate
def test_max_details_defers_the_rest():
    serve_portal()
    rows, report = harvest(max_details=5)
    assert len(rows) == 5 and report["deferred"] == 7


@responses.activate
@pytest.mark.db
def test_loading_twice_changes_nothing_the_second_time(conn):
    serve_portal()
    rows, _ = harvest()
    one = upsert(conn, rows, RUN1)
    two = upsert(conn, harvest()[0], RUN1 + timedelta(hours=1))
    assert (one["inserted"], one["updated"], one["unchanged"]) == (12, 0, 0)
    assert (two["inserted"], two["updated"], two["unchanged"]) == (0, 0, 12)
    assert conn.execute("SELECT count(*) FROM iris_staging.signal_ingest").fetchone()[0] == 12


@responses.activate
@pytest.mark.db
def test_a_failed_detail_page_leaves_the_stored_row_untouched(conn):
    serve_portal()
    upsert(conn, harvest()[0], RUN1)
    gone = "cf1b05c0-2732-11ef-87bb-47c1e8e0114e"
    responses.reset()
    serve_portal(missing_details={gone})
    rows, report = harvest()
    stats = upsert(conn, rows, RUN1 + timedelta(days=1))
    assert stats["unchanged"] == 11 and len(report["detail_errors"]) == 1
    last_seen, date = conn.execute(
        "SELECT last_seen_at, published_at FROM iris_staging.signal_ingest WHERE source_id = %s", (gone,)
    ).fetchone()
    assert last_seen == RUN1 and date is not None  # not seen this run, and still the complete record


# --- the command -------------------------------------------------------------------------


@responses.activate
def test_dry_run_command_prints_the_report_and_writes_the_json(monkeypatch, tmp_path, capsys):
    serve_portal()
    monkeypatch.setenv("HARVESTER_CONTACT", "tester@example.org")
    report_file = tmp_path / "new_folder" / "report.json"
    code = run.main(
        ["--dry-run", "--min-interval", "0", "--cache-dir", str(tmp_path / "cache"), "--report", str(report_file)]
    )
    assert code == 0 and "dry run, nothing written" in capsys.readouterr().out
    assert report_file.exists()


def test_the_placeholder_contact_address_is_refused(monkeypatch, capsys):
    monkeypatch.setenv("HARVESTER_CONTACT", run.PLACEHOLDER_CONTACT)
    assert run.main(["--dry-run"]) == 1
    assert "HARVESTER_CONTACT" in capsys.readouterr().err


@responses.activate
def test_exit_code_2_when_something_failed(monkeypatch, tmp_path):
    serve_portal(missing_details={"cf1b05c0-2732-11ef-87bb-47c1e8e0114e"})
    monkeypatch.setenv("HARVESTER_CONTACT", "tester@example.org")
    assert run.main(["--dry-run", "--min-interval", "0", "--cache-dir", str(tmp_path)]) == 2


def test_offline_run_needs_no_contact_address_and_reports_what_the_cache_lacks(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HARVESTER_CONTACT", run.PLACEHOLDER_CONTACT)
    code = run.main(["--dry-run", "--offline", "--cache-dir", str(tmp_path), "--signal", "grid_landing"])
    assert code == 2  # the cache is empty: every search term fails, visibly
    assert "7 search problems" in capsys.readouterr().out
