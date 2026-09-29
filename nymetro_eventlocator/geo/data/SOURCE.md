Boundary data, downloaded through the eventscout polite gateway and simplified with shapely.

- nyc_boroughs.geojson: NYC Open Data, Borough Boundaries (gthc-hcne), water excluded. Tolerance 0.0001°. (2026-09-23)
- nyc_neighborhoods.geojson: NYC Open Data, 2020 Neighborhood Tabulation Areas (9nt8-h7nd). Tolerance 0.00005°. (2026-09-23)
- nyc_metro_counties.geojson: the 17 non-NYC counties of the Census "New York-Newark-Jersey City, NY-NJ" Metro Area
  (CBSA 35620): US Census TIGERweb CBSA layer 3 + State_County layer 1, counties whose interior point is inside the
  metro boundary. Tolerance 0.0003°. (2026-09-26)
- nyc_metro_places.geojson: New Jersey municipalities (TIGERweb county subdivisions, layer 1; most NJ towns are
  "townships", which the Census files as county subdivisions, not places) + New York incorporated places and CDPs
  (TIGERweb Places layers 4 and 5) inside that metro area,
  excluding New York city itself, with "CDP/village/city/town/borough/township" suffixes stripped. Used as the
  "neighborhood" outside NYC. Tolerance 0.0002°. (2026-09-26)
