"""Hourly 0-10 score + nightly summaries.  Weights live in config.WEIGHTS."""
import math
from . import config as C

W = C.WEIGHTS


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def merge_inputs(nws, t7, om):
    """Combine source dicts for one hour into the model inputs (+ source tags)."""
    r = {}
    def pick(name, *cands):
        for src, val in cands:
            if val is not None:
                r[name], r[name + "_src"] = val, src
                return
        r[name], r[name + "_src"] = None, "none"
    pick("temp_f", ("nws", nws.get("nws_temp_f")), (om.get("om_src", "om"), om.get("om_temp_f")))
    pick("dew_f", ("nws", nws.get("nws_dew_f")), (om.get("om_src", "om"), om.get("om_dew_f")))
    pick("rh", ("nws", nws.get("nws_rh")), (om.get("om_src", "om"), om.get("om_rh")), ("7timer", t7.get("t7_rh_pct")))
    pick("wind_mph", ("nws", nws.get("nws_wind_mph")), (om.get("om_src", "om"), om.get("om_wind_mph")), ("7timer", t7.get("t7_wind_mph")))
    pick("gust_mph", ("nws", nws.get("nws_gust_mph")), (om.get("om_src", "om"), om.get("om_gust_mph")))
    pick("pop", ("nws", nws.get("nws_pop")), (om.get("om_src", "om"), om.get("om_pop")))
    # blended total cloud
    # (the "openmeteo" weight is the long-range-model slot; MET Norway fills it when Open-Meteo is unavailable)
    lr = "met" if str(om.get("om_src", "")).startswith("met") else "openmeteo"
    parts = [("nws", "nws", nws.get("nws_sky")), ("openmeteo", lr, om.get("om_cloud")), ("7timer", "7timer", t7.get("t7_cloud_pct"))]
    num = den = 0.0
    used = []
    for k, lab, v in parts:
        if v is not None:
            num += W["cloud_blend"][k] * v
            den += W["cloud_blend"][k]
            used.append(lab)
    r["cloud_blend"] = num / den if den else None
    r["cloud_src"] = "+".join(used) if used else "none"
    r["high_cloud"] = om.get("om_high")
    r["spread_f"] = (r["temp_f"] - r["dew_f"]) if r["temp_f"] is not None and r["dew_f"] is not None else None
    return r


def score_hour(m, moon_alt, illum, t7, om):
    """Return (score or None, penalties dict, flags list)."""
    pen, flags = {}, []
    if m["cloud_blend"] is None:
        return None, pen, ["no cloud data"]
    pen["cloud"] = W["cloud"] * m["cloud_blend"] / 100
    pen["high_cloud"] = W["high_cloud"] * (m["high_cloud"] or 0) / 100
    if m["high_cloud"] is None:
        flags.append("no high-cloud data")
    pen["moon"] = W["moon"] * illum * math.sin(math.radians(moon_alt)) if moon_alt > 0 else 0.0
    # transparency
    if t7.get("t7_transp") is not None:
        pen["transparency"] = W["transparency"] * clamp((t7["t7_transp"] - 1) / 7)
        m["transp_src"] = "7timer"
    else:
        rh, vis = m.get("rh"), om.get("om_vis_m")
        terms, wts = [], []
        if rh is not None:
            terms.append(clamp((rh - W["proxy_rh_lo"]) / (W["proxy_rh_hi"] - W["proxy_rh_lo"]))); wts.append(W["proxy_rh_share"])
        if vis is not None:
            terms.append(clamp((W["proxy_vis_good_m"] - vis) / (W["proxy_vis_good_m"] - W["proxy_vis_bad_m"]))); wts.append(1 - W["proxy_rh_share"])
        if terms:
            pen["transparency"] = W["transparency"] * sum(t * w for t, w in zip(terms, wts)) / sum(wts)
            m["transp_src"] = "proxy(RH/vis)"
        else:
            pen["transparency"] = W["transparency"] * W["neutral_fraction"]
            m["transp_src"] = "neutral"
        flags.append("transparency " + m["transp_src"])
    if t7.get("t7_seeing") is not None:
        pen["seeing"] = W["seeing"] * clamp((t7["t7_seeing"] - 1) / 7)
        m["seeing_src"] = "7timer"
    else:
        pen["seeing"] = W["seeing"] * W["neutral_fraction"]
        m["seeing_src"] = "neutral"
        flags.append("seeing neutral")
    # dew
    s = m["spread_f"]
    pen["dew"] = 0.0
    if s is not None:
        if s < W["dew_spread_severe_f"]:
            pen["dew"] = W["dew_severe"]
        elif s < W["dew_spread_warn_f"]:
            pen["dew"] = W["dew_warn"]
    # wind
    wp = 0.0
    ws, g = m["wind_mph"], m["gust_mph"]
    if ws is not None and ws > W["wind_sustained_mph"]:
        wp += min(W["wind_base"] + W["wind_per_mph"] * (ws - W["wind_sustained_mph"]), W["wind_sustained_max"])
    if g is not None and g > W["gust_mph"]:
        wp += W["gust"]
    pen["wind"] = min(wp, W["wind_cap"])
    pen["precip"] = W["precip"] * (m["pop"] or 0) / 100
    score = clamp(10 - sum(pen.values()), 0, 10)
    return round(score, 1), pen, flags


def best_blocks(rows):
    """rows: list of (slot_dt, score) for dark hours in time order (score may be None)."""
    good, best = [], None
    run = []
    for slot, sc in rows + [(None, None)]:
        if sc is not None and sc >= C.GOOD_SCORE:
            run.append((slot, sc))
        else:
            if run:
                good.append(run)
            run = []
    if good:
        best = max(good, key=lambda r: (len(r), sum(x[1] for x in r) / len(r)))
        return "good", best
    pairs = [(a, b) for a, b in zip(rows, rows[1:]) if a[1] is not None and b[1] is not None]
    if pairs:
        p = max(pairs, key=lambda ab: ab[0][1] + ab[1][1])
        return "best2h", list(p)
    singles = [r for r in rows if r[1] is not None]
    if singles:
        return "best1h", [max(singles, key=lambda r: r[1])]
    return None, []


def verdict(kind, block, mean):
    if kind is None:
        return "No forecast yet"
    if kind == "good" and len(block) >= C.GO_MIN_BLOCK_H:
        return "Go"
    if kind == "good":
        return "Marginal"
    avg = sum(x[1] for x in block) / len(block)
    if avg >= C.MARGINAL_MIN_2H:
        return "Marginal"
    return "No-go"
