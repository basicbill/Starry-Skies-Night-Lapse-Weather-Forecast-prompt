# nightlapse — Green Valley, AZ night-lapse / astrophotography scorer

Scores each whole hour of astronomical darkness from 0 to 10, from tonight through the end of the
dark window around the next new moon. Site: 1315 S Desert Meadows Cir, Green Valley, AZ 85614.
All times are **MST (America/Phoenix, UTC-7, no DST)**. Everything stays local: nothing is written to
any external service.

## Run

```bash
python3 /workspace/nightlapse/run.py                 # auto-detect next new moon, ±4 nights
python3 /workspace/nightlapse/run.py --days 3        # ±3 nights
python3 /workspace/nightlapse/run.py --date 2026-11-09   # plan around the new moon nearest this date
python3 /workspace/nightlapse/run.py --regeocode     # refresh lat/lon (Census) + elevation (USGS)
python3 /workspace/nightlapse/run.py --outdir /tmp/x # write somewhere else
python3 /workspace/nightlapse/run.py --low-threshold 5  # explain window-night hours below 5 (default 6)
python3 /workspace/nightlapse/run.py --no-skymaps    # skip the radiant sky maps
```
"Tonight" means the night that is still astronomically dark if you run before dawn; otherwise it's this evening.

The command runs without prompts, so it works from cron. Example (not installed):
`15 15 * * * python3 /workspace/nightlapse/run.py >> /workspace/nightlapse/output/cron.log 2>&1`

`run.py` re-runs itself inside `/workspace/nightlapse/.venv` if the system Python is missing the deps.

### Dependencies
Python 3.10+ with `skyfield`, `numpy`, `matplotlib`, `requests` (see `requirements.txt`), installed in a venv:
```bash
python3 -m venv /workspace/nightlapse/.venv
/workspace/nightlapse/.venv/bin/pip install -r /workspace/nightlapse/requirements.txt
```
The JPL DE421 ephemeris (`data/de421.bsp`, ~17 MB) is downloaded once on the first run and cached.

## Output: `output/<run date MST>/`
- `summary.md`: nightly table (verdict, mean score, best block, sun/twilight/moon times, illumination,
  true-dark hours, min dew spread + lens-heater flag, active meteor showers, confidence), notes, a meteor-shower
  table, and data-source caveats.
- `heatmap.png`: nights as rows, 7 PM–6 AM slots as columns. Hatched cells aren't astronomically dark,
  `n/a` cells have no forecast, the new-moon night is outlined, and dashed lines mark the dark window.
- `hourly.csv`: every hourly input from every source, the merged inputs with their sources, each penalty, and the score.
- `radiant_<night>.png`: an all-sky meteor-radiant map for each dark-window night where an active shower's
  radiant is above the horizon at mid-darkness (see below). The HTML pages behind them are in `skymap_html/`.
- `raw/`: the raw API responses used for the run.

## How it works
### Location
`data/location.json` holds the cached location: US Census geocoder (Public_AR_Current) gives
31.851330, −110.988891, and USGS EPQS (3DEP 1 m) gives 872.9 m. `--regeocode` refreshes it.
### Astronomy (Skyfield + DE421, computed locally)
- The new moon is found with `almanac.moon_phases`. The **new-moon night** is the night whose local midnight is
  nearest the new-moon instant (`NEW_MOON_NIGHT_RULE="nearest"`). Set it to `"calendar"` to use the evening of the
  new moon's calendar date instead. Window = new-moon night ± `--days`.
- Auto-detect picks the first new moon whose window hasn't ended yet, so daily runs before and during the window both work.
- For each night (noon→noon) it computes sunset, the start of astronomical darkness (sun < −18°), the end of
  astronomical darkness (morning astronomical twilight begins), sunrise, moonrise/moonset, and illumination at local midnight.
- Moon altitude is taken at each hour-slot midpoint. A slot counts as "dark" if the sun is below −18° at its midpoint.
- **True darkness** = astronomically dark AND moon below the horizon (geometric altitude < −0.833°), sampled every 2 min.
### Weather (hourly, keyed to UTC hours, displayed in MST)
| Source | Fields | Range |
|---|---|---|
| NWS api.weather.gov `/points` → `forecastGridData` | skyCover, temperature, dewpoint, relativeHumidity, windSpeed, windGust, probabilityOfPrecipitation (intervals expanded to hours) | ~7 days |
| 7Timer ASTRO (`tzshift=0`) | cloudcover, seeing, transparency, lifted_index, rh2m, wind10m (3-hourly, linearly interpolated, held ±1 h at the ends) | ~72 h |
| Open-Meteo forecast API | cloud_cover (+low/mid/high), temperature, dew point, RH, visibility, wind, gusts, precip probability | 16 days |
| **Fallback:** Open-Meteo *ensemble* API | member means. Cloud layers from ECMWF IFS 0.25° ens (AIFS ens after that); GEFS for dew/temp/RH/vis/wind; PoP = % of GEFS members with ≥0.1 mm/h | ~15 days |

Each fetch is retried. If a fetch fails, the tool uses that source's cached copy (`data/cache/`, ≤18 h old, flagged).
If there's no usable cache, the run continues without that source and `summary.md` says what's missing.
Nights before the window that have no data are omitted. Window nights with no data show as **No forecast yet**.

Merged inputs: temp/dew/RH/wind/gust/PoP come from NWS when available, otherwise Open-Meteo (RH and wind can also
come from 7Timer). `hourly.csv` records each value's source in the `*_src` columns.

**Confidence**: high = 3 days lead or less; medium = 4–7 days; low = more than 7 days, or any night with no NWS data
(Open-Meteo only). "NWS partial" means NWS covers only some of the dark hours.

## Score formula (weights in `nightlapse/config.py` → `WEIGHTS`)
```
score = clamp(10 − Σ penalties, 0, 10)          (dark hours only)

cloud_blend  = weighted mean of available total-cloud % : NWS 0.5, Open-Meteo 0.3, 7Timer 0.2 (renormalised)
cloud        = 6.0 × cloud_blend/100
high_cloud   = 1.5 × OpenMeteo_high_cloud/100           (extra weight for thin cirrus)
moon         = 5.5 × illum_fraction × sin(moon_alt)     (0 when the moon is below the horizon)
transparency = 1.5 × (T−1)/7   with 7Timer transparency T (1–8)
               else proxy: 1.5 × [0.6·ramp(RH, 40→90 %) + 0.4·ramp(vis, 24 km→4 km)]   (flagged "proxy")
               else 1.5 × 0.5  (flagged "neutral")
seeing       = 0.75 × (S−1)/7  with 7Timer seeing S (1–8), else 0.75 × 0.5 (flagged "neutral")
dew          = 1.5 if (T − Td) < 2 °F, 0.75 if < 4 °F, else 0
wind         = [0.5 + 0.05/mph above 12 mph, max 1.0 if sustained > 12 mph] + [0.5 if gusts > 20 mph], cap 1.5
precip       = 2.0 × PoP/100
```
Per night:
- **Best block** = the longest contiguous run of dark hours scoring ≥ 7 (ties go to the higher mean). If no hour reaches 7, it's the best 2-hour block.
- **Mean** = mean score over the scored dark hours.
- **Verdict**: **Go** = a ≥7 block of at least 2 h. **Marginal** = a single ≥7 hour, or a best 2-h mean ≥ 5. **No-go** = anything else.
- **Lens heater advised** when the night's minimum temperature–dewpoint spread (sunset→sunrise) is under 4 °F.

## Below-threshold hours
For dark-window nights only, every dark hour scoring below `LOW_SCORE_THRESHOLD` (config, default 6) is listed with its
score and its biggest penalties in plain words, e.g. "moon 20% lit at 26° altitude (−0.5)" or "gusts 21 mph (−0.5)".
Up to `LOW_MAX_PENALTIES` penalties of at least `LOW_MIN_PENALTY` are shown. Clouds count as one cause: total cloud
plus the extra high-cirrus penalty. An hour is **cloud-driven** when clouds are its largest penalty. Placeholder
penalties for missing data ("seeing unknown (neutral)") are listed but are never picked as the main cause.
Consecutive hours with the same main cause are grouped into one range. If no hour is below the threshold,
the section says so.

## Meteor radiant sky maps
- **Which nights:** dark-window nights where at least one active shower's radiant is above the horizon at the middle
  of astronomical darkness, rounded to the nearest hour.
- **Rendering:** [VirtualSky](https://github.com/slowe/VirtualSky) is vendored at `vendor/VirtualSky` (commit in
  `vendor/VIRTUALSKY_VERSION.txt`). The tool writes a local HTML page, serves it on a random 127.0.0.1 port, and
  screenshots it with headless Chrome/Chromium.
- **Projection:** VirtualSky's `polar` projection, which is zenith-centred and azimuthal-equidistant. North is at the
  top and east on the left, as seen lying on your back.
- **What's drawn:** stars to magnitude 4.8 with bright-star names, constellation lines and names, the Milky Way
  outline, the alt-az grid, the horizon, and N/E/S/W.
- **Radiants and moon:** each radiant is a VirtualSky square inside a coloured ring placed at the Skyfield-computed
  alt/az. Because the square lands inside the ring, every map checks itself. The moon is ringed and labelled with
  its illumination when it's up.
- **Fallback:** if Chrome or VirtualSky is missing, a plain matplotlib polar plot with no star field is written instead
  and flagged.

## Meteor showers
`nightlapse/showers.py` is a built-in table from the IMO 2026 Meteor Shower Calendar: SLY, OCT, DRA, STA, ORI,
EGE, LMI, NTA, with active dates, ZHR, and radiant positions with Table-6 drift. Peak instants use the IMO-listed
UT time when given (OCT, DRA). Otherwise they're computed locally from the listed solar longitude. For each shower
the tool computes, on its peak night, the radiant altitude, the hours above 20° in darkness, and how many of those
hours have the moon up, which gives the moon-interference label. **Peak dates are year-specific: update the table for 2027+.**

## Files
`run.py` (entry) · `nightlapse/config.py` (all settings/weights) · `astro.py` · `weather.py` · `scoring.py` ·
`showers.py` · `explain.py` (below-threshold hours) · `skymap.py` (radiant maps) · `report.py` · `geo.py` · `data/` (ephemeris, location, API cache) · `vendor/VirtualSky` · `output/`.
Chrome/Chromium is needed only for the sky maps; the binaries tried are listed in `SKYMAP_BROWSERS`.

## Other locations (one-off runs)

The default (no flags) is still Green Valley, AZ (America/Phoenix), output in `output/<date>/`.

- `python3 run.py --site stcharles`: St. Charles, IL (708 S 12th Ave, America/Chicago). Output goes to `output/<date>-stcharles/` and the cache to `data/cache/stcharles/`.
- `python3 run.py --lat 41.91 --lon -88.30 --tz America/Chicago --name "Somewhere" [--elev 226]`: any custom site (elevation comes from USGS EPQS if omitted).
- `--skymap-nights 2026-10-08[,…]`: draw radiant maps only for these evening dates.
- Every run also writes `nights.json`, a per-night summary.
- `python3 compare.py RUN_DIR_A RUN_DIR_B --out DIR`: writes a side-by-side `comparison.md` and `comparison.png`, adds NASA POWER October climatology, and reads light-pollution estimates from `data/light_pollution.json`.

## Long-range model chain (Open-Meteo rate limits)

The `om_*` columns come from the first of these that works:
1. Open-Meteo forecast API (one request; a cache under 3 h old is reused instead of calling).
2. Open-Meteo ensemble API (one request for ECMWF IFS + GEFS, all variables; same 3 h rule).
3. The newest cached Open-Meteo response under 36 h old. It is flagged with its age, and its nights drop one confidence level (↓ in the heatmap).
4. MET Norway locationforecast 2.0 (api.met.no, keyless). Used only for hours still missing; honours `Expires` and `If-Modified-Since`. Set `CONTACT` in config.py to add an email or website to its User-Agent, as MET's terms request.

HTTP 429 is retried once after `RETRY_429_BACKOFF_S` seconds. Settings are in config.py (`OM_FRESH_H`, `OM_STALE_MAX_H`, `OM_ENSEMBLE_MODELS`, `MET_NORWAY`). Each hour's source is listed in hourly.csv (`longrange_source`) and in the summary.md notes.
