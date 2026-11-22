# Geography: minimum useful verification (no paid map API)

## Decision

The supplier's address directory is the source of truth for the house's
electricity queue. The bot does **not** run Google Maps, Google Geocoding,
OpenStreetMap Nominatim, Visicom or any other map API every five minutes.

- A unique house match against the official supplier directory is required
  for new queue assignments.
- The locally declared district and quarter are descriptive, **not**
  independent geographic certification.
- District and quarter matter only when a genuine official operational
  notice explicitly names those territories.
- If the relevant territory cannot be established confidently, omit that
  narrowly scoped warning; do not suppress confirmed address-based schedules.
- Never infer a quarter from a map pin, street centroid or bounding box.
- A quarter is a neighborhood/historical name, not necessarily a municipality
  administrative boundary.

The existing `coverage_for_house()` function already restricts matching to
named official announcements, and requires an explicit configured district or
quarter for corresponding notifications. The exact quarter string is optional.

## Optional future map verification

If an actual source begins posting quarter-specific alerts that matter, add
**one-time, cached verification** on setup only. Prefer licensed, genuinely free
municipal GIS/open geographic data that contains actual polygon boundaries.
Do not require a paid API or a billing-enabled Google project.

Public OSM Nominatim may be used only in compliance with its usage policy; it
is not an acceptable unbounded backend for automatically cloned monitoring bots.

## Current configuration

The currently configured address and geography were supplied by the operator
of this installation. Geographic values must not be logged or written into
public JSON checkpoints.

The preliminary GeoJSON polygon evaluator was removed from the production
branch because it had no live boundary data, no licensed source, and no
relevant recurring use case. This reduces maintenance and false confidence.

References:
https://developers.google.com/maps/documentation/geocoding/usage-and-billing
https://operations.osmfoundation.org/policies/nominatim/
