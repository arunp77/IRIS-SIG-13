"""The portal side: paging through search results, merging records, reading detail pages."""

import glob
import json

import pytest
import responses
from responses import matchers

from harvester.source import SEARCH_URL, SourceError, collect, fetch_detail, parse_detail, search
from tests.helpers import FIXTURES, fixture_detail_html, fixture_records, quick_client, record_by_id, serve_portal

PLAN = "5b611dce-4bd4-11ea-afa7-87b6b3c7cd7c"  # in force, entry-into-force date, 6 downloads
NO_DATES = "acce6a96-4794-11f1-b715-87b47a647849"  # draft without any date row


def groups_of(term):
    return json.loads((FIXTURES / "search" / f"{term}.json").read_text(encoding="utf-8"))["results"]


def ids_of(term):
    return {r["x_gmlid"][0] for r in fixture_records(term)}


def offsets():
    return [int(call.request.params["offset"]) for call in responses.calls]


# --- searching ---------------------------------------------------------------------------


@responses.activate
def test_all_pages_are_fetched_once_and_paging_counts_groups():
    serve_portal()
    found = list(search(quick_client(), "Solarpark", page_size=2))
    assert found == fixture_records("Solarpark")
    groups = len(groups_of("solarpark"))
    assert offsets() == list(range(0, groups, 2))  # next offset = groups received, no request past the end


@responses.activate
def test_term_without_matches_gives_nothing():
    serve_portal()
    assert list(search(quick_client(), "Seekabel")) == []
    assert len(responses.calls) == 1


@responses.activate
def test_empty_page_ends_the_search_even_if_total_promises_more():
    page = {"success": True, "total": 50, "results": [[{"x_gmlid": ["a"]}]]}
    responses.add(responses.GET, SEARCH_URL, json=page)
    responses.add(responses.GET, SEARCH_URL, json={"success": True, "total": 50, "results": []})
    assert len(list(search(quick_client(), "x"))) == 1
    assert offsets() == [0, 1]


@responses.activate
@pytest.mark.parametrize(
    "body", ["<html>maintenance</html>", "[]", '{"success": false}', '{"success": true, "results": "no"}']
)
def test_unusable_answers_raise_a_source_error(body):
    responses.add(responses.GET, SEARCH_URL, body=body)
    with pytest.raises(SourceError):
        list(search(quick_client(), "x"))


@responses.activate
def test_answer_without_results_is_an_empty_result():
    responses.add(responses.GET, SEARCH_URL, json={"success": True})
    assert list(search(quick_client(), "x")) == []


# --- collecting --------------------------------------------------------------------------


@responses.activate
def test_a_plan_found_by_two_terms_is_kept_once_with_both_sightings():
    serve_portal()
    candidates, seen, problems = collect(quick_client(), {"solar_acreage": ["Solarpark", "Solar"]})
    park, solar = ids_of("Solarpark"), ids_of("Solar")
    assert park & solar and problems == []
    assert seen == len(fixture_records("Solarpark")) + len(fixture_records("Solar"))
    assert set(candidates) == park | solar  # a plan listed twice, or found by both terms, counts once
    assert all(len(candidates[i]["found_by"]) == 2 for i in park & solar)


@responses.activate
def test_a_failing_term_is_reported_and_the_others_still_run():
    query = {"type": "Bauleitplan", "q": "Broken", "offset": "0", "n": "100", "crs": "EPSG:25832"}
    responses.add(responses.GET, SEARCH_URL, status=403, match=[matchers.query_param_matcher(query)])
    serve_portal()
    candidates, _, problems = collect(quick_client(), {"solar_acreage": ["Broken", "Solarpark"]})
    assert [p["term"] for p in problems] == ["Broken"] and "403" in problems[0]["error"]
    assert set(candidates) == ids_of("Solarpark")


@responses.activate
def test_record_without_an_id_is_reported_not_dropped_silently():
    responses.add(responses.GET, SEARCH_URL, json={"success": True, "total": 1, "results": [[{"title": "no id"}]]})
    candidates, seen, problems = collect(quick_client(), {"solar_acreage": ["x"]})
    assert candidates == {} and seen == 1 and "x_gmlid" in problems[0]["error"]


# --- detail pages ------------------------------------------------------------------------


def test_dates_are_read_by_label():
    detail = parse_detail(fixture_detail_html(PLAN))
    assert detail["dates"] == {"Inkrafttretensdatum": "2017-11-29"}
    assert detail["fields"]["Inkrafttretensdatum"].startswith("29.11.2017")


def test_label_with_sharp_s_is_kept_as_the_portal_writes_it():
    detail = parse_detail(fixture_detail_html("06eef634-9196-11ed-9ff0-275813261164"))
    assert "Aufstellungsbeschlußdatum" in detail["dates"]


def test_page_without_date_rows_is_fine():
    assert parse_detail(fixture_detail_html(NO_DATES))["dates"] == {}


def test_every_download_is_read_once():
    """Several downloads sit in a nested table; reading its rows twice would double them."""
    detail = parse_detail(fixture_detail_html(PLAN))
    urls = [d["url"] for d in detail["documents"]]
    assert len(urls) == 6 and len(set(urls)) == 6


def test_the_authority_block_with_its_contact_data_is_not_read():
    for path in glob.glob(str(FIXTURES / "detail" / "*.html")):
        text = json.dumps(parse_detail(open(path, encoding="utf-8").read()))
        assert "@" not in text and "Zuständige Stelle" not in text


def test_unknown_plan_page_raises_although_the_portal_answers_200():
    with pytest.raises(SourceError, match="no plan table"):
        parse_detail((FIXTURES / "not_found.html").read_text(encoding="utf-8"))


@responses.activate
def test_fetch_detail_asks_for_the_right_page():
    serve_portal()
    detail = fetch_detail(quick_client(), record_by_id(PLAN))
    assert "Inkrafttretensdatum" in detail["dates"]
    assert "type=bplan" in responses.calls[0].request.url and PLAN in responses.calls[0].request.url
