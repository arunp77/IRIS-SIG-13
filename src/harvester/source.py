"""The portal: finding candidate records and reading their detail pages.

The brief names https://b-plan-mv.de, which no longer exists (docs/source_notes.md). Its
successor, the Bau- und Planungsportal M-V, has two things we use:

  1. a JSON search service (documented by the portal for third parties) that lists records
  2. an HTML detail page per plan, the only place with dates and document titles (the
     "HTML pattern" of the brief)
"""

import json
import re
from datetime import date
from urllib.parse import urlencode

from bs4 import BeautifulSoup

SEARCH_URL = "https://www.gaia-mv.de/geoportalsearch/_ajax/search/"
DETAIL_URL = "https://www.bauportal-mv.de/bauportal/Uebersicht/Details"

# The row with the responsible authority is contact data, so it is not read.
AUTHORITY_LABEL = "Zuständige Stelle"


class SourceError(Exception):
    """The portal answered, but not with something usable."""


def first(value):
    """The portal wraps most values in one-element lists."""
    if isinstance(value, list):
        value = value[0] if value else None
    text = str(value).strip() if value is not None else ""
    return text or None


def detail_url(record):
    """Address of a record's detail page (the portal's own _resourceURL is redirected)."""
    kind = first(record.get("_resourceClass"))
    return f"{DETAIL_URL}?{urlencode({'type': kind, 'id': first(record.get('x_gmlid'))})}" if kind else None


# ---------------------------------------------------------------- search


def search(client, term, page_size=100):
    """Yield every record found for one search term.

    `total`, `offset` and `n` count result groups (several documents of one plan), not
    records, so the next offset is the number of groups received. Paging stops at the end
    or at an empty page.
    """
    offset = 0
    while True:
        params = {"type": "Bauleitplan", "q": term, "offset": offset, "n": page_size, "crs": "EPSG:25832"}
        data = _decode(client.get(SEARCH_URL, params))
        groups = data.get("results", [])
        if not isinstance(groups, list):
            raise SourceError("'results' is not a list")
        for group in groups:
            yield from group
        offset += len(groups)
        if not groups or offset >= int(data.get("total", 0)):
            return


def _decode(body):
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise SourceError(f"search answer is not JSON: {body[:60]!r}") from exc
    if not isinstance(data, dict) or data.get("success") is not True:
        raise SourceError(f"search answer is not a success: {str(data)[:80]}")
    return data


def collect(client, search_terms, page_size=100):
    """Search every term of every signal and merge the records by portal id.

    Returns (candidates, records_seen, problems). A term that fails is recorded in
    `problems`, and the other terms still run.
    """
    candidates, problems, seen = {}, [], 0
    for signal, terms in search_terms.items():
        for term in terms:
            try:
                for record in search(client, term, page_size):
                    seen += 1
                    record_id = first(record.get("x_gmlid"))
                    if record_id is None:
                        problems.append({"term": term, "error": "record has no x_gmlid"})
                        continue
                    entry = candidates.setdefault(record_id, {"record": record, "found_by": []})
                    if [signal, term] not in entry["found_by"]:
                        entry["found_by"].append([signal, term])
            except Exception as exc:  # FetchError, SourceError: note it, go on with the next term
                problems.append({"term": term, "error": str(exc)})
    return candidates, seen, problems


# ---------------------------------------------------------------- detail page


def fetch_detail(client, record):
    kind, record_id = first(record.get("_resourceClass")), first(record.get("x_gmlid"))
    if not kind:
        raise SourceError("record has no _resourceClass, detail page address unknown")
    return parse_detail(client.get(DETAIL_URL, {"type": kind, "id": record_id}))


def parse_detail(html):
    """Read a detail page: a table of label / value rows.

    Returns {"fields": {label: text}, "dates": {label: "YYYY-MM-DD"}, "documents": [{title, url, date}]}.
    The rows differ from plan to plan (the date rows depend on the legal stage), so every row is
    optional. A plan without a plan table, such as the portal's "Keinen Plan gefunden!" page,
    raises SourceError: the portal answers HTTP 200 for unknown ids.
    """
    table = BeautifulSoup(html, "html.parser").select_one("table.detailsTable")
    if table is None:
        raise SourceError("page has no plan table")
    detail = {"fields": {}, "dates": {}, "documents": []}
    # Only rows of this table: several downloads sit in a nested table inside one cell, and
    # reading its rows again here would count every link twice.
    for row in (tr for tr in table.find_all("tr") if tr.find_parent("table") is table):
        th, td = row.find("th"), row.find("td")
        label = _clean(th.get_text(" ")) if th else None
        if label == AUTHORITY_LABEL:
            continue
        links = row.find_all("a", href=True)
        for link in links:
            title = _clean(link.get_text(" "))
            detail["documents"].append({"title": title, "url": link["href"].strip(), "date": _date_in(title)})
        if label and td is not None and not links:
            text = _clean(td.get_text(" "))
            detail["fields"][label] = text
            if "datum" in label.lower() and _date_in(text):
                detail["dates"][label] = _date_in(text)
    return detail


def _clean(text):
    return " ".join(text.split())


def _date_in(text):
    """First valid dd.mm.yyyy in a text, as 'YYYY-MM-DD', else None."""
    for day, month, year in re.findall(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text):
        try:
            return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue  # e.g. 31.02.2026
    return None
