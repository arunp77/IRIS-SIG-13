# Raw portal answers

Every answer of the portal is kept here, one JSON file per request:

    {"url": "...", "fetched_at": <unix time>, "body": "<the answer exactly as received>"}

The file name is the SHA-256 of the URL. This is the provenance of everything in the database: any row can be
traced to the requests that produced it. The harvester writes these files itself whenever it asks the portal
(a repeated run within 24 hours reads them instead of asking again).

| Tracked in git | What |
|----------------|------|
| `portal_answers_2026-10-27.tar.gz` | the 786 answers of the full run (31 searches and 755 detail pages), compressed, 1.2 MB |
| `MANIFEST.csv` | one line per answer: file, kind (search / detail), url, time, size, SHA-256 of the body |

| Not tracked | What |
|-------------|------|
| `*.json` | the unpacked answers. `python tasks.py raw` (also part of `setup`) unpacks them here and checks every file against the manifest |

With the answers unpacked, `python tasks.py reproduce` replays the full run **offline** (`harvest --offline`): 755 plans, and a second
run that changes nothing. To download fresh answers from the live portal instead, delete the `*.json` files and run `python tasks.py fetch` (30 minutes or
more; it needs your e-mail address in `.env`).
