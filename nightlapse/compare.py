#!/usr/bin/env python3
"""Side-by-side comparison of two nightlapse runs (reads each run's nights.json).

  python3 /workspace/nightlapse/compare.py RUN_DIR_A RUN_DIR_B --out OUT_DIR [--keys greenvalley,stcharles]

Writes OUT_DIR/comparison.md and OUT_DIR/comparison.png.  Optional context: NASA POWER 2001-2020
October climatology (cloud amount, RH, dew point) and the light-pollution table in data/light_pollution.json.
"""
import argparse, json, os, sys
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
try:
    import matplotlib, requests  # noqa
except ImportError:
    if VENV_PY.exists() and not os.environ.get("NIGHTLAPSE_REEXEC"):
        os.environ["NIGHTLAPSE_REEXEC"] = "1"
        os.execv(str(VENV_PY), [str(VENV_PY), __file__, *sys.argv[1:]])
    raise
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests


def load(d):
    return json.loads((Path(d) / "nights.json").read_text())


def hm(s):
    return datetime.fromisoformat(s).strftime("%H:%M") if s else "—"


def power_climatology(lat, lon, raw_path):
    url = ("https://power.larc.nasa.gov/api/temporal/climatology/point?parameters=CLOUD_AMT_NIGHT,CLOUD_AMT,RH2M,T2MDEW"
           f"&community=RE&longitude={lon:.4f}&latitude={lat:.4f}&format=JSON")
    try:
        r = requests.get(url, timeout=90)
        d = r.json()
        raw_path.write_text(json.dumps(d))
        p = d["properties"]["parameter"]
        return {k: p[k]["OCT"] for k in p}, d["header"].get("range"), None
    except Exception as e:  # noqa
        return None, None, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keys", default="greenvalley,stcharles", help="keys into data/light_pollution.json")
    args = ap.parse_args()
    A, B = load(args.a), load(args.b)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    na, nb = A["site"]["name"], B["site"]["name"]
    ka, kb = args.keys.split(",")
    w0, w1 = date.fromisoformat(A["window"][0]), date.fromisoformat(A["window"][1])
    da = {n["date"]: n for n in A["nights"]}
    db = {n["date"]: n for n in B["nights"]}
    dates = [d for d in sorted(set(da) | set(db)) if w0 <= date.fromisoformat(d) <= w1]

    L = []
    L.append(f"# Night-lapse comparison — {na} vs {nb}")
    L.append("")
    L.append(f"- Runs: {na} at {A['run_local'][:16]} {A['tz_label']}; {nb} at {B['run_local'][:16]} {B['tz_label']}")
    L.append(f"- New moon {A['new_moon'][:16]} ({A['tz_label']}) / {B['new_moon'][:16]} ({B['tz_label']}); new-moon night "
             f"{A['new_moon_night']} (both, 'nearest' rule); window evenings {A['window'][0]} → {A['window'][1]}")
    L.append(f"- Times are each site's local time ({na}: {A['tz_label']}, {nb}: {B['tz_label']}).")
    L.append("")
    L.append("| Night | Mean score | Best block | Astro dark (local) | Astro-dark h | True-dark h | Main penalty (avg/dark hr) | Mean cloud % | Min dew spread |")
    L.append("|---|---|---|---|---|---|---|---|---|")

    def cell(n, f):
        return f(n) if n else "—"
    for d in dates:
        x, y = da.get(d), db.get(d)
        tag = "🌑 " if (x and x["is_new_moon_night"]) else ""
        def fmt(n, site):
            mp = n["main_penalty"]
            return (f"{site}: {'—' if n['mean_score'] is None else format(n['mean_score'], '.1f')}",
                    f"{site}: {n['best_block']}",
                    f"{site}: {hm(n['astro_dark_start'])}–{hm(n['astro_dark_end'])}",
                    f"{site}: {n['astro_dark_h']:.1f}", f"{site}: {n['true_dark_h']:.1f}",
                    f"{site}: {mp['name']} −{mp['avg']:.1f}" if mp else f"{site}: —",
                    f"{site}: {'—' if n['mean_cloud_blend_pct'] is None else format(n['mean_cloud_blend_pct'], '.0f')}",
                    f"{site}: {'—' if n['min_spread_f'] is None else format(n['min_spread_f'], '.1f') + '°F'}")
        fa = fmt(x, "GV") if x else ["GV: —"] * 8
        fb = fmt(y, "SC") if y else ["SC: —"] * 8
        L.append(f"| {tag}{date.fromisoformat(d):%a %b %d} | " + " | ".join(f"{p}<br>{q}" for p, q in zip(fa, fb)) + " |")
    L.append("")
    L.append(f"GV = {na}, SC = {nb}. Mean cloud % = blended total cloud over scored dark hours. "
             "True-dark h = astronomical darkness with the moon down (astronomy only, weather-independent).")
    L.append("")
    # summaries
    def avg(site, key):
        v = [n[key] for n in site["nights"] if n["in_window"] and n[key] is not None]
        return sum(v) / len(v) if v else None
    L.append("## Window averages (from this forecast)")
    L.append("")
    L.append("| | " + na + " | " + nb + " |")
    L.append("|---|---|---|")
    for lab, key, f in (("Mean score", "mean_score", ".2f"), ("Astro-dark h / night", "astro_dark_h", ".2f"),
                        ("True-dark h / night", "true_dark_h", ".2f"), ("Mean blended cloud %", "mean_cloud_blend_pct", ".1f"),
                        ("Mean RH % (dark hrs)", "mean_rh_pct", ".1f"), ("Min dew spread °F (avg of nightly minima)", "min_spread_f", ".1f")):
        a_, b_ = avg(A, key), avg(B, key)
        L.append(f"| {lab} | {'—' if a_ is None else format(a_, f)} | {'—' if b_ is None else format(b_, f)} |")
    L.append("")
    # climatology
    L.append("## Climatology context (NASA POWER, October means, 2001–2020)")
    L.append("")
    ca, ra, ea = power_climatology(A["site"]["lat"], A["site"]["lon"], out / f"power_{ka}.json")
    cb, rb, eb = power_climatology(B["site"]["lat"], B["site"]["lon"], out / f"power_{kb}.json")
    if ca and cb:
        L.append(f"| October mean | {na} | {nb} |")
        L.append("|---|---|---|")
        for k, lab in (("CLOUD_AMT_NIGHT", "Night-time cloud amount %"), ("CLOUD_AMT", "Cloud amount % (all hours)"),
                       ("RH2M", "Relative humidity at 2 m %"), ("T2MDEW", "Dew/frost point at 2 m °C")):
            L.append(f"| {lab} | {ca.get(k)} | {cb.get(k)} |")
        L.append("")
        L.append(f"Source: NASA POWER Climatology API (MERRA-2 / CERES SYN1deg), period: {ra}. Raw JSON saved alongside.")
    else:
        L.append(f"NASA POWER unavailable: {ea or eb}")
    L.append("")
    # light pollution
    L.append("## Light pollution (not modelled by the tool)")
    L.append("")
    lp = json.loads((ROOT / "data" / "light_pollution.json").read_text())
    if ka in lp and kb in lp:
        L.append(f"| | {na} | {nb} |")
        L.append("|---|---|---|")
        for k, lab in (("bortle", "Bortle class (est.)"), ("sqm_mag_arcsec2", "Sky quality, mag/arcsec² (est.)"),
                       ("brightness_mcd_m2", "Total sky brightness, mcd/m²"), ("artificial_ucd_m2", "Artificial brightness, µcd/m²")):
            L.append(f"| {lab} | {lp[ka][k]} | {lp[kb][k]} |")
        L.append("")
        L.append("Source: " + lp["_source"])
    else:
        L.append("Unknown (no figures on file).")
    L.append("")
    srcs = []
    for nm_, S in ((na, A), (nb, B)):
        for k, v in S["sources"].items():
            if v.get("mode") != "live":
                srcs.append(f"- {nm_} {k}: {v.get('mode')} — {v.get('primary_error') or v.get('error')}")
    L.append("## Data-source notes")
    L.append("")
    L.extend(srcs or ["- All sources live for both sites."])
    (out / "comparison.md").write_text("\n".join(L) + "\n")

    # ---------------- chart
    plt.style.use("dark_background")
    fig, axs = plt.subplots(3, 1, figsize=(12, 11), dpi=120, sharex=True)
    fig.patch.set_facecolor("#0d1117")
    x = np.arange(len(dates)); w = 0.38
    ca_, cb_ = "#f0b45a", "#5ab4f0"
    def series(dct, key):
        return [np.nan if (dct.get(d) is None or dct[d][key] is None) else dct[d][key] for d in dates]
    for ax in axs:
        ax.set_facecolor("#0d1117"); ax.grid(axis="y", color="#30363d", lw=0.6); ax.set_axisbelow(True)
    sa, sb = series(da, "mean_score"), series(db, "mean_score")
    b1 = axs[0].bar(x - w/2, sa, w, color=ca_, label=na); b2 = axs[0].bar(x + w/2, sb, w, color=cb_, label=nb)
    axs[0].bar_label(b1, fmt="%.1f", fontsize=8.5, padding=2); axs[0].bar_label(b2, fmt="%.1f", fontsize=8.5, padding=2)
    axs[0].set_ylim(0, 12.5); axs[0].set_ylabel("mean score (0–10)")
    axs[0].axhline(7, color="#56d364", ls="--", lw=1); axs[0].text(1.003, 7, "≥7\ngood", color="#56d364", fontsize=8, va="center",
                                                                          transform=axs[0].get_yaxis_transform())
    axs[0].legend(loc="upper center", ncol=2, fontsize=9.5, framealpha=0.3)
    ka_, kb_ = series(da, "mean_cloud_blend_pct"), series(db, "mean_cloud_blend_pct")
    c1 = axs[1].bar(x - w/2, ka_, w, color=ca_); c2 = axs[1].bar(x + w/2, kb_, w, color=cb_)
    axs[1].bar_label(c1, fmt="%.0f", fontsize=8.5, padding=2); axs[1].bar_label(c2, fmt="%.0f", fontsize=8.5, padding=2)
    axs[1].set_ylim(0, 105); axs[1].set_ylabel("mean blended cloud %\n(scored dark hours)")
    ta, tb = series(da, "true_dark_h"), series(db, "true_dark_h")
    aa, ab = series(da, "astro_dark_h"), series(db, "astro_dark_h")
    axs[2].bar(x - w/2, aa, w, color=ca_, alpha=0.35, label="astronomical dark")
    axs[2].bar(x + w/2, ab, w, color=cb_, alpha=0.35)
    t1 = axs[2].bar(x - w/2, ta, w, color=ca_, label="true dark (moon down)")
    t2 = axs[2].bar(x + w/2, tb, w, color=cb_)
    axs[2].bar_label(t1, fmt="%.1f", fontsize=8.5, padding=2); axs[2].bar_label(t2, fmt="%.1f", fontsize=8.5, padding=2)
    axs[2].set_ylim(0, 13.5); axs[2].set_ylabel("hours of darkness")
    axs[2].legend(loc="upper center", ncol=2, fontsize=9.5, framealpha=0.3,
                  title="faint = astronomical dark, solid = true dark (moon down)", title_fontsize=9)
    labels = []
    for d in dates:
        lab = date.fromisoformat(d).strftime("%a\n%b %d")
        if da.get(d) and da[d]["is_new_moon_night"]:
            lab = "★ NM\n" + lab
        labels.append(lab)
    axs[2].set_xticks(x); axs[2].set_xticklabels(labels, fontsize=9)
    axs[0].set_title(f"{na} vs {nb} — dark window {A['window'][0]} → {A['window'][1]} (evening dates)\n"
                     f"forecast runs {datetime.fromisoformat(A['run_local']):%b %d %H:%M} {A['tz_label']} / "
                     f"{datetime.fromisoformat(B['run_local']):%b %d %H:%M} {B['tz_label']}",
                     fontsize=12, color="#e6edf3")
    fig.tight_layout()
    fig.savefig(out / "comparison.png", facecolor=fig.get_facecolor())
    print(f"wrote {out/'comparison.md'} and {out/'comparison.png'}")


if __name__ == "__main__":
    main()
