CREATE TABLE events (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    start         TEXT NOT NULL,          -- ISO 8601 with offset
    "end"         TEXT,
    url           TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    venue_name    TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    lat           REAL,
    lon           REAL,
    borough       TEXT NOT NULL DEFAULT '',
    neighborhood  TEXT NOT NULL DEFAULT '',
    is_online     INTEGER NOT NULL DEFAULT 0,
    price         TEXT NOT NULL DEFAULT '',
    image         TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT '',
    grp           TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '[]',
    extra         TEXT NOT NULL DEFAULT '{}',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE INDEX events_start ON events(start);
CREATE INDEX events_grp ON events(grp);

CREATE TABLE rejections (
    event_id    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    start       TEXT,
    venue_name  TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    times_seen  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (event_id, reason)
);
CREATE INDEX rejections_last_seen ON rejections(last_seen);

CREATE TABLE notifications (
    event_id  TEXT NOT NULL,
    route_id  TEXT NOT NULL,
    sent_at   TEXT NOT NULL,
    PRIMARY KEY (event_id, route_id)
);

CREATE TABLE geocode_cache (
    query       TEXT PRIMARY KEY,
    lat         REAL,
    lon         REAL,                     -- NULL lat/lon = looked up, not found
    fetched_at  TEXT NOT NULL
);

CREATE TABLE robots_cache (
    origin      TEXT PRIMARY KEY,         -- scheme://host[:port]
    status      INTEGER NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    fetched_at  TEXT NOT NULL
);

CREATE TABLE http_cache (
    url            TEXT PRIMARY KEY,
    etag           TEXT,
    last_modified  TEXT,
    body           BLOB,
    fetched_at     TEXT NOT NULL
);

CREATE TABLE source_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    fetched      INTEGER NOT NULL DEFAULT 0,
    parsed       INTEGER NOT NULL DEFAULT 0,
    skipped_robots INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);
