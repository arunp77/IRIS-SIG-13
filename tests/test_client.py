"""The HTTP client: retries, pauses, contact address and the on-disk cache."""

import pytest
import requests
import responses

from harvester.client import Client, FetchError

URL = "https://example.org/data"


def client(**options):
    sleeps = []
    options = {"cache_dir": None, "min_interval": 0, **options}
    return Client("tester@example.org", sleep=sleeps.append, **options), sleeps


def test_contact_address_is_required():
    with pytest.raises(ValueError):
        Client("  ")


@responses.activate
def test_user_agent_names_the_tool_and_the_contact():
    responses.add(responses.GET, URL, body="ok")
    client()[0].get(URL)
    assert "tester@example.org" in responses.calls[0].request.headers["User-Agent"]


@responses.activate
def test_temporary_errors_are_retried_with_growing_waits():
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, body="fine")
    c, sleeps = client()
    assert c.get(URL) == "fine"
    assert sleeps == [2.0, 4.0]
    assert len(responses.calls) == 3


@responses.activate
def test_gives_up_after_all_attempts():
    responses.add(responses.GET, URL, status=500)
    c, _ = client(attempts=3)
    with pytest.raises(FetchError, match="gave up after 3 attempts"):
        c.get(URL)
    assert len(responses.calls) == 3


@responses.activate
def test_not_found_is_not_retried():
    responses.add(responses.GET, URL, status=404)
    with pytest.raises(FetchError, match="HTTP 404"):
        client()[0].get(URL)
    assert len(responses.calls) == 1


@responses.activate
def test_timeout_is_retried():
    responses.add(responses.GET, URL, body=requests.Timeout())
    responses.add(responses.GET, URL, body="back again")
    assert client()[0].get(URL) == "back again"


@responses.activate
def test_retry_after_header_overrides_the_wait():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "7"})
    responses.add(responses.GET, URL, body="ok")
    c, sleeps = client()
    c.get(URL)
    assert sleeps == [7.0]


@responses.activate
def test_pause_between_two_requests():
    responses.add(responses.GET, URL, body="ok")
    c, sleeps = client(min_interval=2.0)
    c.get(URL)
    c.get(URL)
    assert len(sleeps) == 1 and 1.5 < sleeps[0] <= 2.0


@responses.activate
def test_umlauts_are_decoded_as_utf8():
    responses.add(responses.GET, URL, body="Wiedervernässung".encode("utf-8"), content_type="text/html")
    assert client()[0].get(URL) == "Wiedervernässung"


@responses.activate
def test_second_request_comes_from_the_cache(tmp_path):
    responses.add(responses.GET, URL, body="once")
    first, _ = client(cache_dir=tmp_path)
    assert first.get(URL, {"q": "a"}) == "once"
    again, _ = client(cache_dir=tmp_path)  # a new client, the cache is on disk
    assert again.get(URL, {"q": "a"}) == "once"
    assert len(responses.calls) == 1 and again.cache_hits == 1


@responses.activate
def test_old_or_damaged_cache_entries_are_ignored(tmp_path):
    responses.add(responses.GET, URL, body="fresh")
    old, _ = client(cache_dir=tmp_path, cache_ttl_hours=0)
    old.get(URL)
    assert old.get(URL) == "fresh" and len(responses.calls) == 2  # ttl 0: always asks again
    for file in tmp_path.glob("*.json"):
        file.write_text("{ not json", encoding="utf-8")
    assert client(cache_dir=tmp_path)[0].get(URL) == "fresh"  # damaged file: asks again


@responses.activate
def test_offline_mode_uses_the_cache_whatever_its_age_and_never_asks_the_portal(tmp_path):
    responses.add(responses.GET, URL, body="saved")
    client(cache_dir=tmp_path)[0].get(URL)  # fills the cache
    offline, _ = client(cache_dir=tmp_path, cache_ttl_hours=0, offline=True)  # ttl 0: online it would ask again
    assert offline.get(URL) == "saved" and len(responses.calls) == 1


@responses.activate
def test_offline_mode_refuses_what_is_not_in_the_cache(tmp_path):
    with pytest.raises(FetchError, match="offline: not in the cache"):
        client(cache_dir=tmp_path, offline=True)[0].get(URL)
    assert len(responses.calls) == 0
