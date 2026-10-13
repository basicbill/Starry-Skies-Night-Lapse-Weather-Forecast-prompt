"""Outputs: hourly.csv, heatmap.png, summary.md (+ shower details)."""
import csv, math
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from . import config as C
from . import astro as A
from . import showers as SH

TZ = ZoneInfo(C.TZ)
L = C.TZ_LABEL


def hm(dt):
    return dt.strftime("%H:%M") if dt else "—"


def hm_rel(dt, d):
    if not dt:
        return "—"
    s = dt.strftime("%H:%M")
    return s + ("⁺¹" if dt.date() > d else "")


def fmt_utc(s):
    """ISO UTC string -> local label"""
    if not s:
        return "—"
    return datetime.fromisoformat(s).astimezone(TZ).strftime(f"%a %b %d %H:%M {L}")


def r1(x):
    return "" if x is None else f"{x:.1f}"


# ----------------------------------------------------------------------------- showers
def shower_details(site, first, last, nm):
    out = []
    year = nm.year
    for sh in SH.SHOWERS:
        # show if active on any displayed night
        d, act = first, False
        while d <= last + timedelta(days=1):
            act = act or SH.is_active(sh, d)
            d += timedelta(days=1)
        if not act:
            continue
        if sh.get("peak_utc"):
            peak, how = sh["peak_utc"].astimezone(TZ), "IMO 2026 calendar"
        else:
            approx = datetime(year, *sh["peak_date"], 12, tzinfo=TZ)
            peak = A.solar_longitude_time(sh["lam"], approx)
            how = f"computed from λ☉={sh['lam']}° (IMO date {approx:%b %d})"
        pn = sh.get("peak_night") or A.night_of_instant(peak)
        if sh.get("peak_night"):
            how += f"; peak night per {sh.get('peak_night_src', 'IMO')}"
        elif 7 <= peak.hour < 17:
            how += "; peak falls in daytime, adjacent nights similar"
        na = A.night_astro(site, pn)
        ra, de = SH.radiant_on(sh, pn)
        info = {"sh": sh, "peak": peak, "peak_how": how, "peak_night": pn, "ra": ra, "dec": de,
                "illum_peak": float(A.illum(A.t_of(peak)))}
        a, b = na["astro_dark_start"], na["astro_dark_end"]
        if a and b:
            n = int((b - a).total_seconds() // 600) + 1
            times = [a + timedelta(minutes=10 * i) for i in range(n)]
            ralt = A.radiant_alt(site, ra, de, times)
            malt = A.alt_deg(site, A.MOON, A.ts.from_datetimes([t.astimezone(timezone.utc) for t in times]))
            i = int(np.argmax(ralt))
            up = ralt > 20
            mup = malt > C.MOON_HORIZON_DEG
            info.update(rad_alt_dusk=float(ralt[0]), rad_max=float(ralt[i]), rad_max_t=times[i],
                        rad_up_h=float(up.sum() * 10 / 60), rad_up_moon_h=float((up & mup).sum() * 10 / 60),
                        rad_up_first=times[int(np.argmax(up))] if up.any() else None)
            frac = (info["rad_up_moon_h"] / info["rad_up_h"]) if info["rad_up_h"] else 0
            ill = float(A.illum(A.t_of(times[i])))
            eff = frac * ill
            info["moon_label"] = ("radiant <20° all dark hours" if not up.any() else
                                  "none" if eff < 0.05 else "minor" if eff < 0.25 else
                                  "moderate" if eff < 0.6 else "severe")
            info["illum_night"] = ill
        out.append(info)
    return out


def shower_tag(sh, d, details):
    det = next((x for x in details if x["sh"]["code"] == sh["code"]), None)
    return sh["code"] + ("★" if det and det["peak_night"] == d else "")


# ----------------------------------------------------------------------------- long-range source labels
def lr_label(tag):
    """om_src tag -> human-readable long-range source (the om_* columns hold whichever source supplied the hour)."""
    if not tag:
        return ""
    t = str(tag)
    if t.startswith("met-norway"):
        extra = "6-hourly step, linearly interpolated" if "interp" in t else "6-hourly step" if "6-hourly" in t else "hourly"
        mode = t[t.find("(") + 1:t.find(")")] if "(" in t else "live"
        return f"MET Norway locationforecast 2.0 ({mode}; {extra})"
    name = "Open-Meteo forecast" if t.startswith("om-forecast") else "Open-Meteo ensemble (member means)"
    if "CACHED" in t:
        return f"{name} — CACHED copy, {t.split('CACHED ')[1]}"
    if "(cache" in t:
        return f"{name} (reused cache {t.split('(cache ')[1].rstrip(')')} old)"
    return f"{name} (live)"


def lr_short(n):
    """Compact per-night long-range source tag for the heatmap."""
    srcs = n.get("lr_srcs") or []
    if not srcs:
        return "—"
    tags = []
    for t in srcs:
        if t.startswith("met-norway"):
            x = "MET"
        elif "CACHED" in t:
            x = ("OM" if t.startswith("om-forecast") else "OM-ens") + " cache " + t.split("CACHED ")[1].replace(" old", "")
        else:
            x = "OM" if t.startswith("om-forecast") else "OM-ens"
        if x not in tags:
            tags.append(x)
    return "+".join(tags)


# ----------------------------------------------------------------------------- CSV
CSV_COLS = ["night", "in_window", "new_moon_night", "slot_start_local", "dark", "sun_alt", "moon_alt", "moon_illum_pct",
            "nws_sky", "nws_temp_f", "nws_dew_f", "nws_rh", "nws_wind_mph", "nws_gust_mph", "nws_pop",
            "t7_cloud_code", "t7_cloud_pct", "t7_seeing", "t7_transp", "t7_lifted_index", "t7_rh2m_code", "t7_rh_pct",
            "t7_wind_code", "t7_wind_mph",
            "longrange_source", "om_src", "om_cloud", "om_low", "om_mid", "om_high", "om_temp_f", "om_dew_f", "om_rh", "om_vis_m",
            "om_wind_mph", "om_gust_mph", "om_pop",
            "cloud_blend", "cloud_src", "temp_f", "temp_f_src", "dew_f", "dew_f_src", "spread_f", "rh", "rh_src",
            "wind_mph", "wind_mph_src", "gust_mph", "gust_mph_src", "pop", "pop_src", "transp_src", "seeing_src",
            "pen_cloud", "pen_high_cloud", "pen_moon", "pen_transparency", "pen_seeing", "pen_dew", "pen_wind",
            "pen_precip", "score", "flags"]


def write_csv(ctx):
    path = ctx["outdir"] / "hourly.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for n in ctx["nights"]:
            for r in n["rows"]:
                h, m = r["h"], r["m"]
                row = {"night": n["date"].isoformat(), "in_window": n["in_window"], "new_moon_night": n["is_nm"],
                       "slot_start_local": h["slot"].strftime(f"%Y-%m-%d %H:%M {L}"), "dark": h["dark"],
                       "sun_alt": round(h["sun_alt"], 2), "moon_alt": round(h["moon_alt"], 2),
                       "moon_illum_pct": round(100 * h["illum"], 1), "score": r["score"], "flags": "; ".join(r["flags"])}
                for src in (r["nws"], r["t7"], r["om"]):
                    for k, v in src.items():
                        if k in CSV_COLS:
                            row[k] = round(v, 2) if isinstance(v, float) else v
                for k in ("cloud_blend", "cloud_src", "temp_f", "temp_f_src", "dew_f", "dew_f_src", "spread_f", "rh",
                          "rh_src", "wind_mph", "wind_mph_src", "gust_mph", "gust_mph_src", "pop", "pop_src",
                          "transp_src", "seeing_src"):
                    v = m.get(k)
                    row[k] = round(v, 2) if isinstance(v, float) else v
                for k, v in r["pen"].items():
                    row["pen_" + k] = round(v, 3)
                row["longrange_source"] = lr_label(r["om"].get("om_src"))
                w.writerow(row)
    return path


# ----------------------------------------------------------------------------- heatmap
def write_heatmap(ctx):
    nights = ctx["nights"]
    hours = C.HEATMAP_HOURS
    nr, nc = len(nights), len(hours)
    plt.style.use("dark_background")
    fig_h = max(4.5, 0.62 * nr + 2.6)
    fig, ax = plt.subplots(figsize=(15.5, fig_h), dpi=130)
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")
    cmap = plt.get_cmap("RdYlGn")
    for i, n in enumerate(nights):
        byh = {r["h"]["slot"].hour: r for r in n["rows"]}
        for j, hh in enumerate(hours):
            r = byh.get(hh)
            x, y = j, i
            if r is None or not r["h"]["dark"]:
                ax.add_patch(Rectangle((x, y), 1, 1, facecolor="#2a2f36", edgecolor="#0d1117", hatch="///", lw=0.8))
                ax.patches[-1].set_edgecolor("#454c55")
                ax.add_patch(Rectangle((x, y), 1, 1, fill=False, edgecolor="#0d1117", lw=1.5))
            elif r["score"] is None:
                ax.add_patch(Rectangle((x, y), 1, 1, facecolor="#3b3f46", edgecolor="#0d1117", lw=1.5))
                ax.text(x + .5, y + .5, "n/a", ha="center", va="center", fontsize=8, color="#9aa4ae")
            else:
                s = r["score"]
                ax.add_patch(Rectangle((x, y), 1, 1, facecolor=cmap(s / 10), edgecolor="#0d1117", lw=1.5))
                cb_ = r["m"].get("cloud_blend")
                ax.text(x + .5, y + (.40 if cb_ is not None else .5), f"{s:.1f}", ha="center", va="center", fontsize=9.5,
                        fontweight="bold", color="black")
                if cb_ is not None:
                    ax.text(x + .5, y + .74, f"☁ {cb_:.0f}%", ha="center", va="center", fontsize=6.8, color="black")
        if n["is_nm"]:
            ax.add_patch(Rectangle((0, i), nc, 1, fill=False, edgecolor="#7fd3ff", lw=2.6, zorder=5))
    idx = [i for i, n in enumerate(nights) if n["in_window"]]
    ax.set_xlim(0, nc)
    ax.set_ylim(nr, 0)
    ax.set_xticks([j + .5 for j in range(nc)])
    ax.set_xticklabels([datetime(2000, 1, 1, h).strftime("%-I %p").replace(" ", "\n") for h in hours], fontsize=9)
    ax.xaxis.tick_top()
    ax.set_yticks([i + .5 for i in range(nr)])
    labels = []
    for n in nights:
        lab = n["date"].strftime("%a %b %d")
        if n["is_nm"]:
            lab = "★ NEW MOON  " + lab
        labels.append(lab)
    ax.set_yticklabels(labels, fontsize=9.5)
    for t, n in zip(ax.get_yticklabels(), nights):
        if n["is_nm"]:
            t.set_color("#7fd3ff"); t.set_fontweight("bold")
        elif n["in_window"]:
            t.set_color("#e3d4ff")
        else:
            t.set_color("#9aa4ae")
    # right-hand panel: columns laid out from measured text widths so nothing overlaps
    vcol = {"Go": "#56d364", "Marginal": "#e3b341", "No-go": "#f85149", "No forecast yet": "#8b949e"}
    cols = [[], [], [], [], []]     # verdict, mean, confidence, model source, moon
    for i, n in enumerate(nights):
        verdict = n["verdict"] if n["has_data"] else "No forecast yet"
        conf = n["conf"].split(" ")[0] if n["has_data"] else "—"
        if n["has_data"] and n.get("lr_stale"):
            conf += "↓"
        mean = "—" if n["mean"] is None else f"{n['mean']:.1f}"
        vals = [(verdict, dict(color=vcol.get(verdict, "w"), fontweight="bold", fontsize=9.5)),
                (mean, dict(color="#c9d1d9", fontsize=9)),
                (conf, dict(color="#c9d1d9", fontsize=8.5)),
                (lr_short(n) if n["has_data"] else "—", dict(color="#9aa4ae", fontsize=8)),
                (f"{100*n['astro']['illum_midnight']:.0f}%", dict(color="#c9d1d9", fontsize=8.5))]
        for c, (txt, kw) in enumerate(vals):
            cols[c].append(ax.text(nc + 0.15, i + .5, txt, va="center", ha="left", **kw))
    heads = ["verdict", "mean", "confidence", "model", "moon"]
    head_t = [ax.text(nc + 0.15, -0.12, h, va="bottom", ha="left", fontsize=8, color="#8b949e") for h in heads]
    fig.subplots_adjust(left=0.13, right=0.72, top=0.86 if nr > 6 else 0.80, bottom=0.12)
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    x = nc + 0.15
    gap = 0.22
    for c in range(len(cols)):
        for t in cols[c] + [head_t[c]]:
            t.set_x(x)
        w_px = max(t.get_window_extent(rend).width for t in cols[c] + [head_t[c]])
        x0 = inv.transform((0, 0))[0]
        x += (inv.transform((w_px, 0))[0] - x0) + gap
    right_edge = x
    # window boundaries / label placed after the measured panel
    if idx:
        for yb in (min(idx), max(idx) + 1):
            ax.plot([-0.05 * nc - 4.2, right_edge], [yb, yb], color="#c9a7ff", lw=1.6, ls="--", clip_on=False, zorder=6)
        ax.text(right_edge + 0.05, (min(idx) + max(idx) + 1) / 2, f"dark window\n(new-moon night ±{ctx['days']})",
                rotation=270, ha="left", va="center", fontsize=9, color="#c9a7ff")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 10))
    cb = fig.colorbar(sm, ax=ax, orientation="horizontal", fraction=0.035, pad=0.04, aspect=50)
    cb.set_label("night-lapse score (0 = hopeless, 10 = perfect)   |   small number = blended cloud cover % used for scoring   |   hatched = not astronomically dark   |   n/a = no forecast data\n"
                 "model: OM = Open-Meteo, OM-ens = its ensemble, MET = MET Norway; ↓ = confidence lowered (cached data)", fontsize=8.5)
    loc = ctx["loc"]
    ax.set_title(f"Night-lapse conditions — {loc['name']} ({loc['lat']:.4f}, {loc['lon']:.4f}, {loc['elevation_m']:.0f} m)\n"
                 f"New moon {ctx['nm']:%a %b %d %H:%M} {L}  •  run {ctx['now']:%Y-%m-%d %H:%M} {L}  •  hours = slot start, {L}",
                 fontsize=12, pad=34, color="#e6edf3")
    path = ctx["outdir"] / "heatmap.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ----------------------------------------------------------------------------- summary.md
def _block_txt(n):
    if not n["block"]:
        return "—"
    a = n["block"][0][0]
    b = n["block"][-1][0] + timedelta(hours=1)
    avg = sum(x[1] for x in n["block"]) / len(n["block"])
    tag = "" if n["kind"] == "good" else " (best 2 h, <7)" if n["kind"] == "best2h" else " (best 1 h)"
    return f"{a:%H:%M}–{b:%H:%M} ({len(n['block'])} h, avg {avg:.1f}){tag}"


def _moon_txt(lst, d):
    return ", ".join(hm_rel(x, d) for x in lst) if lst else "—"


def _drivers(n):
    tot = {}
    k = 0
    for r in n["rows"]:
        if r["score"] is None:
            continue
        k += 1
        for p, v in r["pen"].items():
            tot[p] = tot.get(p, 0) + v
    if not k:
        return ""
    avg = sorted(((v / k, p) for p, v in tot.items()), reverse=True)
    return ", ".join(f"{p.replace('_', ' ')} −{v:.1f}" for v, p in avg if v >= 0.3) or "no significant penalties"


def write_summary(ctx):
    loc, nm = ctx["loc"], ctx["nm"]
    st = ctx["statuses"]
    lines = []
    A_ = lines.append
    A_(f"# Night-lapse forecast — {loc['name']}")
    A_("")
    A_(f"- **Run:** {ctx['now']:%a %b %d %Y, %H:%M} {L}")
    A_(f"- **Site:** {loc['address']} — {loc['lat']:.5f}, {loc['lon']:.5f}, elev {loc['elevation_m']:.0f} m "
       f"({loc['elevation_m']*3.28084:.0f} ft). Geocode: {loc['geocoder']}; elevation: {loc['elevation_source']}.")
    A_(f"- **New moon:** {nm:%a %b %d %Y %H:%M} {L} ({nm.astimezone(timezone.utc):%H:%M} UTC)")
    A_(f"- **New-moon night:** {ctx['nm_night']:%a %b %d} → {ctx['nm_night'] + timedelta(days=1):%a %b %d} "
       f"(rule: {C.NEW_MOON_NIGHT_RULE})")
    A_(f"- **Dark window (±{ctx['days']} nights):** evenings {ctx['w0']:%a %b %d} → {ctx['w1']:%a %b %d} "
       f"(last morning {ctx['w1'] + timedelta(days=1):%a %b %d})")
    A_("")
    A_("## Nightly summary")
    A_("")
    A_(f"Night = evening date. Times {L}; ⁺¹ = next morning. Best block = longest run of dark hours scoring ≥7 "
       "(else best 2-h block). 🌑 = new-moon night, **bold** = inside dark window. ★ after a shower code = its peak night.")
    A_("")
    A_("| Night | Verdict | Mean | Best block | Sunset | Astro dark | Sunrise | Moonrise | Moonset | Illum | True-dark h | Min dew spread | Showers | Confidence |")
    A_("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for n in ctx["nights"]:
        a = n["astro"]
        d = n["date"]
        name = d.strftime("%a %b %d")
        if n["in_window"]:
            name = f"**{name}**"
        if n["is_nm"]:
            name = "🌑 " + name
        mean = "—" if n["mean"] is None else f"{n['mean']:.1f}"
        dew = "—"
        if n["min_spread"]:
            dew = f"{n['min_spread'][0]:.1f}°F @ {n['min_spread'][1]:%H:%M}" + (" 🔥heater" if n["lens_heater"] else "")
        shw = ", ".join(shower_tag(s, d, ctx["showers"]) for s in n["active"]) or "—"
        verdict = n["verdict"] if n["has_data"] else "No forecast yet"
        conf = n["conf"] if n["has_data"] else "no forecast yet"
        A_(f"| {name} | {verdict} | {mean} | {_block_txt(n)} | {hm(a['sunset'])} | "
           f"{hm(a['astro_dark_start'])}–{hm_rel(a['astro_dark_end'], d)} | {hm_rel(a['sunrise'], d)} | "
           f"{_moon_txt(a['moonrise'], d)} | {_moon_txt(a['moonset'], d)} | {100*a['illum_midnight']:.0f}% | "
           f"{a['true_dark_h']:.1f} | {dew} | {shw} | {conf} |")
    A_("")
    A_("Illum = moon illumination at local midnight. True-dark h = astronomical darkness with the moon below the horizon. "
       "🔥heater = lens heater advised (min temp–dewpoint spread < 4 °F).")
    A_("")
    A_("## Notes")
    A_("")
    for n in ctx["nights"]:
        if not n["has_data"]:
            A_(f"- **{n['date']:%a %b %d}:** no forecast yet (astronomy only: {n['astro']['true_dark_h']:.1f} h true dark).")
            continue
        td = ", ".join(f"{x:%H:%M}–{hm_rel(y, n['date'])}" for x, y in n["astro"]["true_dark_intervals"]) or "none"
        srcs = []
        srcs.append(f"NWS {100*n['nws_frac']:.0f}% of dark hrs")
        srcs.append(f"7Timer {100*n['t7_frac']:.0f}%")
        lr = "; ".join(dict.fromkeys(lr_label(x) for x in n.get("lr_srcs") or [])) or "none"
        srcs.append(f"long-range model: {lr}")
        A_(f"- **{n['date']:%a %b %d}** — {n['verdict']}. Main penalties (avg per dark hr): {_drivers(n)}. "
           f"Moon-free dark: {td}. Sources: {', '.join(srcs)}.")
    A_("")
    _low_section(ctx, A_)
    A_("## Meteor showers (IMO 2026 calendar)")
    A_("")
    A_(f"| Shower | Active | Peak ({L}) | ZHR | Radiant (RA, Dec) | Peak night: radiant | Moon on peak night |")
    A_("|---|---|---|---|---|---|---|")
    for s in ctx["showers"]:
        sh = s["sh"]
        (am, ad), (bm, bd) = sh["active"]
        act = f"{date(2000, am, ad):%b %d}–{date(2000, bm, bd):%b %d}"
        rad = "—"
        if "rad_max" in s:
            rad = (f"{s['rad_alt_dusk']:.0f}° at dark start, max {s['rad_max']:.0f}° @ {s['rad_max_t']:%H:%M}; "
                   f"{s['rad_up_h']:.1f} dark h above 20°")
        moon = (f"{100*s.get('illum_night', s['illum_peak']):.0f}% lit; up during {s.get('rad_up_moon_h', 0):.1f} of those h "
                f"→ interference: {s.get('moon_label', 'n/a')}")
        A_(f"| {sh['name']} ({sh['code']}) | {act} | {s['peak']:%a %b %d %H:%M} ({s['peak_how']}); "
           f"night of {s['peak_night']:%b %d} | {sh['zhr']} | {s['ra']:.0f}°, {s['dec']:+.0f}° | {rad} | {moon} |")
    A_("")
    A_("Notes: " + " ".join(f"{s['sh']['code']}: {s['sh']['note']}." for s in ctx["showers"]))
    A_("")
    _skymap_section(ctx, A_)
    A_("## Data sources & caveats")
    A_("")
    n_ = st["NWS"]
    if n_.get("ok"):
        rg = n_.get("range_utc") or (None, None)
        A_(f"- **NWS** gridpoint {n_.get('grid','')} — {n_['mode']}; issued {fmt_utc(n_.get('updateTime'))}; "
           f"hourly data through {fmt_utc(rg[1])}.")
    else:
        A_(f"- **NWS: FAILED** ({n_.get('error')}). Clouds/temps came from Open-Meteo only.")
    t_ = st["7Timer"]
    if t_.get("ok"):
        rg = t_.get("range_utc") or (None, None)
        A_(f"- **7Timer ASTRO** — {t_['mode']}; init {fmt_utc(t_.get('init_utc'))}; 3-hourly values linearly "
           f"interpolated to hourly, covering {fmt_utc(rg[0])} → {fmt_utc(rg[1])}. Beyond that, transparency uses an "
           f"RH/visibility proxy and seeing is neutral ({C.WEIGHTS['neutral_fraction']:.0%} of its weight) — flagged in hourly.csv.")
    else:
        A_(f"- **7Timer: FAILED** ({t_.get('error')}). Transparency = RH/visibility proxy, seeing = neutral for all hours.")
    o_ = st["Open-Meteo"]
    A_("- **Long-range model chain** (fills the `om_*` columns in hourly.csv; the `longrange_source` column names the "
       f"source of every hour): Open-Meteo forecast → Open-Meteo ensemble → newest Open-Meteo cache < {C.OM_STALE_MAX_H:g} h "
       f"→ MET Norway. A cache < {C.OM_FRESH_H:g} h old is reused without calling the API; HTTP 429 is retried once.")
    for a in o_.get("attempts", []):
        A_(f"  - {a['step']}: {a['result']}" + (f" — {a['error']}" if a.get("error") else ""))
    rg = o_.get("range_utc") or (None, None)
    if o_.get("stale"):
        A_(f"- **⚠ Open-Meteo data is a CACHED copy, {o_['age_h']:.1f} h old** (fetched {fmt_utc(o_.get('fetched_utc'))}). "
           "Nights that use it have their confidence lowered one level and are marked ↓ in the heatmap.")
    if o_.get("kind") in ("om-ensemble",):
        A_("- Open-Meteo **ensemble** = member means: cloud layers from ECMWF IFS 0.25° ens, total cloud = mean of the "
           "available ensemble models' means (IFS + GEFS; caches from before 2026-10-04 also include ECMWF AIFS), temp/dew point/RH/visibility/wind/precip-probability from GEFS ens "
           "(PoP = % of members with ≥0.1 mm/h). Ensemble means smooth out cloud timing.")
    m_ = o_.get("met")
    if m_:
        mr = m_.get("range_utc") or (None, None)
        if o_.get("met_used"):
            A_(f"- **MET Norway locationforecast 2.0** (api.met.no, CC BY 4.0) — {m_['mode']}; model run updated "
               f"{fmt_utc(m_.get('updated_at'))}; covers {fmt_utc(mr[0])} → {fmt_utc(mr[1])}; filled {o_.get('met_hours', 0)} "
               "hours Open-Meteo could not supply. Hourly steps for ~2.5 days, then 6-hourly (linearly interpolated to hourly). "
               "Outside the Nordic area it has no visibility, gust or precipitation-probability fields, so on those hours the "
               "transparency proxy uses RH only and gust/PoP penalties come from NWS or are zero.")
        else:
            A_(f"- MET Norway: {m_['mode']}" + (f" ({m_.get('error')})" if m_.get("error") else "") + "; not needed for any hour.")
    if o_.get("ok"):
        A_(f"- Long-range model data available through {fmt_utc(rg[1])}.")
    else:
        A_(f"- **Long-range models: all FAILED.** No cloud-layer data; nights beyond NWS range show 'no forecast yet'.")
    A_(f"- Confidence: high ≤{C.CONF_HIGH_MAX_DAYS} days lead, medium 4–{C.CONF_MEDIUM_MAX_DAYS}, low beyond / model-only nights; "
       "'NWS partial' = NWS covers only part of the dark hours; nights that use cached Open-Meteo data drop one level.")
    A_("- Scores are only computed for whole-hour slots whose midpoint has the sun below −18°; the twilight edges are not scored.")
    A_("- Raw API responses for this run are saved in `raw/`. Full hourly inputs, penalties and sources: `hourly.csv`.")
    path = ctx["outdir"] / "summary.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _low_section(ctx, A_):
    thr = C.LOW_SCORE_THRESHOLD
    A_(f"## Below-threshold hours (dark-window nights, score < {thr:g})")
    A_("")
    low = ctx.get("low") or []
    flagged = [x for x in low if x["groups"]]
    nodata = [x for x in low if not x["has_data"]]
    if not flagged:
        nd = ", ".join(x["night"].strftime("%a %b %d") for x in nodata)
        A_(f"None: every scored dark hour on the dark-window nights is {thr:g} or higher."
           + (f" (No forecast yet for: {nd}.)" if nodata else ""))
        A_("")
        return
    A_("Penalties are on the 0–10 score scale. **cloud-driven** = clouds are the largest penalty; "
       "**not cloud-driven** hours may still be worth shooting.")
    A_("")
    for x in flagged:
        A_(f"- **{x['night']:%a %b %d}**")
        for g in x["groups"]:
            hs = g["hours"]
            a, b = hs[0]["slot"], hs[-1]["slot"] + timedelta(hours=1)
            lab = "cloud-driven" if g["cloud_driven"] else "not cloud-driven"
            scores = ", ".join(f"{h['score']:.1f}" for h in hs)
            A_(f"  - **{a:%H:%M}–{b:%H:%M} {L}** ({len(hs)} h, score {scores}) — **{lab}** (main cause: {g['primary']})")
            for h in hs:
                txt = "; ".join(f"{t} (−{v:.1f})" for _, v, t in h["items"])
                A_(f"    - {h['slot']:%H:%M} — {h['score']:.1f}: {txt}")
    rest = [x for x in low if not x["groups"] and x["has_data"]]
    if rest:
        A_(f"- Other window nights with no hours below {thr:g}: " + ", ".join(f"{x['night']:%a %b %d}" for x in rest) + ".")
    if nodata:
        A_("- No forecast yet: " + ", ".join(f"{x['night']:%a %b %d}" for x in nodata) + ".")
    A_("")


def _skymap_section(ctx, A_):
    A_("## Meteor radiant sky maps")
    A_("")
    if ctx.get("skymaps_error"):
        A_(f"Sky maps could not be produced: {ctx['skymaps_error']}")
        A_("")
        return
    maps = ctx.get("skymaps") or []
    if not maps:
        A_("No dark-window night has an active shower radiant above the horizon at mid-darkness.")
    else:
        A_("Each map is an all-sky view at the middle of astronomical darkness (rounded to the nearest hour). It shows the "
           "horizon, cardinal points, bright stars, constellation lines, the Milky Way outline, each active shower's radiant "
           f"(square), and the moon if it's up. Renderer: {maps[0]['renderer']}.")
        A_("")
        A_("| Night | Map time | File | Radiants above horizon (alt / az) | Below horizon | Moon |")
        A_("|---|---|---|---|---|---|")
        for p in maps:
            up = ", ".join(f"{r['code']}{'★' if r['peak_tonight'] else ''} {r['alt']:.0f}° / {r['az']:.0f}°"
                           for r in p["radiants"] if r["up"])
            dn = ", ".join(f"{r['code']} ({r['alt']:.0f}°)" for r in p["radiants"] if not r["up"]) or "—"
            m = p["moon"]
            mt = (f"{100*m['illum']:.0f}% lit, alt {m['alt']:.0f}°" if m["up"] else f"below horizon ({m['alt']:.0f}°)")
            A_(f"| {p['night']:%a %b %d} | {p['time']:%H:%M} {L} | `{p['file']}` | {up} | {dn} | {mt} |")
    for d, why in ctx.get("skymaps_skipped") or []:
        A_(f"- {d:%a %b %d}: no map ({why}).")
    A_("")


# ----------------------------------------------------------------------------- nights.json
def _avg_penalties(n):
    tot, k = {}, 0
    for r in n["rows"]:
        if r["score"] is None:
            continue
        k += 1
        for p, v in r["pen"].items():
            key = "clouds" if p in ("cloud", "high_cloud") else p
            tot[key] = tot.get(key, 0) + v
    return {p: v / k for p, v in tot.items()} if k else {}


def write_nights_json(ctx):
    """Structured per-night results (used by compare.py)."""
    import json
    out = {"site": ctx["loc"], "tz": C.TZ, "tz_label": L, "run_local": ctx["now"].isoformat(),
           "new_moon": ctx["nm"].isoformat(), "new_moon_night": ctx["nm_night"].isoformat(),
           "window": [ctx["w0"].isoformat(), ctx["w1"].isoformat()],
           "sources": {k: {kk: vv for kk, vv in v.items() if kk in ("mode", "error", "source", "kind", "stale", "age_h", "met_used", "met_hours", "range_utc")}
                       for k, v in ctx["statuses"].items()},
           "nights": []}
    for n in ctx["nights"]:
        a = n["astro"]
        pens = _avg_penalties(n)
        main = max(pens.items(), key=lambda kv: kv[1]) if pens else None
        dark = [r for r in n["rows"] if r["score"] is not None]
        mean_cloud = (sum(r["m"]["cloud_blend"] for r in dark) / len(dark)) if dark else None
        rh = [r["m"]["rh"] for r in dark if r["m"].get("rh") is not None]
        out["nights"].append({
            "date": n["date"].isoformat(), "in_window": n["in_window"], "is_new_moon_night": n["is_nm"],
            "verdict": n["verdict"], "mean_score": None if n["mean"] is None else round(n["mean"], 2),
            "best_block": _block_txt(n), "confidence": n["conf"],
            "astro_dark_start": a["astro_dark_start"].isoformat() if a["astro_dark_start"] else None,
            "astro_dark_end": a["astro_dark_end"].isoformat() if a["astro_dark_end"] else None,
            "astro_dark_h": round(a["astro_dark_h"], 2), "true_dark_h": round(a["true_dark_h"], 2),
            "illum_midnight": round(a["illum_midnight"], 4),
            "avg_penalties": {k: round(v, 3) for k, v in pens.items()},
            "main_penalty": None if not main else {"name": main[0], "avg": round(main[1], 3)},
            "mean_cloud_blend_pct": None if mean_cloud is None else round(mean_cloud, 1),
            "mean_rh_pct": round(sum(rh) / len(rh), 1) if rh else None,
            "min_spread_f": None if not n["min_spread"] else round(n["min_spread"][0], 1),
            "min_spread_at": None if not n["min_spread"] else n["min_spread"][1].isoformat(),
        })
    (ctx["outdir"] / "nights.json").write_text(json.dumps(out, indent=1))
