-- Location details read from an event's own page (e.g. Meetup event pages), so each page is fetched once a week at most.
CREATE TABLE detail_cache (
    url         TEXT PRIMARY KEY,
    venue_name  TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    lat         REAL,
    lon         REAL,
    is_online   INTEGER NOT NULL DEFAULT 0,
    fetched_at  TEXT NOT NULL
);
