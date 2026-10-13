"""Built-in IMO meteor-shower table.

Source: IMO 2026 Meteor Shower Calendar (Rendtel, IMO INFO(3-25)), working list + Table 6
(radiant drift).  Peak dates/times are YEAR-SPECIFIC: update this table for other years.
`peak_utc` is used when the calendar gives a time; otherwise the peak instant is computed
locally from the listed solar longitude (lambda_sun, J2000).
"""
from datetime import datetime, timezone, date

UTC = timezone.utc

SHOWERS = [
    {"code": "SLY", "name": "September Lyncids", "active": ((9, 10), (10, 8)), "peak_date": (9, 13),
     "lam": 170.0, "zhr": "3", "v": 60,
     "radiant": [((10, 5), 140, 45), ((10, 10), 147, 44)], "note": "weak"},
    {"code": "OCT", "name": "October Camelopardalids", "active": ((10, 5), (10, 6)), "peak_date": (10, 6),
     "peak_utc": datetime(2026, 10, 6, 4, 40, tzinfo=UTC), "lam": 192.58, "zhr": "5(?)", "v": 47,
     "radiant": [((10, 5), 164, 79)], "note": "short-lived, circumpolar radiant"},
    {"code": "DRA", "name": "Draconids", "active": ((10, 6), (10, 10)), "peak_date": (10, 9),
     "peak_utc": datetime(2026, 10, 9, 1, 0, tzinfo=UTC), "lam": 195.4,
     "peak_night": date(2026, 10, 8), "peak_night_src": "IMO peak 01h UT Oct 9 = evening of Oct 8 in US time zones", "zhr": "5(?) (variable; outbursts possible)",
     "v": 20, "radiant": [((10, 6), 263, 56)],
     "note": "radiant highest at nightfall; very slow meteors; IMO: 'perfectly moonless' in 2026"},
    {"code": "STA", "name": "Southern Taurids", "active": ((9, 20), (11, 20)), "peak_date": (11, 5),
     "lam": 223.0, "zhr": "5-10 (secondary max ZHR~5 near Oct 13)", "v": 27,
     "radiant": [((10, 5), 28, 8), ((10, 10), 32, 9), ((10, 15), 36, 11), ((10, 20), 40, 12),
                 ((10, 25), 43, 13), ((10, 30), 47, 14), ((11, 5), 52, 15)],
     "note": "slow, fireball-prone; radiant up most of the night; IMO notes an annual-component maximum around Oct 13-14 (ZHR ~5), main listed max Nov 5"},
    {"code": "ORI", "name": "Orionids", "active": ((10, 2), (11, 7)), "peak_date": (10, 21),
     "lam": 208.0, "zhr": "20+", "v": 66,
     "peak_night": date(2026, 10, 21), "peak_night_src": "IMO web calendar ('Oct 21-22, 2026 night')",
     "radiant": [((10, 5), 85, 14), ((10, 10), 88, 15), ((10, 15), 91, 15), ((10, 20), 94, 16),
                 ((10, 25), 98, 16), ((10, 30), 101, 16), ((11, 5), 105, 17)],
     "note": "radiant useful after ~local midnight; low rates away from peak"},
    {"code": "EGE", "name": "epsilon-Geminids", "active": ((10, 14), (10, 27)), "peak_date": (10, 18),
     "lam": 205.0, "zhr": "3", "v": 70,
     "radiant": [((10, 15), 99, 27), ((10, 20), 104, 27), ((10, 25), 109, 27)], "note": "weak"},
    {"code": "LMI", "name": "Leonis Minorids", "active": ((10, 19), (10, 27)), "peak_date": (10, 24),
     "lam": 211.0, "zhr": "2", "v": 62,
     "radiant": [((10, 20), 158, 39), ((10, 25), 163, 37)], "note": "weak, pre-dawn radiant"},
    {"code": "NTA", "name": "Northern Taurids", "active": ((10, 20), (12, 10)), "peak_date": (11, 12),
     "lam": 230.0, "zhr": "5", "v": 29,
     "radiant": [((10, 20), 38, 18), ((10, 25), 43, 19), ((10, 30), 47, 20), ((11, 5), 52, 21)],
     "note": "slow, fireball-prone"},
]


def _md(d):
    return (d.month, d.day)


def is_active(sh, d):
    a, b = sh["active"]
    md = _md(d)
    if a <= b:
        return a <= md <= b
    return md >= a or md <= b          # wraps over new year


def radiant_on(sh, d):
    """Linear interpolation of Table-6 radiant positions for date d (same year)."""
    pts = [(datetime(d.year, m, dd).date(), ra, de) for (m, dd), ra, de in sh["radiant"]]
    if d <= pts[0][0] or len(pts) == 1:
        return pts[0][1], pts[0][2]
    if d >= pts[-1][0]:
        return pts[-1][1], pts[-1][2]
    for (d0, r0, e0), (d1, r1, e1) in zip(pts, pts[1:]):
        if d0 <= d <= d1:
            f = (d - d0).days / max((d1 - d0).days, 1)
            return r0 + (r1 - r0) * f, e0 + (e1 - e0) * f
