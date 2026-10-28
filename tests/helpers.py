"""A fake portal for the tests and the offline demo, built from real saved answers.

tests/fixtures/portal/ holds real answers of the portal (captured on 2026-10-27):

    search/<term>.json    the records the search returned for one term (cut down to the records
                          the tests need; the real answers list hundreds of plans)
    detail/<id>.html      the detail page of one plan, unmodified
    not_found.html        what the portal answers for an unknown plan id (HTTP 200, no plan table)

`serve_portal` makes `requests` answer the portal's two services from these files, the way the real
portal does, so nothing needs network access.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import responses

from harvester.client import Client
from harvester.source import DETAIL_URL, SEARCH_URL

FIXTURES = Path(__file__).parent / "fixtures" / "portal"


def slug(term):
    return re.sub(r"[^a-z0-9]+", "-", term.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")).strip(
        "-"
    )


def quick_client(**options):
    """A client without cache and without pauses."""
    return Client("tester@example.org", cache_dir=None, min_interval=0, sleep=lambda s: None, **options)


def serve_portal(missing_details=()):
    """Register the fake portal with `responses` (use inside a @responses.activate test).

    A search term without a file gets an empty answer, like a real query with no match. A detail
    page that is not in the folder, or listed in `missing_details`, gets the portal's own
    "plan not found" page.
    """

    def search(request):
        query = parse_qs(urlparse(request.url).query)
        path = FIXTURES / "search" / f"{slug(query['q'][0])}.json"
        groups = json.loads(path.read_text(encoding="utf-8"))["results"] if path.exists() else []
        offset, size = int(query["offset"][0]), int(query["n"][0])
        body = {
            "success": True,
            "first": offset,
            "start": offset,
            "hits": len(groups),
            "total": len(groups),
            "results": groups[offset : offset + size],
        }
        return 200, {}, json.dumps(body)

    def detail(request):
        plan_id = parse_qs(urlparse(request.url).query)["id"][0]
        path = FIXTURES / "detail" / f"{plan_id}.html"
        if plan_id in missing_details or not path.exists():
            return 200, {}, (FIXTURES / "not_found.html").read_text(encoding="utf-8")
        return 200, {}, path.read_text(encoding="utf-8")

    responses.add_callback(responses.GET, SEARCH_URL, callback=search)
    responses.add_callback(responses.GET, DETAIL_URL, callback=detail)


def fixture_records(term):
    """The records of a search fixture, flattened."""
    groups = json.loads((FIXTURES / "search" / f"{slug(term)}.json").read_text(encoding="utf-8"))["results"]
    return [record for group in groups for record in group]


def fixture_detail_html(plan_id):
    return (FIXTURES / "detail" / f"{plan_id}.html").read_text(encoding="utf-8")


def record_by_id(plan_id):
    """The search record of one plan, found in any search fixture."""
    for path in (FIXTURES / "search").glob("*.json"):
        for group in json.loads(path.read_text(encoding="utf-8"))["results"]:
            for record in group:
                if record["x_gmlid"][0] == plan_id:
                    return record
    raise KeyError(plan_id)
