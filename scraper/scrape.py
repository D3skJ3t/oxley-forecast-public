"""Scrape each forecast site for Oxley QLD 4075 and write data/forecast.json.

Shape: {generated, sources: [{source, name, url, fetched, days: [{date, tmax, tmin,
rain_min, rain_max, rain_text, chance, text}]}], observations: {...},
errors: {source: message}}.
A source that fails keeps its last good entry (its "fetched" time shows how old it is).
"""
import json, os, re, sys, time, traceback
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

OUT = "data/forecast.json"
BNE = timezone(timedelta(hours=10))  # Queensland has no daylight saving
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-AU,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}

URLS = {
    "bom": "https://api.weather.bom.gov.au/v1/locations/r7hg1p/forecasts/daily",
    "willyweather": "https://www.willyweather.com.au/qld/brisbane/oxley.html",
    "willyweather_rain": "https://rainfall.willyweather.com.au/qld/brisbane/oxley.html",
    "elders": "https://eldersweather.com.au/forecast/qld/oxley",
    "weatherzone": "https://www.weatherzone.com.au/qld/brisbane/oxley",
    "wunderground": "https://www.wunderground.com/forecast/au/oxley/-27.556,152.975",
    "windy": "https://www.windy.com/-27.556/152.975",
    "bom_obs": "https://www.bom.gov.au/fwo/IDQ60901/IDQ60901.94575.json",
}
LAT, LON = -27.556, 152.975


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_bne():
    return datetime.now(BNE).date()


def num(v):
    """Number or None; whole floats become ints so the JSON reads cleanly."""
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace("%", "").strip())
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def rain_range(text):
    """'1-5mm', '1–5 mm', '5 - 10 mm' -> (1, 5); '< 1mm' -> (0, 1)."""
    if not text:
        return None, None
    t = text.replace("–", "-").replace("\xa0", " ")
    vals = [num(n) for n in re.findall(r"\d+(?:\.\d+)?", t)]
    if "<" in t and vals:
        return 0, vals[0]
    if len(vals) >= 2:
        return vals[0], vals[1]
    if len(vals) == 1:
        return vals[0], vals[0]
    return None, None


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def day(date, tmax=None, tmin=None, rain_text=None, rain_min=None, rain_max=None, chance=None, text=None):
    if rain_text is not None and rain_min is None and rain_max is None:
        rain_min, rain_max = rain_range(rain_text)
    return {"date": date, "tmax": num(tmax), "tmin": num(tmin), "rain_min": num(rain_min),
            "rain_max": num(rain_max), "rain_text": clean(rain_text) or None,
            "chance": num(chance), "text": clean(text) or None}


def get(url, headers=None, **kw):
    r = requests.get(url, headers={**HEADERS, **(headers or {})}, timeout=30, **kw)
    r.raise_for_status()
    return r


# ---- Bureau of Meteorology: JSON feed used by its own app ----
def scrape_bom():
    data = get(URLS["bom"]).json()["data"]
    days = []
    for d in data:
        # Dates are local midnight expressed in UTC (e.g. 2026-10-26T14:00:00Z is 27 Oct)
        local = datetime.fromisoformat(d["date"].replace("Z", "+00:00")).astimezone(BNE).date()
        amt = (d.get("rain") or {}).get("amount") or {}
        lo, hi = amt.get("min"), amt.get("max")
        txt = None
        if lo is not None or hi is not None:
            txt = f"{lo or 0}-{hi} mm" if hi is not None else f"{lo} mm"
        days.append(day(local.isoformat(), d.get("temp_max"), d.get("temp_min"), txt, lo, hi,
                        (d.get("rain") or {}).get("chance"), d.get("short_text")))
    return {"source": "bom", "name": "BOM official", "url": "https://www.bom.gov.au/qld/forecasts/brisbane.shtml", "days": days}


# ---- WillyWeather: plain HTML, temperatures on the main page, rain on the rainfall page ----
def scrape_willyweather():
    main = BeautifulSoup(get(URLS["willyweather"]).text, "html.parser")
    rain = BeautifulSoup(get(URLS["willyweather_rain"]).text, "html.parser")
    chance = {}
    for li in rain.select("section.forecast li.day"):
        t = li.find("time")
        if not t:
            continue
        cv, ca = li.select_one(".chance-value"), li.select_one(".chance-amount")
        chance[t["datetime"]] = (cv.get_text() if cv else None, ca.get_text() if ca else None)
    days = []
    for li in main.select("section.forecast li.day"):
        t = li.find("time")
        if not t:
            continue
        mn, mx, pr = li.select_one("li.min"), li.select_one("li.max"), li.select_one(".short-precis")
        c, amt = chance.get(t["datetime"], (None, None))
        days.append(day(t["datetime"], mx.get("data-max") if mx else None, mn.get("data-min") if mn else None,
                        amt, chance=c, text=pr.get_text() if pr else None))
    if not days:
        raise ValueError("no forecast days found in page")
    return {"source": "willyweather", "name": "WillyWeather", "url": URLS["willyweather"], "days": days}


# ---- Elders Weather: 7-day table in the server-rendered HTML ----
def elders_date(label, idx, today):
    label = clean(label)
    if idx == 0 or label.lower() == "today":
        return today
    try:
        d = datetime.strptime(f"{label} {today.year}", "%a %b %d %Y").date()
    except ValueError:
        return today + timedelta(days=idx)
    if d < today - timedelta(days=180):  # header crosses into the new year
        d = d.replace(year=today.year + 1)
    return d


def scrape_elders():
    soup = BeautifulSoup(get(URLS["elders"]).text, "html.parser")
    table = None
    for t in soup.select("table"):
        heads = [clean(th.get_text()) for th in t.select("thead th")]
        if "Today" in heads:
            table = t
            break
    if table is None:
        raise ValueError("7-day table not found")
    heads = [clean(th.get_text()) for th in table.select("thead th")][1:]
    rows = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = clean(cells[0].get_text())
        vals = []
        for td in cells[1:]:
            img = td.find("img")
            vals.append(img.get("alt") if img and label == "Summary" else clean(td.get_text()))
        rows[label] = vals
    today = today_bne()
    days = []
    for i, h in enumerate(heads):
        def col(k):
            v = rows.get(k) or []
            return v[i] if i < len(v) else None
        tmax = (col("Maximum") or "").replace("°C", "")
        tmin = (col("Minimum") or "").replace("°C", "")
        days.append(day(elders_date(h, i, today).isoformat(), tmax, tmin, col("Rain Amount"),
                        chance=col("Chance of Rain"), text=col("Summary")))
    return {"source": "elders", "name": "Elders Weather", "url": URLS["elders"], "days": days}


def capture_json(url, wanted):
    """Load url in headless Chromium and return the JSON bodies of responses whose URL satisfies wanted().
    Used for sites whose forecast only arrives as background requests the page itself signs."""
    from playwright.sync_api import sync_playwright

    found = []

    def on_resp(r):
        if wanted(r.url):
            try:
                found.append(r.json())
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(user_agent=UA, locale="en-AU", timezone_id="Australia/Brisbane",
                                      viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            page.on("response", on_resp)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            for _ in range(30):  # up to 30 s for the first matching response
                if found:
                    break
                page.wait_for_timeout(1000)
            page.wait_for_timeout(5000)  # let any later matching responses arrive
        finally:
            browser.close()
    if not found:
        raise ValueError("expected background response never arrived")
    return found


# ---- Weatherzone: forecast arrives as background JSON, so load the page in a browser ----
def scrape_weatherzone():
    found = capture_json(URLS["weatherzone"],
                         lambda u: "/weather/forecast/daily/TWCID/" in u and "start=" in u)
    # The page requests several date ranges from the same endpoint; keep the longest
    payload = max(found, key=lambda b: len(b.get("body") or []))
    loc = next(iter(((payload.get("metadata") or {}).get("locations") or {}).values()), {})
    days = [day(d["date"], d.get("temperatureMax"), d.get("temperatureMin"), d.get("precipitationAmountText"),
                chance=d.get("precipitationProbability"), text=d.get("precis"))
            for d in payload.get("body") or []]
    if not days:
        raise ValueError("forecast response had no days")
    out = {"source": "weatherzone", "name": "Weatherzone", "url": URLS["weatherzone"], "days": days}
    if loc.get("name"):
        out["point"] = loc["name"]  # forecast point Weatherzone uses for Oxley (Archerfield)
    return out


def wu_unit(el):
    """Weather Underground's unit class on a value element: "m" metric, "e" imperial (US)."""
    return next((c for c in (el.get("class") or []) if c in ("m", "e", "h", "s")), None) if el else None


# ---- Weather Underground: 10-day cards in the plain HTML. The bare /oxley URL is Oxley NSW ----
def scrape_wunderground():
    # Accepting JSON gets a different page (Brisbane, other layout), so ask for HTML only
    # It occasionally serves its Brisbane page instead, so retry a couple of times before giving up
    for attempt in range(3):
        html = get(URLS["wunderground"], headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}).text
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text() if soup.title else ""
        if "Oxley" in title:
            break
        time.sleep(3)
    else:
        raise ValueError(f"wrong page served: {clean(title)[:80]}")
    days = []
    for a in soup.select("a.day-link"):
        m = re.search(r"/date/(\d{4}-\d{2}-\d{2})", a.get("href", ""))
        sunrise = a.select_one(".sunrise[data-time]")
        date = m.group(1) if m else (sunrise["data-time"][:10] if sunrise else None)
        if not date:
            continue
        hi, lo = a.select_one(".high"), a.select_one(".low")
        cond, qpf = a.select_one(".condition"), a.select_one(".precip")
        # Units follow the visitor's location: GitHub's US servers get Fahrenheit and inches. The low
        # and rain elements carry a unit class ("m" metric, "e" imperial); the high has none.
        t_unit, r_unit = wu_unit(lo), wu_unit(qpf)
        if t_unit not in ("m", "e") or (qpf and r_unit not in ("m", "e")):
            raise ValueError(f"unknown units: low {lo.get('class') if lo else None}, rain {qpf.get('class') if qpf else None}")
        to_c = (lambda v: None if v is None else round((v - 32) * 5 / 9, 1)) if t_unit == "e" else (lambda v: v)
        # "--" for today's high once the day has passed its peak; num() turns that into None
        tmax = to_c(num(hi.get_text().replace("°", ""))) if hi else None
        tmin = to_c(num(lo.get_text().replace("°", ""))) if lo else None
        mm = num(qpf.get_text()) if qpf else None
        if mm is not None and r_unit == "e":
            mm = round(mm * 25.4, 1)
        for v in (tmax, tmin):
            if v is not None and not -10 <= v <= 50:
                raise ValueError(f"implausible temperature {v} for {date} after unit handling")
        days.append(day(date, tmax, tmin, None if mm is None else f"{mm:g} mm", mm, mm,
                        text=cond.get_text() if cond else None))
    if not days:
        raise ValueError("no forecast cards found in page")
    return {"source": "wunderground", "name": "Weather Underground", "url": URLS["wunderground"], "days": days}


# ---- Windy: point forecast arrives as background JSON (signed by the page), temperatures in Kelvin ----
def scrape_windy():
    found = capture_json(URLS["windy"], lambda u: "/forecast/point/" in u)
    payload = max(found, key=lambda b: len(b.get("summary") or []))
    header = payload.get("header") or {}
    offset = timedelta(hours=header.get("utcOffset", 10))
    k2c = lambda k: None if k is None else round(k - 273.15, 1)
    data = payload.get("data") or {}
    rain, steps = {}, {}
    for ts, mm in zip(data.get("ts") or [], data.get("precipAmount") or []):
        d = (datetime.fromtimestamp(ts / 1000, timezone.utc) + offset).date().isoformat()
        rain[d] = rain.get(d, 0) + (mm or 0)
        steps[d] = steps.get(d, 0) + 1
    days = []
    for s in payload.get("summary") or []:
        d = (datetime.fromtimestamp(s["timestamp"] / 1000, timezone.utc) + offset).date().isoformat()
        # Only total rain for days the 3-hourly series fully covers; the last day is often cut short
        mm = round(rain[d], 1) if steps.get(d, 0) >= s.get("segments", 8) else None
        days.append(day(d, k2c(s.get("tempMax")), k2c(s.get("tempMin")),
                        None if mm is None else f"{mm:g} mm", mm, mm))
    if not days:
        raise ValueError("forecast response had no daily summary")
    out = {"source": "windy", "name": "Windy", "url": URLS["windy"], "days": days}
    if header.get("model"):
        out["point"] = f"{header['model']} model"
    return out


# ---- Bureau of Meteorology latest observations: Archerfield, about 3 km from Oxley ----
def scrape_observations():
    rows = get(URLS["bom_obs"]).json()["observations"]["data"]
    r = rows[0]  # newest first
    t = r.get("local_date_time_full") or ""
    when = f"{t[0:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:{t[10:12]}:00+10:00" if len(t) >= 12 else None
    return {"source": "bom_obs", "station": r.get("name"), "time": when,
            "temp": num(r.get("air_temp")), "feels_like": num(r.get("apparent_t")),
            "humidity": num(r.get("rel_hum")), "wind_dir": r.get("wind_dir"),
            "wind_kmh": num(r.get("wind_spd_kmh")), "gust_kmh": num(r.get("gust_kmh")),
            "rain_since_9am": num(r.get("rain_trace")), "pressure": num(r.get("press"))}


SCRAPERS = [scrape_bom, scrape_willyweather, scrape_elders, scrape_weatherzone, scrape_wunderground,
            scrape_windy]
EXTRAS = [scrape_observations]  # written as top-level "observations"


def masked(e):
    return f"{type(e).__name__}: {e}"[:300]


def main():
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    old = {s["source"]: s for s in prev.get("sources", [])}
    sources, errors, ran = [], {}, 0
    result = {}
    for fn in EXTRAS:
        key = fn.__name__.replace("scrape_", "")
        ran += 1
        try:
            result[key] = {**fn(), "fetched": now_iso()}
            print(f"{key}: ok")
        except Exception as e:
            errors[key] = masked(e)
            traceback.print_exc()
            if prev.get(key):
                result[key] = prev[key]
            print(f"{key}: FAILED ({errors[key]})", file=sys.stderr)
    for fn in SCRAPERS:
        key = fn.__name__.replace("scrape_", "")
        try:
            s = fn()
            ran += 1
            s["fetched"] = now_iso()
            sources.append(s)
            print(f"{key}: {len(s['days'])} days")
        except Exception as e:
            ran += 1
            errors[key] = masked(e)
            traceback.print_exc()
            # Keep the last good entry, unless it is itself bad (e.g. saved before a unit fix)
            if key in old and all(-10 <= d.get("tmax", 0) <= 50 for d in old[key].get("days", [])
                                  if isinstance(d.get("tmax"), (int, float))):
                sources.append(old[key])
            print(f"{key}: FAILED ({errors[key]})", file=sys.stderr)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": now_iso(), "sources": sources, **result, "errors": errors}, f, indent=1, ensure_ascii=False)
        f.write("\n")
    # Fail the step only if nothing at all came back, so partial results still get committed
    return 0 if len(errors) < ran else 1


if __name__ == "__main__":
    sys.exit(main())
