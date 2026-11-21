# Independent geographic verification design

**Status:** geometry checks implemented and synthetic-tested, but live geocoding
and trusted boundary-source integration are NOT activated.

## Verified vs claimed

- Provider address lookup confirms only that the supplier recognizes the
  configured city, street, house and supplies one or more queue identifiers.
- A district or historical quarter copied into POWER_CONFIG_JSON is a user
  claim. Its presence or nonempty string is NOT independent verification.
- An internet search snippet, map centroid or bounding box is NOT proof that
  the actual building lies inside a district.
- A location may be a municipal administrative district and simultaneously
  an informal or historical neighborhood. These are different datasets.
- A geocoder may know a building but lack the exact quarter boundary.

## Selected mechanism

A separate **one-time verification**, not a request every five minutes:

1. Parse the private address. Require the unique supplier lookup to succeed.
2. Use a licensed, documented geocoder to locate the exact building.
3. Match city, street and house separately; reject approximate addresses.
4. Obtain the actual geometry for municipal district and historical quarter
   from a legitimate GeoJSON provider (not only a center or bounding box).
5. Use geo_boundaries.membership to test the address coordinate against both
   polygons. Hole and border results are unverified.
6. Record the provenance, source object ID, data timestamp and result in a
   **private** checkpoint. Do not expose the location or raw geometry in
   public GitHub state or Actions logs.
7. Cache the result. Revalidate only after the address, selected locality,
   boundary-source version or a long freshness interval changes.
8. Never call a disputed quarter 'verified'. With no independent geometry,
   report geography as unverified and disable only narrowly targeted
   quarter/district messages that depend on unknown coverage.

The daily outage schedule comes directly from the verified supplier queue
and does not depend on geographic geocoding.

## External sources and licensing

- Use official municipal GIS GeoJSON where available. Administrative region
  and informal neighborhood data often have different coverage.
- A Ukrainian commercial geocoder offers both address features and historic
  district polygons, but requires API credentials and has licensing rules.
  Its published test key is for a limited duration; production free use has
  accessibility requirements. Do not assume a private Telegram group
  qualifies for free use.
- The public Nominatim OSM service has restricted usage and should not be
  used as the generic automated geocoding backend for many duplicated bots.
  Do not scrape public maps for their internal API.
- If no compliant and adequate source is available, a manual address
  assertion remains **declared**, never **independently verified**.

Documentation for licensed geographic APIs:
https://api.visicom.ua/uk/products/data-api/data-api-references/geocode
https://api.visicom.ua/uk/products/data-api/data-api-references/feature
https://api.visicom.ua/uk/price
https://api.visicom.ua/uk/docs/terms-of-free-use

**Release blocker:** live geocoding, API rights, suitable boundary geometries,
and private checkpoint storage have not been established. They require separate
acceptance tests before this mechanism may change production alert filtering.
