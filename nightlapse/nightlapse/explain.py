"""Plain-language explanations for low-scoring dark hours inside the dark window."""
from datetime import timedelta
from . import config as C

W = C.WEIGHTS


def _transp_words(t):
    return "good" if t <= 2.5 else "fair" if t <= 4.5 else "poor" if t <= 6.5 else "very poor"


def describe(r):
    """List of (category, penalty, text) for one scored hour, largest first."""
    m, pen, h, t7, om = r["m"], r["pen"], r["h"], r["t7"], r["om"]
    items = []
    cl = pen.get("cloud", 0) + pen.get("high_cloud", 0)
    if cl > 0:
        txt = f"clouds {m['cloud_blend']:.0f}%"
        if m.get("high_cloud") is not None and m["high_cloud"] >= 5:
            txt += f" incl. {m['high_cloud']:.0f}% high cirrus"
        items.append(("clouds", cl, f"{txt} ({m['cloud_src']})"))
    if pen.get("moon", 0) > 0:
        items.append(("moon", pen["moon"], f"moon {100*h['illum']:.0f}% lit at {h['moon_alt']:.0f}° altitude"))
    if pen.get("wind", 0) > 0:
        parts = []
        if m["wind_mph"] is not None and m["wind_mph"] > W["wind_sustained_mph"]:
            parts.append(f"wind {m['wind_mph']:.0f} mph")
        if m["gust_mph"] is not None and m["gust_mph"] > W["gust_mph"]:
            parts.append(f"gusts {m['gust_mph']:.0f} mph")
        items.append(("wind", pen["wind"], ", ".join(parts) or "wind"))
    if pen.get("dew", 0) > 0:
        items.append(("dew", pen["dew"], f"dew spread {m['spread_f']:.1f}°F"))
    if pen.get("transparency", 0) > 0:
        src = m.get("transp_src")
        if src == "7timer":
            txt = f"transparency {_transp_words(t7['t7_transp'])} (7Timer {t7['t7_transp']:.1f}/8)"
        elif src and src.startswith("proxy"):
            vis = om.get("om_vis_m")
            bits = [f"RH {m['rh']:.0f}%"] if m.get("rh") is not None else []
            if vis is not None:
                bits.append(f"visibility {vis/1000:.0f} km")
            txt = f"transparency estimated from {', '.join(bits)}"
        else:
            txt = "transparency unknown (neutral)"
        items.append(("transparency", pen["transparency"], txt))
    if pen.get("seeing", 0) > 0:
        txt = (f"seeing {t7['t7_seeing']:.1f}/8 (7Timer)" if m.get("seeing_src") == "7timer"
               else "seeing unknown (neutral)")
        items.append(("seeing", pen["seeing"], txt))
    if pen.get("precip", 0) > 0:
        items.append(("precip", pen["precip"], f"rain chance {m['pop']:.0f}%"))
    items.sort(key=lambda x: -x[1])
    return items


def low_hours(nights):
    """Return per-window-night groups of consecutive below-threshold hours sharing a primary cause."""
    out = []
    for n in nights:
        if not n["in_window"]:
            continue
        hours = []
        for r in n["rows"]:
            if not r["h"]["dark"] or r["score"] is None or r["score"] >= C.LOW_SCORE_THRESHOLD:
                continue
            items = describe(r)
            # neutral placeholders (no data) are listed but never chosen as the main cause
            real = [i for i in items if "neutral" not in i[2]]
            primary = real[0][0] if real else ("missing data" if items else "none")
            shown = [i for i in items if i[1] >= C.LOW_MIN_PENALTY][:C.LOW_MAX_PENALTIES] or items[:1]
            hours.append({"slot": r["h"]["slot"], "score": r["score"], "primary": primary,
                          "cloud_driven": primary == "clouds", "items": shown})
        groups = []
        for hr in hours:
            g = groups[-1] if groups else None
            if (g and g["primary"] == hr["primary"]
                    and hr["slot"] - g["hours"][-1]["slot"] == timedelta(hours=1)):
                g["hours"].append(hr)
            else:
                groups.append({"primary": hr["primary"], "cloud_driven": hr["cloud_driven"], "hours": [hr]})
        out.append({"night": n["date"], "groups": groups, "has_data": n["has_data"]})
    return out
