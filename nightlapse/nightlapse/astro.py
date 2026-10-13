"""Local astronomy with Skyfield + DE421 (cached in data/)."""
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import numpy as np
from skyfield.api import Loader, wgs84, Star
from skyfield import almanac
from skyfield.framelib import ecliptic_J2000_frame

from . import config as C

TZ = ZoneInfo(C.TZ)
_loader = Loader(str(C.DATA_DIR))
ts = _loader.timescale(builtin=True)
eph = _loader("de421.bsp")          # downloaded once, then cached in data/
SUN, MOON, EARTH = eph["sun"], eph["moon"], eph["earth"]


class Site:
    def __init__(self, lat, lon, elev_m):
        self.topos = wgs84.latlon(lat, lon, elevation_m=elev_m)
        self.obs = EARTH + self.topos


def t_of(dt):
    return ts.from_datetime(dt.astimezone(timezone.utc))


def local(t):
    """skyfield Time -> aware local datetime"""
    return t.utc_datetime().astimezone(TZ)


def alt_deg(site, body, t):
    return site.obs.at(t).observe(body).apparent().altaz()[0].degrees


def illum(t):
    return almanac.fraction_illuminated(eph, "moon", t)


def new_moons(start_dt, end_dt):
    t, y = almanac.find_discrete(t_of(start_dt), t_of(end_dt), almanac.moon_phases(eph))
    return [local(ti) for ti, yi in zip(t, y) if yi == 0]


def night_of_instant(dt_local):
    """Evening date of the night that 'contains' a local instant (per NEW_MOON_NIGHT_RULE)."""
    if C.NEW_MOON_NIGHT_RULE == "calendar":
        return dt_local.date()
    # nearest local midnight: before noon -> the night that began the previous evening
    return dt_local.date() if dt_local.hour >= 12 else dt_local.date() - timedelta(days=1)


def pick_new_moon(tonight, days, target=None):
    """Return the new-moon datetime (local) to plan around."""
    if target is not None:
        c = datetime(target.year, target.month, target.day, 12, tzinfo=TZ)
        nms = new_moons(c - timedelta(days=20), c + timedelta(days=20))
        return min(nms, key=lambda d: abs((d - c).total_seconds()))
    start = datetime(tonight.year, tonight.month, tonight.day, 12, tzinfo=TZ) - timedelta(days=days + 2)
    for nm in new_moons(start, start + timedelta(days=75)):
        if night_of_instant(nm) + timedelta(days=days) >= tonight:
            return nm
    raise RuntimeError("no new moon found")


def _events(fn, site, body, t0, t1):
    t, y = fn(site.obs, body, t0, t1)
    return [local(ti) for ti, yi in zip(t, y) if yi]


def night_astro(site, d):
    """Astronomy for the night starting on local evening date d (noon d -> noon d+1)."""
    n0 = datetime(d.year, d.month, d.day, 12, tzinfo=TZ)
    n1 = n0 + timedelta(days=1)
    t0, t1 = t_of(n0), t_of(n1)
    out = {"date": d}
    sets = _events(almanac.find_settings, site, SUN, t0, t1)
    rises = _events(almanac.find_risings, site, SUN, t0, t1)
    out["sunset"] = sets[0] if sets else None
    out["sunrise"] = rises[0] if rises else None
    # astronomical twilight: state 0 == fully dark (sun < -18)
    f = almanac.dark_twilight_day(eph, site.topos)
    tt, yy = almanac.find_discrete(t0, t1, f)
    out["astro_dark_start"] = next((local(a) for a, b in zip(tt, yy) if b == 0), None)
    out["astro_dark_end"] = next((local(a) for a, b in zip(tt, yy) if b == 1 and out["astro_dark_start"] and local(a) > out["astro_dark_start"]), None)
    out["moonrise"] = _events(almanac.find_risings, site, MOON, t0, t1)
    out["moonset"] = _events(almanac.find_settings, site, MOON, t0, t1)
    mid = t_of(n0 + timedelta(hours=12))
    out["illum_midnight"] = float(illum(mid))
    # true darkness: astro dark AND moon below horizon (2-minute sampling)
    out["true_dark_h"] = 0.0
    out["true_dark_intervals"] = []
    out["astro_dark_h"] = 0.0
    if out["astro_dark_start"] and out["astro_dark_end"]:
        a, b = out["astro_dark_start"], out["astro_dark_end"]
        out["astro_dark_h"] = (b - a).total_seconds() / 3600
        n = int((b - a).total_seconds() // 120) + 1
        times = [a + timedelta(minutes=2 * i) for i in range(n)]
        tarr = ts.from_datetimes([x.astimezone(timezone.utc) for x in times])
        malt = alt_deg(site, MOON, tarr)
        down = malt < C.MOON_HORIZON_DEG
        out["true_dark_h"] = float(down[:-1].sum() * 2 / 60) if n > 1 else 0.0
        # contiguous intervals
        start = None
        for x, dn in zip(times, down):
            if dn and start is None:
                start = x
            if not dn and start is not None:
                out["true_dark_intervals"].append((start, x)); start = None
        if start is not None:
            out["true_dark_intervals"].append((start, b))
    return out


def hourly_astro(site, d, hours):
    """Per hour slot (local start hour) for night d: sun/moon altitude at slot midpoint, illumination."""
    slots = []
    for h in hours:
        day = d if h >= 12 else d + timedelta(days=1)
        slots.append(datetime(day.year, day.month, day.day, h, tzinfo=TZ))
    mids = [s + timedelta(minutes=30) for s in slots]
    tarr = ts.from_datetimes([m.astimezone(timezone.utc) for m in mids])
    salt = alt_deg(site, SUN, tarr)
    malt = alt_deg(site, MOON, tarr)
    ill = illum(tarr)
    return [{"slot": s, "sun_alt": float(a), "moon_alt": float(m), "illum": float(i),
             "dark": bool(a < C.DARK_SUN_ALT)} for s, a, m, i in zip(slots, salt, malt, ill)]


def radiant_alt(site, ra_deg, dec_deg, dts):
    star = Star(ra_hours=ra_deg / 15.0, dec_degrees=dec_deg)
    tarr = ts.from_datetimes([x.astimezone(timezone.utc) for x in dts])
    return site.obs.at(tarr).observe(star).apparent().altaz()[0].degrees


def solar_longitude_time(lam_deg, approx_local):
    """Time when the Sun's J2000 ecliptic longitude equals lam_deg (near approx date)."""
    def lon(t):
        e = EARTH.at(t).observe(SUN).apparent()
        return e.frame_latlon(ecliptic_J2000_frame)[1].degrees
    lo = approx_local - timedelta(days=4)
    hi = approx_local + timedelta(days=4)
    def g(dt):
        return ((lon(t_of(dt)) - lam_deg + 180) % 360) - 180
    if g(lo) > 0 or g(hi) < 0:
        return None
    for _ in range(40):
        m = lo + (hi - lo) / 2
        if g(m) < 0:
            lo = m
        else:
            hi = m
    return lo
