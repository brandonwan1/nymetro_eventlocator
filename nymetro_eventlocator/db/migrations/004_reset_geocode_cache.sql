-- Geocoding now ignores kinds of facility ("tennis courts") when checking a match, and retries a venue as the place
-- it is part of ("X Park Tennis Courts" -> "X Park"). Cached answers were checked with the older rule and store no
-- label to re-check, so drop them; each place is looked up again on the next run (max 60 lookups per run).
DELETE FROM geocode_cache;
