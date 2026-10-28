-- Validation queries for iris_staging.signal_ingest.
--   psql -f sql/02_validation.sql          (or: make validate)
--
-- A "check" lists rows that break a rule: an empty result means it passes.
-- An "info" query prints a summary to read.

\echo '== check: duplicate source ids (the unique key makes this impossible; it shows that it holds)'
SELECT source, source_id, count(*) AS copies
FROM iris_staging.signal_ingest GROUP BY 1, 2 HAVING count(*) > 1;

\echo '== check: points that are not in EPSG:25832'
SELECT id, source_id, ST_SRID(geom) AS srid
FROM iris_staging.signal_ingest WHERE geom IS NOT NULL AND ST_SRID(geom) <> 25832;

\echo '== check: geometry that is not a valid point'
SELECT id, source_id, GeometryType(geom) AS type
FROM iris_staging.signal_ingest WHERE geom IS NOT NULL AND (GeometryType(geom) <> 'POINT' OR NOT ST_IsValid(geom));

\echo '== check: point and location status disagree'
SELECT id, source_id, location_status, geom IS NOT NULL AS has_point
FROM iris_staging.signal_ingest WHERE (geom IS NOT NULL) <> (location_status = 'resolved');

\echo '== check: points outside Mecklenburg-Vorpommern (box 10.5-14.5 E, 53.0-54.8 N)'
SELECT id, source_id, title
FROM iris_staging.signal_ingest
WHERE geom IS NOT NULL
  AND NOT ST_Within(ST_Transform(geom, 4326), ST_MakeEnvelope(10.5, 53.0, 14.5, 54.8, 4326));

\echo '== check: required fields missing (title, source link, matched terms)'
SELECT id, source_id FROM iris_staging.signal_ingest
WHERE btrim(title) = '' OR source_url IS NULL OR cardinality(matched_terms) = 0;

\echo '== check: rows that cannot be traced (hash, raw record)'
SELECT id, source_id FROM iris_staging.signal_ingest
WHERE content_hash !~ '^[0-9a-f]{64}$' OR NOT (raw_payload ? 'search_record')
   OR raw_payload #>> '{search_record,x_gmlid}' IS NULL;

\echo '== check: timestamps out of order'
SELECT id, source_id FROM iris_staging.signal_ingest
WHERE NOT (first_seen_at <= updated_at AND updated_at <= last_seen_at);

\echo '== info: rows per signal and location status'
SELECT signal_type, location_status, count(*) AS rows
FROM iris_staging.signal_ingest GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== info: missing optional fields (the portal has no publication date: published_at is a stand-in)'
SELECT count(*) AS rows,
       count(*) FILTER (WHERE published_at IS NULL)           AS no_published_at,
       count(*) FILTER (WHERE cardinality(document_urls) = 0) AS no_documents,
       count(*) FILTER (WHERE municipality IS NULL)           AS no_municipality,
       count(*) FILTER (WHERE geom IS NULL)                   AS no_point
FROM iris_staging.signal_ingest;

\echo '== info: which portal date stood in for the publication date'
SELECT coalesce(raw_payload ->> 'published_at_basis', '(none)') AS basis, count(*) AS rows
FROM iris_staging.signal_ingest GROUP BY 1 ORDER BY 2 DESC;

\echo '== info: dates to review (before 2000 or more than a year ahead)'
SELECT source_id, left(title, 60) AS title, published_at::date
FROM iris_staging.signal_ingest
WHERE published_at < '2000-01-01' OR published_at > now() + interval '1 year' ORDER BY published_at;

\echo '== info: what the latest run did (rows seen again, changed, new)'
WITH latest AS (SELECT max(last_seen_at) AS ts FROM iris_staging.signal_ingest)
SELECT count(*) FILTER (WHERE first_seen_at = ts)                                   AS inserted,
       count(*) FILTER (WHERE first_seen_at < ts AND updated_at = ts)               AS updated,
       count(*) FILTER (WHERE updated_at < ts AND last_seen_at = ts)                AS unchanged,
       count(*) FILTER (WHERE last_seen_at < ts)                                    AS not_seen_this_run
FROM iris_staging.signal_ingest, latest;
