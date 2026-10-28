# Quality and error report

## What was run

- **Full run.** The portal answers (786 of them: 31 searches and the detail page of every one of the 755 kept plans) were
  fetched from the live portal on 27 October 2026 and kept in `data/raw/` (committed as `portal_answers_2026-10-27.tar.gz`
  with a checksum manifest; `python tasks.py raw` unpacks them). The harvester then ran over these files, offline (`python tasks.py reproduce`),
  into an empty PostGIS database, and a second time to test re-runs. Output: `data/sample/run_report.json`, `data/sample/validation_output.txt`.
- **Live check of this code, 28 October 2026.** `--signal grid_landing --signal peatland_restoration --dry-run` against the
  live portal with an empty cache: 10 requests in 19 seconds (the pause is kept), 52 records returned, none matching a
  rule; every answer was written to `data/raw/`.
- **Tests and demo:** 109 tests, and the offline demo on saved real answers (`python tasks.py test`, `python tasks.py demo`).

## Result

| | |
|---|---:|
| Records returned by the searches (repeats included) | 1,593 |
| Distinct candidate records | **802** |
| Classified and loaded | **755** (all `solar_acreage`) |
| Matched no rule | 47 |
| Rejected for invalid coordinates | 0 |
| Skipped for a failed detail page | 0 |
| Failed database writes | 0 |
| Second run | `0 inserted, 0 updated, 755 unchanged` |

**The 47 that matched no rule** are listed with their titles in the report. 40 contain `Moor` as a place or field name
(*Krummes Moor*, *Nördlich im Moor*), 4 are plans next to a substation (`Umspannwerk`), 1 is a ferry-port extension
(`Offshore Süd`), and 2 contain none of these words (the portal found them through other fields). None is a grid landing
point or a peatland restoration plan, so **grid landing: 0 and peatland restoration: 0** is the honest result for this portal.

## Completeness of the 755 rows

| Field | Missing | Note |
|-------|--------:|------|
| point (`geom`) | 0 | every row is `resolved` |
| municipality, legal status, plan type | 0 | |
| `published_at` (stand-in) | 101 (13 %) | the detail page has no usable date, mostly plans in the draft stage |
| document links | 98 (13 %) | the municipality has not uploaded documents for these plans |
| district | 1 | one plan has an empty "responsible authority" block |

Which portal date stood in for the publication date: start of public display 319 rows, entry into force 223, council
decision 111, a date in a download title 1, none 101. One plan has an entry-into-force date of 31 December 1989, which is
implausible for a photovoltaic plan; it is stored as given and listed by the validation query "dates to review".
Plans by stage: in force 319, in procedure 427, ended 9.

## Geographic check

All 755 points lie inside the outline of Mecklenburg-Vorpommern (`data/reference/mv_outline.geojson`, simplified): 744
strictly inside, 11 within 3 km (the outline is simplified at the coast), none further out (`qa/quicklook.py`). The map
is `docs/figures/quicklook.png`.

## Validation queries

`sql/02_validation.sql` has 8 checks (empty result = pass) and 6 info queries. On the loaded data all 8 checks return no
rows: no duplicate keys, no point outside EPSG:25832 or MV, no invalid geometry, no row where point and status disagree,
no missing title, link or matched terms, no row without a hash and raw record, no timestamps out of order.

## Errors and failure handling

None of these happened in the full run (0 errors); each is covered by a test.

| Situation | Behaviour | Test |
|-----------|-----------|------|
| Timeout, connection error, HTTP 429/5xx | retried with waits of 2, 4, 8 s, `Retry-After` respected; then a failed search term or detail page is reported | `test_client.py` |
| HTTP 404 | not retried | `test_client.py` |
| A search term fails | recorded under `search_problems`; the other terms still run | `test_source.py` |
| Malformed or unusable search answer, `success: false` | reported as a failed term | `test_source.py` |
| Empty answer, term without matches, empty page before `total` | valid, no error; paging stops | `test_source.py` |
| Same plan from several terms or twice in one answer | merged by portal id | `test_source.py`, `test_load.py` |
| Detail page missing, or the "plan not found" page (HTTP 200) | plan skipped and reported; the stored row stays as it was | `test_source.py`, `test_run.py` |
| Centroid in the usual x,y order, in degrees, outside MV | record **rejected** with the reason, never loaded | `test_transform.py` |
| No centroid | stored without a point (`unresolved`), warning | `test_transform.py`, `test_load.py` |
| No date on the detail page | `published_at` empty, warning | `test_transform.py` |
| The database refuses a row | listed under `load.failed`; the other rows still load; exit code 2 | `test_load.py` |
| A repeated run | nothing is inserted or changed; a changed plan is updated in place | `test_load.py`, `test_run.py` |

## Limitations and open questions

See "Known limitations" in the README. Questions for the data's users:

1. Is another source expected for grid landing and peatland restoration? The same GeoPortal lists related datasets that
   are not planning records.
2. Which date should count as the publication date? The portal has none.
3. Should plans in the draft stage (no public date yet) be loaded, or only plans made public?
4. Does `iris_staging.signal_ingest` already have a defined schema? The brief names the table but no columns, so the
   columns in `docs/data_dictionary.md` are my design.
