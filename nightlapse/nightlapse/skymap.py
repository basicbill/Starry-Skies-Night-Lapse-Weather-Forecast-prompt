"""All-sky meteor-radiant maps.

Primary renderer: VirtualSky (vendor/VirtualSky, https://github.com/slowe/VirtualSky) on a local HTML
page served from 127.0.0.1, screenshotted with headless Chrome/Chromium.  Every number printed on the
map (times, radiant/moon alt-az, illumination) is computed with Skyfield.  If Chrome or VirtualSky is
unavailable, a simpler matplotlib polar plot (no star field) is written instead and flagged.
"""
import functools, html, http.server, json, math, os, shutil, subprocess, threading
from datetime import timedelta, timezone

import numpy as np

from . import config as C
from . import astro as A
from . import showers as SH

VS_DIR = C.ROOT / "vendor" / "VirtualSky"
UTC = timezone.utc
COLOURS = ["#ff4fd8", "#ffd23f", "#4fe3ff", "#7dff6a", "#ff8a3d", "#c39bff", "#ff5f5f", "#ffffff"]


def find_browser():
    for b in C.SKYMAP_BROWSERS:
        p = shutil.which(b)
        if p:
            return p
    return None


def altaz(site, body_or_radec, dt):
    t = A.t_of(dt)
    if isinstance(body_or_radec, tuple):
        from skyfield.api import Star
        ra, de = body_or_radec
        body = Star(ra_hours=ra / 15.0, dec_degrees=de)
    else:
        body = body_or_radec
    alt, az, _ = site.obs.at(t).observe(body).apparent().altaz()
    return float(alt.degrees), float(az.degrees)


def compass(az):
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((az % 360) / 22.5 + 0.5) % 16]


def plan_for(n, t, site, ctx, file=None):
    """Everything drawn on one map for night n at local time t (all positions from Skyfield)."""
    rads = []
    for i, sh in enumerate(n["active"]):
        ra, de = SH.radiant_on(sh, n["date"])
        alt, az = altaz(site, (ra, de), t)
        det = next((x for x in ctx["showers"] if x["sh"]["code"] == sh["code"]), None)
        rads.append({"code": sh["code"], "name": sh["name"], "zhr": sh["zhr"], "ra": ra, "dec": de,
                     "alt": alt, "az": az, "up": alt > 0, "colour": COLOURS[i % len(COLOURS)],
                     "peak_tonight": bool(det and det["peak_night"] == n["date"])})
    malt, maz = altaz(site, A.MOON, t)
    moon = {"alt": malt, "az": maz, "illum": float(A.illum(A.t_of(t))), "up": malt > C.MOON_HORIZON_DEG}
    e = A.EARTH.at(A.t_of(t)).observe(A.MOON).apparent().radec()
    moon["ra"], moon["dec"] = e[0].hours * 15.0, e[1].degrees
    a, b = n["astro"]["astro_dark_start"], n["astro"]["astro_dark_end"]
    return {"night": n["date"], "time": t, "dark": (a, b), "mid_exact": t, "radiants": rads, "moon": moon,
            "file": file or f"radiant_{n['date'].isoformat()}.png"}


def plan_maps(ctx, site, only=None):
    """Decide which window nights get a map, and compute everything shown on it."""
    plans, skipped = [], []
    for n in ctx["nights"]:
        if not n["in_window"] or not n["active"]:
            continue
        if only is not None and n["date"] not in only:
            continue
        a, b = n["astro"]["astro_dark_start"], n["astro"]["astro_dark_end"]
        if not (a and b):
            skipped.append((n["date"], "no astronomical darkness")); continue
        mid = a + (b - a) / 2
        t = (mid + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
        p = plan_for(n, t, site, ctx)
        p["mid_exact"] = mid
        if not any(r["up"] for r in p["radiants"]):
            skipped.append((n["date"], "no active radiant above the horizon at mid-darkness")); continue
        plans.append(p)
    return plans, skipped


# ----------------------------------------------------------------------------- VirtualSky page
def _page(plan, loc):
    t = plan["time"]
    up = [r for r in plan["radiants"] if r["up"]]
    pointers = [{"ra": round(r["ra"], 3), "dec": round(r["dec"], 3), "label": "",
                 "colour": r["colour"], "d": 14} for r in up]
    m = plan["moon"]
    if m["up"]:
        pointers.append({"ra": round(m["ra"], 3), "dec": round(m["dec"], 3),
                         "label": "", "colour": "#f0f0f0", "d": 18})
    # HTML overlay at Skyfield-computed positions, using VirtualSky's polar formula
    # (r = R*(90-alt)/90, x = R - r*sin(az), y = R - r*cos(az)); VirtualSky's own square should sit inside each ring.
    R = C.SKYMAP_PX / 2
    def xy(alt, az):
        rr = R * (90 - alt) / 90
        return R - rr * math.sin(math.radians(az)), R - rr * math.cos(math.radians(az))
    marks = []
    items = [(r["colour"], f"{r['code']} radiant {r['alt']:.0f}°", r["alt"], r["az"]) for r in up]
    if m["up"]:
        items.append(("#f0f0f0", f"Moon {100*m['illum']:.0f}% lit", m["alt"], m["az"]))
    placed = [(xy(al, az)[0] - 19, xy(al, az)[1] - 19, xy(al, az)[0] + 19, xy(al, az)[1] + 19)
              for _, _, al, az in items]   # rings + label boxes already used (x0, y0, x1, y1)
    W_ = 2 * R
    def free(box):
        if box[0] < 4 or box[2] > W_ - 4 or box[1] < 4 or box[3] > W_ - 4:
            return False
        return all(box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3] for b in placed)
    for col, lab, al, az in items:
        x, y = xy(al, az)
        wlab, hlab = 12.5 * len(lab), 28
        cands = []
        for dy in (0, 30, -30, 60, -60, 90, -90):
            cands += [(x + 24, y - 14 + dy), (x - 24 - wlab, y - 14 + dy)]
        bx, by = next(((cx, cy) for cx, cy in cands if free((cx, cy, cx + wlab, cy + hlab))), cands[0])
        placed.append((bx, by, bx + wlab, by + hlab))
        placed.append((x - 19, y - 19, x + 19, y + 19))
        marks.append(f"<div class='ring' style='left:{x-19:.0f}px;top:{y-19:.0f}px;border-color:{col}'></div>"
                     f"<div class='lab' style='left:{bx:.0f}px;top:{by:.0f}px;color:{col}'>{html.escape(lab)}</div>")
    for txt, x, y in (("N", R, 26), ("S", R, 2 * R - 26), ("E", 26, R), ("W", 2 * R - 26, R)):
        marks.append(f"<div class='card' style='left:{x-20:.0f}px;top:{y-20:.0f}px'>{txt}</div>")
    rows = []
    for r in plan["radiants"]:
        pos = (f"alt {r['alt']:.0f}°, az {r['az']:.0f}° ({compass(r['az'])})" if r["up"]
               else f"below horizon (alt {r['alt']:.0f}°)")
        rows.append(f"<tr><td><span class='sw' style='background:{r['colour'] if r['up'] else '#555'}'></span>"
                    f"{html.escape(r['name'])} ({r['code']}){' — <b>PEAK NIGHT</b>' if r['peak_tonight'] else ''}</td>"
                    f"<td>ZHR {html.escape(r['zhr'])}</td><td>{pos}</td></tr>")
    moon_txt = (f"Moon {100*m['illum']:.0f}% lit, alt {m['alt']:.0f}°, az {m['az']:.0f}° ({compass(m['az'])})"
                if m["up"] else f"Moon below horizon (alt {m['alt']:.0f}°, {100*m['illum']:.0f}% lit)")
    a, b = plan["dark"]
    cfg = {"id": "sky", "projection": "polar", "latitude": loc["lat"], "longitude": loc["lon"],
           "clock": t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "live": False, "ground": True,
           "gradient": False, "showstars": True, "magnitude": 4.8, "scalestars": 1.6, "showstarlabels": True,
           "constellations": True, "constellationlabels": True, "constellationwidth": 1.2,
           "showgalaxy": True, "galaxywidth": 1.5, "showplanets": True, "showplanetlabels": True,
           "cardinalpoints": True, "gridlines_az": True, "showdate": False, "showposition": False,
           "keyboard": False, "mouse": False, "fontsize": "17px", "credit": False, "lang": "en",
           "width": C.SKYMAP_PX, "height": C.SKYMAP_PX}
    title = (f"{html.escape(loc['name'])} — all-sky view {t:%a %b %d %Y, %H:%M} {C.TZ_LABEL}")
    sub = (f"Night of {plan['night']:%a %b %d}: middle of astronomical darkness ({a:%H:%M}–{b:%H:%M} {C.TZ_LABEL}, "
           f"mid {plan['mid_exact']:%H:%M}) rounded to the hour")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="/vendor/VirtualSky/stuquery.min.js"></script>
<script src="/vendor/VirtualSky/virtualsky.min.js"></script>
<style>
 body{{margin:0;background:#0d1117;color:#e6edf3;font-family:'DejaVu Sans',Arial,sans-serif;width:{C.SKYMAP_PX + 40}px}}
 h1{{font-size:24px;margin:14px 20px 2px}} .sub{{font-size:16px;color:#9aa4ae;margin:0 20px 10px}}
 #wrap{{position:relative;width:{C.SKYMAP_PX}px;height:{C.SKYMAP_PX}px;margin:0 20px}}
 #sky{{width:{C.SKYMAP_PX}px;height:{C.SKYMAP_PX}px}}
 .virtualskybutton{{display:none!important}}
 .ring{{position:absolute;width:32px;height:32px;border:3px solid;border-radius:50%;z-index:10}}
 .lab{{position:absolute;font-size:21px;font-weight:bold;z-index:10;text-shadow:0 0 4px #000,0 0 4px #000;white-space:nowrap}}
 .card{{position:absolute;width:40px;height:40px;line-height:40px;text-align:center;font-size:30px;font-weight:bold;
        color:#ffb86b;z-index:10;text-shadow:0 0 5px #000}}
 table{{margin:10px 20px;font-size:17px;border-collapse:collapse;width:{C.SKYMAP_PX}px}}
 td{{padding:4px 14px 4px 0;vertical-align:top}} td:first-child,td:last-child{{white-space:nowrap}}
 .sw{{display:inline-block;width:14px;height:14px;margin-right:8px;vertical-align:-1px}}
 .foot{{font-size:13px;color:#8b949e;margin:6px 20px 14px}} .moon{{font-size:17px;margin:2px 20px}}
</style></head><body>
<h1>{title}</h1><div class="sub">{sub}</div>
<div id="wrap"><div id="sky"></div>{''.join(marks)}</div>
<table>{''.join(rows)}</table>
<div class="moon">{moon_txt}</div>
<div class="foot">Zenith-centred polar (azimuthal-equidistant) all-sky view, N at top, E at left, as seen lying on your back.
Coloured squares (VirtualSky) inside rings (Skyfield) = shower radiants (positions from the IMO 2026 calendar, Table 6 drift). Alt/az and moon values computed with Skyfield/DE421.
Sky rendered with VirtualSky (slowe/VirtualSky) in headless Chrome; Milky Way outline from VirtualSky's galaxy.json.</div>
<script>
S(document).ready(function(){{
  var sky = S.virtualsky({json.dumps(cfg)});
  var ptrs = {json.dumps(pointers)};
  for (var i = 0; i < ptrs.length; i++) sky.addPointer(ptrs[i]);
  sky.draw();
}});
</script></body></html>"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class _Handler(_Quiet):
    """/vendor/... -> ROOT/vendor, everything else -> the run's skymap_html directory."""
    pages_dir = None

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/vendor/"):
            return str(C.ROOT / path.lstrip("/"))
        return str(self.pages_dir / path.lstrip("/"))


def _serve(pages_dir):
    h = type("H", (_Handler,), {"pages_dir": pages_dir})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def render_virtualsky(plans, ctx):
    browser = find_browser()
    if not browser:
        raise RuntimeError("no Chrome/Chromium binary found")
    if not (VS_DIR / "virtualsky.min.js").exists():
        raise RuntimeError(f"VirtualSky not found at {VS_DIR}")
    html_dir = ctx["outdir"] / "skymap_html"
    html_dir.mkdir(parents=True, exist_ok=True)
    srv = _serve(html_dir)
    port = srv.server_address[1]
    env = dict(os.environ, TZ=C.TZ)
    try:
        for p in plans:
            page = html_dir / p["file"].replace(".png", ".html")
            page.write_text(_page(p, ctx["loc"]))
            rel = page.name
            out = ctx["outdir"] / p["file"]
            if out.exists():
                out.unlink()
            h = C.SKYMAP_PX + 120 + 34 * len(p["radiants"]) + 110
            cmd = [browser, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                   f"--window-size={C.SKYMAP_PX + 40},{h}", "--virtual-time-budget=10000",
                   f"--screenshot={out}", f"http://127.0.0.1:{port}/{rel}"]
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
            if not out.exists() or out.stat().st_size < 20000:
                raise RuntimeError(f"Chrome screenshot failed: {r.stderr[-300:]}")
            p["renderer"] = "VirtualSky"
            p["path"] = out
    finally:
        srv.shutdown()


# ----------------------------------------------------------------------------- fallback
def render_matplotlib(plans, ctx, reason):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for p in plans:
        fig = plt.figure(figsize=(9, 9.6), dpi=120)
        fig.patch.set_facecolor("#0d1117")
        ax = fig.add_subplot(projection="polar")
        ax.set_facecolor("#0b1a2b")
        ax.set_theta_zero_location("N"); ax.set_theta_direction(1)   # E to the left, looking up
        ax.set_rlim(0, 90); ax.set_rticks([30, 60]); ax.set_yticklabels([])
        ax.set_xticks(np.radians([0, 90, 180, 270])); ax.set_xticklabels(["N", "E", "S", "W"], color="w", fontsize=14)
        ax.grid(color="#2c3e50")
        for r in p["radiants"]:
            if r["up"]:
                ax.plot(np.radians(r["az"]), 90 - r["alt"], "s", ms=12, color=r["colour"])
                ax.annotate(f"{r['code']} ({r['alt']:.0f}°)", (np.radians(r["az"]), 90 - r["alt"]),
                            xytext=(8, 8), textcoords="offset points", color=r["colour"], fontsize=12)
        m = p["moon"]
        if m["up"]:
            ax.plot(np.radians(m["az"]), 90 - m["alt"], "o", ms=16, color="#eee")
        ax.set_title(f"{ctx['loc']['name']} — {p['time']:%a %b %d %H:%M} {C.TZ_LABEL}\n"
                     f"(fallback plot, no star field: {reason[:60]})", color="w", fontsize=12)
        out = ctx["outdir"] / p["file"]
        fig.savefig(out, facecolor=fig.get_facecolor())
        plt.close(fig)
        p["renderer"] = f"matplotlib fallback ({reason})"
        p["path"] = out


def build(ctx, site, only=None):
    plans, skipped = plan_maps(ctx, site, only)
    if plans:
        try:
            render_virtualsky(plans, ctx)
        except Exception as e:  # noqa
            render_matplotlib(plans, ctx, str(e))
    return plans, skipped
