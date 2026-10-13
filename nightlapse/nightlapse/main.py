"""Orchestration: astronomy + weather -> scores -> outputs."""
import argparse, json, sys
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

from . import config as C
from . import astro as A
from . import weather as Wx
from . import scoring as S
from . import showers as SH
from . import report as R
from . import geo
from . import sites as SITES
from . import explain as EX
from . import skymap as SKY

TZ = ZoneInfo(C.TZ)
UTC = timezone.utc


def parse_args(argv):
    p = argparse.ArgumentParser(description="Night-lapse / astro conditions scorer (default site: Green Valley, AZ)",
                                parents=[SITES.site_parser()])
    p.add_argument("--days", type=int, default=C.DEFAULT_DAYS, help="nights on each side of new-moon night (default %(default)s)")
    p.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="force target new moon nearest this date (YYYY-MM-DD)")
    p.add_argument("--regeocode", action="store_true", help="re-run Census geocode + USGS elevation lookup")
    p.add_argument("--outdir", help="override output directory")
    p.add_argument("--low-threshold", type=float, default=None,
                   help=f"explain window-night dark hours scoring below this (default {C.LOW_SCORE_THRESHOLD})")
    p.add_argument("--no-skymaps", action="store_true", help="skip the meteor-radiant all-sky maps")
    p.add_argument("--skymap-nights", help="only draw radiant maps for these nights (comma-separated YYYY-MM-DD)")
    return p.parse_args(argv)


LEVELS = ["high", "medium", "low"]


def lead_conf(lead, nws_frac, om_frac, lr_name="Open-Meteo", stale_age=None):
    """stale_age: age (h) of a cached long-range response used for this night -> one level lower."""
    lvl = "high" if lead <= C.CONF_HIGH_MAX_DAYS else "medium" if lead <= C.CONF_MEDIUM_MAX_DAYS else "low"
    notes = []
    if nws_frac == 0:
        if om_frac > 0:
            lvl, notes = "low", [f"{lr_name} only"]
        else:
            notes = ["7Timer only"]
    elif nws_frac < 1:
        notes = ["NWS partial"]
    if stale_age is not None:
        lvl = LEVELS[min(LEVELS.index(lvl) + 1, 2)]
        notes.append(f"lowered: Open-Meteo cache {stale_age:.0f} h old")
    return lvl + (f" ({'; '.join(notes)})" if notes else "")


def run(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.low_threshold is not None:
        C.LOW_SCORE_THRESHOLD = args.low_threshold
    now = datetime.now(TZ)
    today = now.date()
    loc = geo.load_location(force=args.regeocode)
    site = A.Site(loc["lat"], loc["lon"], loc["elevation_m"])
    # "tonight" = last night while it is still astronomically dark, otherwise this evening
    prev = A.night_astro(site, today - timedelta(days=1))
    still_dark = prev["astro_dark_end"] is not None and now < prev["astro_dark_end"]
    tonight = today - timedelta(days=1) if still_dark else today

    nm = A.pick_new_moon(tonight, args.days, args.date)
    nm_night = A.night_of_instant(nm)
    w0, w1 = nm_night - timedelta(days=args.days), nm_night + timedelta(days=args.days)

    folder = today.isoformat() + (f"-{C.SITE_SLUG}" if C.SITE_SLUG else "")
    outdir = C.OUTPUT_DIR / folder if not args.outdir else __import__("pathlib").Path(args.outdir)
    raw = outdir / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    # ---------------- weather
    nws, st_nws = Wx.fetch_nws(loc["lat"], loc["lon"], raw)
    t7, st_t7 = Wx.fetch_7timer(loc["lat"], loc["lon"], raw)
    need_end = datetime.combine(w1 + timedelta(days=1), datetime.min.time().replace(hour=7), TZ).astimezone(UTC)
    om, st_om = Wx.fetch_longrange(loc["lat"], loc["lon"], loc["elevation_m"], raw, need_end,
                                   (w1 - today).days + 2)
    statuses = {"NWS": st_nws, "7Timer": st_t7, "Open-Meteo": st_om}   # key kept for nights.json / compare.py

    # ---------------- nights
    first = min(tonight, w0) if args.date and w1 < tonight else tonight
    nights, d = [], first
    while d <= w1:
        nights.append(d)
        d += timedelta(days=1)

    night_rows, hourly_rows = [], []
    for d in nights:
        na = A.night_astro(site, d)
        hs = A.hourly_astro(site, d, C.NIGHT_HOURS)
        in_window = w0 <= d <= w1
        rows, any_data, nws_hours, t7_hours, om_hours, dark_scored = [], False, 0, 0, 0, 0
        for h in hs:
            key = h["slot"].astimezone(UTC)
            n_, t_, o_ = nws.get(key, {}), t7.get(key, {}), om.get(key, {})
            m = S.merge_inputs(n_, t_, o_)
            score, pen, flags = (None, {}, [])
            if h["dark"]:
                score, pen, flags = S.score_hour(m, h["moon_alt"], h["illum"], t_, o_)
                if score is not None:
                    dark_scored += 1
                    nws_hours += n_.get("nws_sky") is not None
                    t7_hours += t_.get("t7_transp") is not None
                    om_hours += o_.get("om_cloud") is not None
            if m["cloud_blend"] is not None:
                any_data = True
            rows.append({"h": h, "nws": n_, "t7": t_, "om": o_, "m": m, "score": score, "pen": pen, "flags": flags})
        if not any_data and not in_window:
            continue    # pre-window night with no forecast at all -> not shown
        dark = [(r["h"]["slot"], r["score"]) for r in rows if r["h"]["dark"]]
        scored = [s for _, s in dark if s is not None]
        kind, block = S.best_blocks(dark)
        mean = sum(scored) / len(scored) if scored else None
        verdict = S.verdict(kind, block, mean)
        # dew: min spread over sunset..sunrise slots
        sp = [(r["m"]["spread_f"], r["h"]["slot"]) for r in rows
              if r["m"]["spread_f"] is not None and na["sunset"] and na["sunrise"]
              and r["h"]["slot"] + timedelta(hours=1) > na["sunset"] and r["h"]["slot"] < na["sunrise"]]
        min_sp = min(sp, key=lambda x: x[0]) if sp else None
        lead = (d - today).days
        nws_frac = (nws_hours / dark_scored) if dark_scored else 0
        lr_srcs = [r["om"].get("om_src") for r in rows if r["score"] is not None and r["om"].get("om_cloud") is not None]
        stale = any("CACHED" in x for x in lr_srcs)
        met = [x for x in lr_srcs if x.startswith("met-norway")]
        lr_name = "MET Norway" if met and len(met) == len(lr_srcs) else "Open-Meteo" if not met else "Open-Meteo/MET"
        conf = lead_conf(lead, nws_frac, (om_hours / dark_scored) if dark_scored else 0, lr_name,
                         st_om.get("age_h") if stale else None) if scored else "n/a"
        active = [s for s in SH.SHOWERS if SH.is_active(s, d) or SH.is_active(s, d + timedelta(days=1))]
        night_rows.append({
            "date": d, "astro": na, "rows": rows, "in_window": in_window, "is_nm": d == nm_night,
            "kind": kind, "block": block, "mean": mean, "verdict": verdict, "min_spread": min_sp,
            "lens_heater": bool(min_sp and min_sp[0] < C.WEIGHTS["dew_spread_warn_f"]),
            "lead": lead, "conf": conf, "nws_frac": nws_frac, "t7_frac": (t7_hours / dark_scored) if dark_scored else 0,
            "om_src": next((r["om"].get("om_src") for r in rows if r["om"].get("om_src")), None),
            "lr_srcs": sorted(set(lr_srcs)), "lr_stale": stale, "lr_name": lr_name if lr_srcs else None,
            "active": active, "has_data": bool(scored),
        })

    shower_info = R.shower_details(site, nights and nights[0], w1, nm)
    ctx = {"now": now, "today": today, "tonight": tonight, "loc": loc, "nm": nm, "nm_night": nm_night,
           "w0": w0, "w1": w1, "days": args.days, "statuses": statuses, "nights": night_rows,
           "showers": shower_info, "outdir": outdir, "forced_date": args.date}
    ctx["low"] = EX.low_hours(night_rows)
    ctx["skymaps"], ctx["skymaps_skipped"], ctx["skymaps_error"] = [], [], None
    if C.SKYMAPS and not args.no_skymaps:
        try:
            only = None
            if args.skymap_nights:
                only = {datetime.strptime(x.strip(), "%Y-%m-%d").date() for x in args.skymap_nights.split(",")}
            ctx["skymaps"], ctx["skymaps_skipped"] = SKY.build(ctx, site, only)
        except Exception as e:  # noqa
            ctx["skymaps_error"] = f"{type(e).__name__}: {e}"
    R.write_csv(ctx)
    R.write_heatmap(ctx)
    R.write_summary(ctx)
    R.write_nights_json(ctx)
    print(f"New moon: {nm:%Y-%m-%d %H:%M} {C.TZ_LABEL}; new-moon night {nm_night}; window {w0} .. {w1}")
    for k, s in statuses.items():
        print(f"  {k:10s}: {(s.get("source") + " — ") if k == "Open-Meteo" and s.get("source") else ""}{s.get("mode")}  {('ERR: ' + s['error']) if s.get('error') else ''}")
    print(f"Wrote {outdir}/summary.md, heatmap.png, hourly.csv")
    for p in ctx["skymaps"]:
        print(f"  radiant map: {p['path']}  [{p['renderer']}]")
    if ctx["skymaps_error"]:
        print("  radiant maps FAILED:", ctx["skymaps_error"])
    return ctx
