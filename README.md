# Forecast Compare (public)

Forecasts for several south-east Queensland and NSW locations from several Australian weather sites,
side by side. Pick the location from the dropdown at the top of the page.

- Site: https://d3skj3t.github.io/oxley-forecast-public (add `?loc=<id>` to open on a location)
- Locations: `locations.json` (K'gari, Byron Bay, Maroochydore, Oxley, Maleny, Bribie Island North,
  Sydney, Moreton Island, Moogerah, Coolangatta), with each site's page or coordinates for each one.
- `scraper/scrape.py` writes `data/<id>.json`. GitHub Actions runs it every 30 minutes; each run updates
  only the one or two locations that are due, so each location refreshes about every 3 hours without
  hitting the sites all at once. A push to the scraper or the location list refreshes everything once.
- Sources: Bureau of Meteorology (forecast and nearest-station observations), Weatherzone, WillyWeather,
  Elders Weather, Weather Underground and Windy. Where a site has no page for the exact place it uses a
  nearby one, which the app names (for example Eurong for K'gari).

This is a separate copy of a private app. Figures belong to the sites they come from; for warnings
and safety decisions, follow the Bureau of Meteorology.
