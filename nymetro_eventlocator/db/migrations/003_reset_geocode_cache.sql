-- 2026-09-24: geocoder now cleans queries and sanity-checks matches (a Bryant Park event had been placed in
-- Marine Park, Brooklyn). Re-resolve everything under the new rules.
DELETE FROM geocode_cache;
