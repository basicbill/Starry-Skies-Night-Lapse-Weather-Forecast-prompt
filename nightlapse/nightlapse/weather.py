"""Fetch + normalise hourly weather from NWS, 7Timer ASTRO and Open-Meteo.

Every value is keyed by the UTC top-of-hour it is valid for.  Failures never raise;
each fetch returns (hourly_dict, status_dict).
"""
import json, re, time
from datetime import datetime, timedelta, timezone
import requests

from . import config as C

UTC = timezone.utc


# ----------------------------------------------------------------------------- http + cache
def _get_json(url, headers=None, want_response=False):
    """GET JSON.  Retries transient errors C.HTTP_RETRIES times; on HTTP 429 retries exactly once after a
    short backoff (server Retry-After if given, capped at 30 s).  Returns data (or (data, response))."""
    h = {"User-Agent": C.USER_AGENT, "Accept": "application/geo+json, application/json"}
    h.update(headers or {})
    last, tried_429, i = None, False, 0
    while i <= C.HTTP_RETRIES:
        try:
            r = requests.get(url, headers=h, timeout=C.HTTP_TIMEOUT)
            if r.status_code == 304 and want_response:
                return None, r
            if r.status_code in (200, 203):
                try:
                    data = r.json()
                except ValueError:
                    # 7Timer sometimes returns slightly non-standard JSON
                    data = json.loads(re.sub(r",\s*([\]}])", r"\1", r.text))
                if isinstance(data, dict) and data.get("error"):
                    raise RuntimeError(f"API error: {data.get('reason')}")
                return (data, r) if want_response else data
            last = f"HTTP {r.status_code}: {r.text[:160]!r}"
            if r.status_code == 429 and not tried_429:
                tried_429 = True
                try:
                    wait = min(float(r.headers.get("Retry-After", C.RETRY_429_BACKOFF_S)), 30.0)
                except ValueError:
                    wait = C.RETRY_429_BACKOFF_S
                time.sleep(wait)
                continue
            if r.status_code == 429:
                last += f" (also after 1 retry, {C.RETRY_429_BACKOFF_S:g} s backoff)"
            if r.status_code in (400, 401, 403, 404, 429):
                break
        except Exception as e:  # noqa
            last = f"{type(e).__name__}: {e}"
            if "API error" in str(e):
                break
        time.sleep(2 * (i + 1))
        i += 1
    raise RuntimeError(last or "unknown error")


def fetch_cached(name, url, raw_dir, headers=None):
    """Live fetch; on failure fall back to the cached copy if fresh enough.
    Returns (data, status) where status = {'ok','mode','fetched_utc','error'}."""
    cache = C.CACHE_DIR / f"{name}.json"
    try:
        data = _get_json(url, headers)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        blob = {"fetched_utc": now, "url": url, "data": data}
        C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(blob))
        (raw_dir / f"{name}.json").write_text(json.dumps(blob))
        return data, {"ok": True, "mode": "live", "fetched_utc": now, "error": None}
    except Exception as e:  # noqa
        err = str(e)
        if cache.exists():
            blob = json.loads(cache.read_text())
            age_h = (datetime.now(UTC) - datetime.fromisoformat(blob["fetched_utc"])).total_seconds() / 3600
            if age_h <= C.MAX_CACHE_AGE_H:
                return blob["data"], {"ok": True, "mode": f"cached ({age_h:.1f} h old)",
                                      "fetched_utc": blob["fetched_utc"], "error": err}
        return None, {"ok": False, "mode": "failed", "fetched_utc": None, "error": err}


def _hour(dt):
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


# ----------------------------------------------------------------------------- NWS
_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def _expand(valid_time):
    start_s, dur_s = valid_time.split("/")
    start = datetime.fromisoformat(start_s)
    m = _DUR.fullmatch(dur_s)
    hours = int(m.group(1) or 0) * 24 + int(m.group(2) or 0) + (1 if m.group(3) else 0)
    return [_hour(start) + timedelta(hours=i) for i in range(max(hours, 1))]


def _conv(v, uom):
    if v is None:
        return None
    if uom.endswith("degC"):
        return v * 9 / 5 + 32
    if uom.endswith("km_h-1"):
        return v / 1.609344
    if uom.endswith("m_s-1"):
        return v * 2.236936
    return v


NWS_FIELDS = {"skyCover": "nws_sky", "temperature": "nws_temp_f", "dewpoint": "nws_dew_f",
              "relativeHumidity": "nws_rh", "windSpeed": "nws_wind_mph", "windGust": "nws_gust_mph",
              "probabilityOfPrecipitation": "nws_pop"}


def fetch_nws(lat, lon, raw_dir):
    hourly = {}
    pts, st = fetch_cached("nws_points", f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", raw_dir)
    if pts is None:
        return hourly, {**st, "source": "NWS"}
    try:
        grid_url = pts["properties"]["forecastGridData"]
    except Exception as e:  # noqa
        return hourly, {"ok": False, "mode": "failed", "error": f"bad /points response: {e}", "source": "NWS"}
    g, st = fetch_cached("nws_grid", grid_url, raw_dir)
    st["source"] = "NWS"
    st["grid"] = grid_url
    if g is None:
        return hourly, st
    p = g.get("properties", {})
    st["updateTime"] = p.get("updateTime")
    for k, col in NWS_FIELDS.items():
        layer = p.get(k) or {}
        uom = layer.get("uom", "")
        for item in layer.get("values", []):
            val = _conv(item.get("value"), uom)
            for h in _expand(item["validTime"]):
                hourly.setdefault(h, {})[col] = val
    sky = [h for h, v in hourly.items() if v.get("nws_sky") is not None]
    if sky:   # cloud cover is what makes an hour scoreable; PoP etc. can run a few hours longer
        st["range_utc"] = (min(sky).isoformat(), max(sky).isoformat())
    if hourly:
        st["range_all_utc"] = (min(hourly).isoformat(), max(hourly).isoformat())
    return hourly, st


# ----------------------------------------------------------------------------- 7Timer ASTRO
T7_CLOUD_PCT = {1: 3, 2: 12.5, 3: 25, 4: 37.5, 5: 50, 6: 62.5, 7: 75, 8: 87.5, 9: 97}
T7_WIND_MS = {1: 0.15, 2: 1.85, 3: 5.7, 4: 9.4, 5: 14.0, 6: 20.85, 7: 28.55, 8: 34.0}


def _t7_rh_pct(code):
    if code is None:
        return None
    if code >= 16:
        return 100.0
    if code == 15:
        return 97.0
    return 2.5 + (code + 4) * 5


def fetch_7timer(lat, lon, raw_dir):
    url = (f"https://www.7timer.info/bin/astro.php?lon={lon:.3f}&lat={lat:.3f}"
           f"&ac=0&unit=metric&output=json&tzshift=0")
    d, st = fetch_cached("7timer_astro", url, raw_dir)
    st["source"] = "7Timer ASTRO"
    hourly = {}
    if d is None:
        return hourly, st
    try:
        init = datetime.strptime(d["init"], "%Y%m%d%H").replace(tzinfo=UTC)
        st["init_utc"] = init.isoformat()
        pts = []
        for row in d["dataseries"]:
            t = init + timedelta(hours=int(row["timepoint"]))
            def ok(v):
                return None if v is None or v == -9999 else v
            pts.append((t, {
                "t7_cloud_code": ok(row.get("cloudcover")),
                "t7_seeing": ok(row.get("seeing")),
                "t7_transp": ok(row.get("transparency")),
                "t7_lifted_index": ok(row.get("lifted_index")),
                "t7_rh2m_code": ok(row.get("rh2m")),
                "t7_wind_code": ok((row.get("wind10m") or {}).get("speed")),
            }))
        pts.sort(key=lambda x: x[0])
        # linear interpolation between 3-hourly points; hold +/-1 h at the ends
        start, end = pts[0][0] - timedelta(hours=1), pts[-1][0] + timedelta(hours=1)
        h = start
        while h <= end:
            before = [p for p in pts if p[0] <= h]
            after = [p for p in pts if p[0] >= h]
            b = before[-1] if before else after[0]
            a = after[0] if after else before[-1]
            span = (a[0] - b[0]).total_seconds()
            w = 0 if span == 0 else (h - b[0]).total_seconds() / span
            rec = {}
            for k in b[1]:
                vb, va = b[1][k], a[1][k]
                if vb is None or va is None:
                    rec[k] = vb if va is None else va
                else:
                    rec[k] = vb + (va - vb) * w
            rec["t7_cloud_pct"] = None if rec["t7_cloud_code"] is None else _interp_map(T7_CLOUD_PCT, rec["t7_cloud_code"])
            rec["t7_rh_pct"] = None if rec["t7_rh2m_code"] is None else _t7_rh_pct(round(rec["t7_rh2m_code"]))
            rec["t7_wind_mph"] = None if rec["t7_wind_code"] is None else _interp_map(T7_WIND_MS, rec["t7_wind_code"]) * 2.236936
            hourly[h] = rec
            h += timedelta(hours=1)
        st["range_utc"] = (min(hourly).isoformat(), max(hourly).isoformat())
    except Exception as e:  # noqa
        st.update(ok=False, mode="parse-failed", error=f"7Timer parse error: {e}")
        hourly = {}
    return hourly, st


def _interp_map(m, code):
    lo = int(code)
    hi = min(lo + 1, max(m))
    lo = max(lo, min(m))
    f = code - int(code)
    return m[lo] + (m[hi] - m[lo]) * f


# ----------------------------------------------------------------------------- long-range models
# Chain (each step only if the previous produced nothing):
#   1. Open-Meteo forecast API   (cache < OM_FRESH_H h is reused instead of calling)
#   2. Open-Meteo ensemble API   (one request, all models + variables; same fresh-cache rule)
#   3. newest Open-Meteo cache < OM_STALE_MAX_H h (either API; flagged with its age, lowers confidence)
#   4. MET Norway locationforecast 2.0 -- fills every hour still missing after 1-3
# All values land in the om_* columns; om_src / longrange_source say where each hour came from.
OM_VARS = ["temperature_2m", "dew_point_2m", "relative_humidity_2m", "cloud_cover", "cloud_cover_low",
           "cloud_cover_mid", "cloud_cover_high", "visibility", "wind_speed_10m", "wind_gusts_10m",
           "precipitation_probability"]
OM_COL = {"temperature_2m": "om_temp_f", "dew_point_2m": "om_dew_f", "relative_humidity_2m": "om_rh",
          "cloud_cover": "om_cloud", "cloud_cover_low": "om_low", "cloud_cover_mid": "om_mid",
          "cloud_cover_high": "om_high", "visibility": "om_vis_m", "wind_speed_10m": "om_wind_mph",
          "wind_gusts_10m": "om_gust_mph", "precipitation_probability": "om_pop"}
ENS_VARS = ["cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "temperature_2m",
            "dew_point_2m", "relative_humidity_2m", "visibility", "wind_speed_10m", "wind_gusts_10m", "precipitation"]
UNITS = "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=GMT"
# multi-model responses suffix keys with the API's internal model ids (checked 2026-10-04)
ENS_MODEL_ALIAS = {"ecmwf_ifs025_ensemble": "ecmwf_ifs025", "ecmwf_aifs025_ensemble": "ecmwf_aifs025",
                   "ncep_gefs_seamless": "gfs_seamless", "gfs_seamless": "gfs_seamless"}
LEGACY_ENS = ["ecmwf_ifs025", "ecmwf_aifs025", "gfs_seamless"]   # per-model cache files from older versions


def _now():
    return datetime.now(UTC)


def _age_h(iso):
    return (_now() - datetime.fromisoformat(iso)).total_seconds() / 3600


def _read_cache(name):
    f = C.CACHE_DIR / f"{name}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:  # noqa
        return None


def _write_cache(name, blob, raw_dir):
    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (C.CACHE_DIR / f"{name}.json").write_text(json.dumps(blob))
    (raw_dir / f"{name}.json").write_text(json.dumps(blob))


def _parse_om_forecast(d, tag):
    hourly = {}
    hh = d.get("hourly", {})
    for i, ts_ in enumerate(hh.get("time", [])):
        t = datetime.fromisoformat(ts_).replace(tzinfo=UTC)
        hourly[t] = {OM_COL[v]: (hh.get(v) or [None] * (i + 1))[i] for v in OM_VARS}
        hourly[t]["om_src"] = tag
    return hourly


_ENS_KEY = re.compile(r"^(?P<var>[a-z_0-9]+?)(?:_member(?P<mem>\d+))?(?:_(?P<model>(?:ecmwf|gfs|icon|gem|bom|ukmo|meteo|jma|kma|ncep)[a-z_0-9]*))?$")


def _ens_members(hh, default_model):
    """Group an ensemble 'hourly' block into {(model, var): [member arrays]}.
    Handles single-model keys ('cloud_cover', 'cloud_cover_member01') and multi-model keys with a model suffix."""
    out = {}
    for k, arr in hh.items():
        if k == "time" or not isinstance(arr, list):
            continue
        m = _ENS_KEY.match(k)
        if not m:
            continue
        var, model = m.group("var"), m.group("model") or default_model
        if var not in ENS_VARS:
            continue
        model = ENS_MODEL_ALIAS.get(model, model.replace("_ensemble", ""))
        out.setdefault((model, var), []).append(arr)
    return out


def _parse_om_ensemble(blocks, tag):
    """blocks: list of (default_model, data).  Member means; PoP = % members with >= 0.1 mm/h."""
    per = {}
    for default_model, d in blocks:
        hh = d.get("hourly", {})
        times = [datetime.fromisoformat(x).replace(tzinfo=UTC) for x in hh.get("time", [])]
        for (model, var), arrs in _ens_members(hh, default_model).items():
            for i, t in enumerate(times):
                vals = [a[i] for a in arrs if i < len(a) and a[i] is not None]
                if not vals:
                    continue
                rec = per.setdefault(t, {})
                if var == "precipitation":
                    rec[(model, "pop")] = 100.0 * sum(1 for x in vals if x >= 0.1) / len(vals)
                else:
                    rec[(model, var)] = sum(vals) / len(vals)
    hourly = {}
    for t, rec in per.items():
        def first(v, models=("ecmwf_ifs025", "ecmwf_aifs025", "gfs_seamless")):
            for mo in models:
                if rec.get((mo, v)) is not None:
                    return rec[(mo, v)], mo
            return None, None
        out = {"om_src": tag}
        tc = [v for (mo, var), v in rec.items() if var == "cloud_cover" and v is not None]
        out["om_cloud"] = sum(tc) / len(tc) if tc else None
        for v, col in (("cloud_cover_low", "om_low"), ("cloud_cover_mid", "om_mid"), ("cloud_cover_high", "om_high")):
            out[col], mo = first(v)
        for v in ("temperature_2m", "dew_point_2m", "relative_humidity_2m", "visibility", "wind_speed_10m", "wind_gusts_10m"):
            out[OM_COL[v]] = first(v, ("gfs_seamless", "ecmwf_ifs025", "ecmwf_aifs025"))[0]
        out["om_pop"] = first("pop", ("gfs_seamless", "ecmwf_ifs025", "ecmwf_aifs025"))[0]
        if out["om_cloud"] is not None:
            hourly[t] = out
    return hourly


def _covers(hourly, need_end):
    ok = [t for t, v in hourly.items() if v.get("om_cloud") is not None]
    return bool(ok) and max(ok) >= need_end


def _try_api(name, url, raw_dir, parse, label, need_end, attempts):
    """Fresh cache (< OM_FRESH_H, covering need_end) -> reuse; else one live call (+1 retry on 429)."""
    blob = _read_cache(name)
    if blob and blob.get("url") == url:
        age = _age_h(blob["fetched_utc"])
        if age < C.OM_FRESH_H:
            hourly = parse(blob["data"], f"{label} (cache {age:.1f} h)")
            if _covers(hourly, need_end):
                attempts.append({"step": label, "result": f"reused cache {age:.1f} h old (< {C.OM_FRESH_H:g} h, no call made)"})
                (raw_dir / f"{name}.json").write_text(json.dumps(blob))
                return hourly, {"mode": f"cache ({age:.1f} h old, fresh)", "fetched_utc": blob["fetched_utc"], "age_h": age,
                                "stale": False}
    try:
        data = _get_json(url)
        now = _now().isoformat(timespec="seconds")
        _write_cache(name, {"fetched_utc": now, "url": url, "data": data}, raw_dir)
        hourly = parse(data, label)
        if not hourly:
            raise RuntimeError("response had no usable cloud data")
        attempts.append({"step": label, "result": "live"})
        return hourly, {"mode": "live", "fetched_utc": now, "age_h": 0.0, "stale": False}
    except Exception as e:  # noqa
        attempts.append({"step": label, "result": "failed", "error": str(e)})
        return None, None


def _stale_candidates():
    """All Open-Meteo caches on disk as (fetched_utc, kind, parser-ready payload)."""
    out = []
    b = _read_cache("openmeteo_forecast")
    if b:
        out.append((b["fetched_utc"], "om-forecast", lambda tag, b=b: _parse_om_forecast(b["data"], tag)))
    b = _read_cache("openmeteo_ensemble")
    if b:
        out.append((b["fetched_utc"], "om-ensemble",
                    lambda tag, b=b: _parse_om_ensemble([(C.OM_ENSEMBLE_MODELS[0], b["data"])], tag)))
    legacy = [(m, _read_cache(f"openmeteo_ens_{m}")) for m in LEGACY_ENS]
    legacy = [(m, b) for m, b in legacy if b]
    if legacy:
        oldest = min(b["fetched_utc"] for _, b in legacy)
        out.append((oldest, "om-ensemble",
                    lambda tag, L=legacy: _parse_om_ensemble([(m, b["data"]) for m, b in L], tag)))
    return sorted(out, key=lambda x: x[0], reverse=True)


def fetch_longrange(lat, lon, elev_m, raw_dir, need_end_utc, days):
    attempts = []
    status = {"source": None, "attempts": attempts, "stale": False, "met_used": False}
    days = max(3, min(16, days))
    q = f"latitude={lat:.4f}&longitude={lon:.4f}&forecast_days={days}{UNITS}"
    # 1. main forecast API
    hourly, st = _try_api("openmeteo_forecast",
                          f"https://api.open-meteo.com/v1/forecast?{q}&hourly={','.join(OM_VARS)}", raw_dir,
                          _parse_om_forecast, "om-forecast", need_end_utc, attempts)
    if hourly:
        status.update(st, source="Open-Meteo forecast API (best-match deterministic)", kind="om-forecast")
    else:
        # 2. ensemble API: a single request for all models and variables
        url = (f"https://ensemble-api.open-meteo.com/v1/ensemble?{q}&models={','.join(C.OM_ENSEMBLE_MODELS)}"
               f"&hourly={','.join(ENS_VARS)}")
        hourly, st = _try_api("openmeteo_ensemble", url, raw_dir,
                              lambda d, tag: _parse_om_ensemble([(C.OM_ENSEMBLE_MODELS[0], d)], tag),
                              "om-ensemble", need_end_utc, attempts)
        if hourly:
            status.update(st, source="Open-Meteo ensemble API (member means)", kind="om-ensemble")
    if not hourly:
        # 3. newest cached Open-Meteo response under OM_STALE_MAX_H
        for fetched, kind, parse in _stale_candidates():
            age = _age_h(fetched)
            if age >= C.OM_STALE_MAX_H:
                attempts.append({"step": f"{kind} cache", "result": f"too old ({age:.1f} h ≥ {C.OM_STALE_MAX_H:g} h)"})
                continue
            hourly = parse(f"{kind} CACHED {age:.0f} h old")
            if hourly:
                attempts.append({"step": f"{kind} cache", "result": f"used, {age:.1f} h old"})
                status.update(source=f"Open-Meteo {kind.split('-')[1]} — CACHED copy", kind=kind, mode=f"cached ({age:.1f} h old)",
                              fetched_utc=fetched, age_h=age, stale=True)
                break
    hourly = hourly or {}
    # 4. MET Norway for every hour the Open-Meteo chain could not supply (only called if needed)
    if C.MET_NORWAY and (not hourly or not _covers(hourly, need_end_utc)):
        met, mst = fetch_met_norway(lat, lon, elev_m, raw_dir)
        status["met"] = mst
        attempts.append({"step": "met-norway", "result": mst["mode"], "error": mst.get("error")})
        filled = 0
        for t, rec in met.items():
            if hourly.get(t, {}).get("om_cloud") is None:
                hourly[t] = rec
                filled += 1
        status["met_hours"] = filled
        status["met_used"] = filled > 0
        if filled and not status.get("source"):
            status.update(source="MET Norway locationforecast 2.0", kind="met-norway", mode=mst["mode"],
                          fetched_utc=mst.get("fetched_utc"), age_h=mst.get("age_h", 0.0))
    status["ok"] = bool(hourly)
    if not status.get("source"):
        status.update(source="none", kind=None, mode="failed")
    errs = [f"{a['step']}: {a['error']}" for a in attempts if a.get("error")]
    status["error"] = "; ".join(errs) or None
    ok = [t for t, v in hourly.items() if v.get("om_cloud") is not None]
    status["range_utc"] = (min(ok).isoformat(), max(ok).isoformat()) if ok else None
    return hourly, status


# ---- MET Norway locationforecast 2.0 ("complete": cloud layers, dew point, gusts, PoP)
def _met_ua():
    ua = "nightlapse/1.1 (personal non-commercial astrophotography night-lapse planner"
    return ua + (f"; {C.CONTACT})" if C.CONTACT else ")")


def fetch_met_norway(lat, lon, elev_m, raw_dir):
    """Honours MET's terms: identifying User-Agent, 4-decimal coordinates, Expires / If-Modified-Since caching."""
    from email.utils import parsedate_to_datetime
    url = (f"https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={lat:.4f}&lon={lon:.4f}"
           f"&altitude={int(round(elev_m))}")
    name = "metno_locationforecast"
    blob = _read_cache(name)
    if blob and blob.get("url") != url:
        blob = None
    st = {"source": "MET Norway locationforecast 2.0"}
    try:
        if blob and blob.get("expires") and _now() < datetime.fromisoformat(blob["expires"]):
            age = _age_h(blob["fetched_utc"])
            st.update(mode=f"cache ({age:.1f} h old, not yet expired)", fetched_utc=blob["fetched_utc"], age_h=age)
        else:
            hdr = {"User-Agent": _met_ua(), "Accept": "application/json", "Accept-Encoding": "gzip, deflate"}
            if blob and blob.get("last_modified"):
                hdr["If-Modified-Since"] = blob["last_modified"]
            data, r = _get_json(url, hdr, want_response=True)
            exp = r.headers.get("Expires")
            exp_iso = parsedate_to_datetime(exp).astimezone(UTC).isoformat() if exp else None
            if r.status_code == 304 and blob:
                blob.update(expires=exp_iso or blob.get("expires"))
                st.update(mode="live (304 not modified; cached body)", fetched_utc=blob["fetched_utc"], age_h=_age_h(blob["fetched_utc"]))
            else:
                blob = {"fetched_utc": _now().isoformat(timespec="seconds"), "url": url, "data": data,
                        "expires": exp_iso, "last_modified": r.headers.get("Last-Modified"), "status": r.status_code}
                st.update(mode="live" + (" (HTTP 203: product deprecated)" if r.status_code == 203 else ""),
                          fetched_utc=blob["fetched_utc"], age_h=0.0)
            _write_cache(name, blob, raw_dir)
    except Exception as e:  # noqa
        if blob and _age_h(blob["fetched_utc"]) < C.OM_STALE_MAX_H:
            age = _age_h(blob["fetched_utc"])
            st.update(mode=f"cached ({age:.1f} h old)", fetched_utc=blob["fetched_utc"], age_h=age, error=str(e))
        else:
            st.update(mode="failed", error=str(e))
            return {}, st
    hourly = _parse_met(blob["data"], st)
    ok = sorted(hourly)
    st["range_utc"] = (ok[0].isoformat(), ok[-1].isoformat()) if ok else None
    st["updated_at"] = blob["data"].get("properties", {}).get("meta", {}).get("updated_at")
    return hourly, st


def _parse_met(d, st):
    pts = []
    for ts in d.get("properties", {}).get("timeseries", []):
        t = datetime.fromisoformat(ts["time"].replace("Z", "+00:00"))
        det = ts.get("data", {}).get("instant", {}).get("details", {})
        n1 = ts.get("data", {}).get("next_1_hours", {}).get("details", {})
        n6 = ts.get("data", {}).get("next_6_hours", {}).get("details", {})
        c = lambda v: None if v is None else v * 9 / 5 + 32
        ms = lambda v: None if v is None else v * 2.236936
        rec = {"om_cloud": det.get("cloud_area_fraction"), "om_low": det.get("cloud_area_fraction_low"),
               "om_mid": det.get("cloud_area_fraction_medium"), "om_high": det.get("cloud_area_fraction_high"),
               "om_temp_f": c(det.get("air_temperature")), "om_dew_f": c(det.get("dew_point_temperature")),
               "om_rh": det.get("relative_humidity"), "om_wind_mph": ms(det.get("wind_speed")),
               "om_gust_mph": ms(det.get("wind_speed_of_gust")), "om_vis_m": None}
        pts.append((t, rec, n1.get("probability_of_precipitation"), n6.get("probability_of_precipitation"),
                    "next_1_hours" in ts.get("data", {})))
    hourly = {}
    tag = f"met-norway ({st['mode'].split(' (')[0]})"
    for k, (t, rec, p1, p6, has1) in enumerate(pts):
        nxt = pts[k + 1] if k + 1 < len(pts) else None
        step = int((nxt[0] - t).total_seconds() // 3600) if nxt else 1
        for j in range(max(step, 1)):
            h = t + timedelta(hours=j)
            if j == 0:
                r = dict(rec)
            else:   # linear interpolation inside a 6-hourly step
                w = j / step
                r = {kk: (None if rec[kk] is None or nxt[1][kk] is None else rec[kk] + (nxt[1][kk] - rec[kk]) * w)
                     for kk in rec}
            r["om_pop"] = p1 if (j == 0 and has1 and p1 is not None) else p6
            r["om_src"] = tag + (" 6-h interp" if step > 1 and j > 0 else " 6-hourly" if step > 1 else "")
            if r["om_cloud"] is not None:
                hourly[h] = r
    return hourly
