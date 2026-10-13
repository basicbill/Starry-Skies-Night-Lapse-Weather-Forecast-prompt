"""Location: cached in data/location.json; --regeocode refreshes from US Census + USGS EPQS."""
import json
import requests
from . import config as C


def _elevation(lat, lon, h):
    e = requests.get("https://epqs.nationalmap.gov/v1/json",
                     params={"x": lon, "y": lat, "units": "Meters", "wkid": 4326, "includeDate": "false"},
                     headers=h, timeout=60).json()
    return float(e["value"])


def load_location(force=False):
    if C.CUSTOM_SITE:
        cs = C.CUSTOM_SITE
        if C.LOCATION_FILE.exists() and not force:
            old = json.loads(C.LOCATION_FILE.read_text())
            if abs(old["lat"] - cs["lat"]) < 1e-6 and abs(old["lon"] - cs["lon"]) < 1e-6 and cs["elev"] is None:
                return old
        h = {"User-Agent": C.USER_AGENT}
        elev, src = cs["elev"], "given on command line"
        if elev is None:
            try:
                elev, src = _elevation(cs["lat"], cs["lon"], h), "USGS EPQS (3DEP)"
            except Exception as ex:  # noqa
                elev, src = 0.0, f"UNKNOWN (USGS EPQS failed: {ex}); 0 m assumed"
        loc = {"name": cs["name"], "address": cs["name"], "lat": cs["lat"], "lon": cs["lon"], "elevation_m": elev,
               "geocoder": "coordinates given on command line", "elevation_source": src}
        C.LOCATION_FILE.write_text(json.dumps(loc, indent=2))
        return loc
    if C.LOCATION_FILE.exists() and not force:
        return json.loads(C.LOCATION_FILE.read_text())
    old = json.loads(C.LOCATION_FILE.read_text()) if C.LOCATION_FILE.exists() else None
    h = {"User-Agent": C.USER_AGENT}
    try:
        r = requests.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                         params={"address": C.ADDRESS, "benchmark": "Public_AR_Current", "format": "json"},
                         headers=h, timeout=C.HTTP_TIMEOUT)
        m = r.json()["result"]["addressMatches"][0]
        lat, lon = m["coordinates"]["y"], m["coordinates"]["x"]
        loc = {"name": C.SITE_NAME, "address": C.ADDRESS, "matched_address": m["matchedAddress"],
               "lat": lat, "lon": lon, "elevation_m": _elevation(lat, lon, h),
               "geocoder": "US Census Geocoder, benchmark Public_AR_Current",
               "elevation_source": "USGS EPQS (3DEP)"}
        C.LOCATION_FILE.write_text(json.dumps(loc, indent=2))
        return loc
    except Exception as ex:  # noqa
        if old:
            print(f"WARNING: re-geocode failed ({ex}); using cached location")
            return old
        raise
