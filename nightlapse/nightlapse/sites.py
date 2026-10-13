"""Site registry + early site selection.

The default site (Green Valley) keeps the original behaviour: data/location.json, America/Phoenix, and
output/<date>/.  Any other site writes to output/<date>-<slug>/.  Site options must be applied BEFORE the
other nightlapse modules are imported (they read config.TZ at import time); run.py does this.
"""
import argparse, json, re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as C

SITES = {
    "greenvalley": {"name": "Green Valley, AZ", "address": "1315 S Desert Meadows Cir, Green Valley, AZ 85614",
                    "tz": "America/Phoenix", "location_file": "location.json", "slug": ""},
    "stcharles": {"name": "St. Charles, IL", "address": "708 S 12th Ave, St. Charles, IL 60174",
                  "tz": "America/Chicago", "location_file": "location_stcharles.json", "slug": "stcharles"},
}
DEFAULT_SITE = "greenvalley"


def site_parser(add_help=False):
    p = argparse.ArgumentParser(add_help=add_help)
    p.add_argument("--site", choices=sorted(SITES), default=None,
                   help=f"named site (default {DEFAULT_SITE})")
    p.add_argument("--lat", type=float, help="custom site latitude (use with --lon/--tz/--name)")
    p.add_argument("--lon", type=float, help="custom site longitude")
    p.add_argument("--tz", help="custom site IANA time zone, e.g. America/Chicago")
    p.add_argument("--name", help="custom site name")
    p.add_argument("--elev", type=float, help="custom site elevation in metres (else USGS EPQS lookup)")
    return p


def apply(argv):
    """Pre-parse site options from argv and point config at that site. Returns the site key/slug."""
    a, _ = site_parser().parse_known_args(argv)
    if a.lat is not None or a.lon is not None:
        if a.lat is None or a.lon is None or not a.tz or not a.name:
            raise SystemExit("--lat, --lon, --tz and --name must be given together")
        slug = re.sub(r"[^a-z0-9]+", "", a.name.lower()) or "custom"
        C.SITE_KEY, C.SITE_SLUG = "custom", slug
        C.TZ = a.tz
        C.ADDRESS = a.name
        C.LOCATION_FILE = C.DATA_DIR / f"location_{slug}.json"
        C.CUSTOM_SITE = {"name": a.name, "lat": a.lat, "lon": a.lon, "elev": a.elev}
    else:
        key = a.site or DEFAULT_SITE
        s = SITES[key]
        C.SITE_KEY, C.SITE_SLUG = key, s["slug"]
        C.TZ = s["tz"]
        C.ADDRESS = s["address"]
        C.SITE_NAME = s["name"]
        C.LOCATION_FILE = C.DATA_DIR / s["location_file"]
        C.CUSTOM_SITE = None
    # label (e.g. MST, CDT) taken at run time; local times themselves are always DST-correct
    C.TZ_LABEL = datetime.now(ZoneInfo(C.TZ)).strftime("%Z")
    # site-specific API cache so sites never share cached weather
    if C.SITE_SLUG:
        C.CACHE_DIR = C.DATA_DIR / "cache" / C.SITE_SLUG
    return C.SITE_KEY, C.SITE_SLUG
