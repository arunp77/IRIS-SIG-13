# Source notes

What I checked about the data source, and what it means for the code. Checks made on 27 and 28 October 2026.

## 1. The URL in the brief

`https://b-plan-mv.de` does not resolve: the local resolver returns no address and `dig b-plan-mv.de @8.8.8.8`
returns no answer. The brief allows for this, so I looked for the current portal.

## 2. The current portal

The state's **Bau- und Planungsportal M-V** (`https://bplan.geodaten-mv.de/bauportal/`, also served at
`https://www.bauportal-mv.de/bauportal/`) lists Bebauungspläne, vorhabenbezogene Bebauungspläne,
Flächennutzungspläne and Satzungen, including plans still in preparation. It has three layers:

| Layer | What it is | Used? |
|-------|-----------|-------|
| Listing pages (HTML) | empty shells; JavaScript fills them in | no, nothing to read |
| JSON search service `https://www.gaia-mv.de/geoportalsearch/_ajax/search/` | holds the records; documented by the portal for third parties | yes: finds candidates |
| Detail page per plan (HTML, `.../Uebersicht/Details?type=<class>&id=<x_gmlid>`) | server-rendered; the only place with dates and document links | yes: pattern "B (HTML)" of the brief |

Search request: `type=Bauleitplan&q=<term>&offset=<n>&n=<page size>&crs=EPSG:25832`.

## 3. Access and terms

- Only the documented interface and public detail pages are used. No access restriction is bypassed.
- `robots.txt`: 404 on `bauportal-mv.de`, a 503 "service unavailable" page on `gaia-mv.de`. So no crawl rules are
  published (this is not the same as an explicit permission).
- The portal states that the plans belong to the municipalities and that copying documents needs their consent, so
  **only metadata and links are stored; no document is downloaded.**
- The harvester sends a `User-Agent` with a contact address, waits 2 seconds between requests, retries temporary
  failures politely and caches every answer (`data/raw/`).

## 4. Findings that shape the code

1. **`total`, `hits`, `offset` and `n` count result groups, not records.** A group can hold several documents of one
   plan, so the next offset is the number of groups received. A plan can appear more than once.
2. **The search matches inside words.** `Moor` also finds `Broderstorf`-like names, so classification must be done by
   the harvester (`config/signals.yaml`).
3. **The centroid is `northing,easting`**, the reverse of the usual x,y order, already in EPSG:25832 (the `crs`
   parameter). The bounding box on the detail pages is in EPSG:25833, not 25832.
4. **No publication date exists.** The only date-like field of a search record is `_resourceIndexed`, the time the
   record was indexed. Detail pages have dates of legal steps (`Auslegungsstartdatum`, `Inkrafttretensdatum`,
   `Aufstellungsbeschlußdatum` ...), and which ones exist depends on the stage of the plan.
5. **An unknown plan id returns HTTP 200** with a page that has no plan table ("Keinen Plan gefunden!").
6. **Several downloads sit in a nested table** inside one cell; reading the inner rows again would count every link twice.
7. **The "responsible authority" block** holds contact data of the municipality. It is not read.

## 5. What the portal contains

| Signal | Found |
|--------|-------|
| Solar acreage | plentiful: 755 plans |
| Grid landing | none: the few `Umspannwerk` hits are plans next to a substation, `Offshore` is a ferry-port extension |
| Peatland restoration | none: every `Moor` hit is a place or field name |

Without the `type=Bauleitplan` filter the same search service also returns other GeoPortal content (dataset metadata
and map layers, for example an offshore cable layer and Moorschutz datasets). Those are map services and reports, not planning
proposals; I did not examine them.
