# Data dictionary: `iris_staging.signal_ingest`

One row per planning record. Coordinate reference system of `geom`: **EPSG:25832** (ETRS89 / UTM zone 32N), unit metres;
in MV easting is about 600 000 to 860 000 and northing about 5 900 000 to 6 100 000. Points are rounded to millimetres.

| Column | Type | Source | Meaning |
|--------|------|--------|---------|
| `id` | bigserial | generated | surrogate key |
| `source` | text | constant | `bplan_mv` |
| `source_id` | text | `x_gmlid` | the portal's id of the plan; `(source, source_id)` is unique and is what makes loads repeatable |
| `signal_type` | text | classifier | `grid_landing`, `solar_acreage` or `peatland_restoration` (checked by the database) |
| `matched_terms` | text[] | classifier | the words of the title that decided the signal |
| `title` | text | `title` | with runs of spaces collapsed |
| `source_url` | text | `_resourceClass`, `x_gmlid` | the plan's detail page on the portal |
| `document_urls` | text[] | `x_dokumentURL`, detail page | links to the plan's documents; repeats removed, spaces encoded as `%20`. Links only, no download |
| `published_at` | timestamptz | detail page | **stand-in** for a publication date, see below; midnight Europe/Berlin; empty if the page has no usable date |
| `legal_status` | text | `x_rechtsstandtext` | stage, e.g. *In Kraft getreten*, *Entwurf*, *Aufstellungsbeschluss* |
| `plan_type` | text | `x_planarthauptText` | e.g. *Bebauungsplan*, *FPlan* |
| `municipality`, `district` | text | `x_gemeinde`, `x_landkreis` | as given |
| `geom` | geometry(Point, 25832) | `x_centroid` | centroid of the plan area; swapped from northing,easting; empty if unresolved |
| `location_status` | text | derived | `resolved` (has a point) or `unresolved` (the portal gave no centroid). A wrong centroid is not loaded at all |
| `content_hash` | text | derived | SHA-256 of the row's content; a change of the hash means the plan changed |
| `raw_payload` | jsonb | search + detail page | the search record exactly as received, the parsed detail page, which searches found it, and which date was used |
| `first_seen_at`, `last_seen_at`, `updated_at` | timestamptz | run time | first inserted / last seen in a run / last time the content changed |

Database rules: unique `(source, source_id)`; only the three signal types; only the two location statuses; a point if
and only if the status is `resolved`.

## The publication date

`published_at` is the first of these that the detail page has: (1) `Auslegungsstartdatum`, start of the public display;
(2) `Inkrafttretensdatum`, entry into force; (3) `Aufstellungsbeschlussdatum`, council decision; (4) the date in the title
of a download starting with "Aufstellungsbeschluss". The label used is stored in `raw_payload.published_at_basis`.
The first two are dates at which a plan becomes visible to the public; the council decision (3 and 4) comes before any
publication, so for those rows the column is not a publication date. Whether that is acceptable is a question for the
data's users.

## Exports

`qa/quicklook.py` writes the points as GeoJSON in EPSG:4326 (WGS84, as the GeoJSON standard requires; QGIS reads it
directly) and as a CSV with `easting_25832` and `northing_25832`.
