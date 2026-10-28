-- Staging table for the harvested planning records. Safe to run more than once.
--   psql -f sql/01_schema.sql      or      harvest --init-db

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS iris_staging;

CREATE TABLE IF NOT EXISTS iris_staging.signal_ingest (
    id             bigserial PRIMARY KEY,

    -- identity: the same plan always has the same (source, source_id), which is what
    -- makes loads repeatable
    source         text NOT NULL,                 -- 'bplan_mv'
    source_id      text NOT NULL,                 -- the portal's own id (x_gmlid)

    signal_type    text NOT NULL
                   CHECK (signal_type IN ('grid_landing', 'solar_acreage', 'peatland_restoration')),
    matched_terms  text[] NOT NULL DEFAULT '{}',  -- the words of the title that decided the signal

    title          text NOT NULL,
    source_url     text,                          -- the plan's page on the portal
    document_urls  text[] NOT NULL DEFAULT '{}',  -- links only, no files are downloaded
    published_at   timestamptz,                   -- STAND-IN, see docs/data_dictionary.md
    legal_status   text,                          -- e.g. 'In Kraft getreten', 'Entwurf'
    plan_type      text,                          -- e.g. 'Bebauungsplan'
    municipality   text,
    district       text,

    -- location: a point in ETRS89 / UTM 32N (EPSG:25832), metres
    geom           geometry(Point, 25832),
    location_status text NOT NULL CHECK (location_status IN ('resolved', 'unresolved')),

    content_hash   text NOT NULL,                 -- fingerprint of the content, detects changes
    raw_payload    jsonb NOT NULL,                -- the record as received + detail page + decisions

    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (source, source_id),
    -- a row has a point exactly when its location is resolved
    CHECK ((geom IS NOT NULL) = (location_status = 'resolved'))
);

CREATE INDEX IF NOT EXISTS ix_signal_ingest_geom ON iris_staging.signal_ingest USING gist (geom);
