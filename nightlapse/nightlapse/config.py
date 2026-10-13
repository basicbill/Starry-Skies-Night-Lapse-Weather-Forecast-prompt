"""All tunable settings live here.  Edit this file to change behaviour."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = ROOT / "output"
LOCATION_FILE = DATA_DIR / "location.json"

TZ = "America/Phoenix"           # MST, UTC-7, no DST
TZ_LABEL = "MST"
ADDRESS = "1315 S Desert Meadows Cir, Green Valley, AZ 85614"
SITE_NAME = "Green Valley, AZ"
SITE_KEY, SITE_SLUG = "greenvalley", ""   # set by sites.apply(); non-default sites write output/<date>-<slug>/
CUSTOM_SITE = None

# NWS asks for an identifying User-Agent.
USER_AGENT = "nightlapse-greenvalley/1.0 (personal astrophotography night-lapse planner)"
HTTP_TIMEOUT = 40
HTTP_RETRIES = 2
# If a live fetch fails, a cached copy of that source no older than this is used (and flagged).
MAX_CACHE_AGE_H = 18
# On HTTP 429 (rate limit) retry once after this many seconds (or the server's Retry-After, capped at 30 s).
RETRY_429_BACKOFF_S = 8

# ---- long-range model chain (Open-Meteo forecast -> Open-Meteo ensemble -> cached Open-Meteo -> MET Norway)
OM_FRESH_H = 3.0        # an Open-Meteo cache younger than this is reused instead of calling the API
OM_STALE_MAX_H = 36.0   # if both Open-Meteo APIs fail, the newest Open-Meteo cache younger than this is used (flagged)
OM_ENSEMBLE_MODELS = ["ecmwf_ifs025", "gfs_seamless"]   # one request; IFS ens = cloud layers, GEFS = surface vars
MET_NORWAY = True       # last resort: MET Norway locationforecast 2.0 (free, keyless; needs identifying User-Agent)
# MET Norway's terms ask for contact info (email or website) in the User-Agent. Put one here if you can.
CONTACT = ""

DEFAULT_DAYS = 4                 # dark window = new-moon night +/- this many nights
# Which night is "the new-moon night":
#   "nearest"  -> the night whose local midnight is closest to the new-moon instant
#   "calendar" -> the evening of the local calendar date of the new moon
NEW_MOON_NIGHT_RULE = "nearest"

# Hours (local slot start) shown in the heatmap / CSV, evening -> morning.
NIGHT_HOURS = [18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6]
HEATMAP_HOURS = [19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6]

# Moon counts as "up" above this geometric altitude (matches rise/set convention).
MOON_HORIZON_DEG = -0.8333
# A slot (H:00-H+1:00) is "astronomically dark" if the sun is below -18 deg at the slot midpoint.
DARK_SUN_ALT = -18.0

# Confidence by lead time (days from tonight to the night's evening date)
CONF_HIGH_MAX_DAYS = 3
CONF_MEDIUM_MAX_DAYS = 7

# ============================================================================
#                        SCORING WEIGHTS  (tweak here)
# Score = clamp(10 - sum(penalties), 0, 10), computed per dark hour.
# ============================================================================
WEIGHTS = {
    # --- clouds (heaviest) ---
    # blended total cloud = weighted mean of available sources (renormalised)
    "cloud_blend": {"nws": 0.5, "openmeteo": 0.3, "7timer": 0.2},
    "cloud": 6.0,            # penalty at 100 % blended total cloud
    "high_cloud": 1.5,       # EXTRA penalty at 100 % Open-Meteo high cloud (thin cirrus)
    # --- moon ---
    "moon": 5.5,             # penalty = moon * illum_fraction * sin(altitude), 0 when moon down
    # --- transparency / seeing ---
    "transparency": 1.5,     # at worst 7Timer transparency (8); scaled (t-1)/7
    "seeing": 0.75,          # at worst 7Timer seeing (8); scaled (s-1)/7 (GoPro is wide-field)
    "neutral_fraction": 0.5, # fraction of the above used when no data/proxy (flagged "neutral")
    # transparency proxy when 7Timer is out of range: RH and visibility
    "proxy_rh_lo": 40.0, "proxy_rh_hi": 90.0,      # RH ramp (0 -> 1)
    "proxy_vis_good_m": 24000.0, "proxy_vis_bad_m": 4000.0,
    "proxy_rh_share": 0.6,   # proxy = 0.6*RH-term + 0.4*visibility-term
    # --- dew ---
    "dew_spread_warn_f": 4.0, "dew_warn": 0.75,     # spread < 4 F
    "dew_spread_severe_f": 2.0, "dew_severe": 1.5,  # spread < 2 F
    # --- wind (GoPro shake) ---
    "wind_sustained_mph": 12.0, "wind_base": 0.5, "wind_per_mph": 0.05, "wind_sustained_max": 1.0,
    "gust_mph": 20.0, "gust": 0.5,
    "wind_cap": 1.5,
    # --- precipitation probability ---
    "precip": 2.0,           # penalty at 100 % PoP
}

# Night verdicts
GOOD_SCORE = 7.0          # "good hour" threshold for best block
GO_MIN_BLOCK_H = 2        # Go  = a contiguous >=7 block of at least this many hours
MARGINAL_MIN_2H = 5.0     # Marginal = any >=7 hour, or best 2-h block mean >= this

# ============================================================================
#                    BELOW-THRESHOLD HOUR EXPLANATIONS
# ============================================================================
LOW_SCORE_THRESHOLD = 6.0     # window-night dark hours scoring below this are explained in summary.md
LOW_MIN_PENALTY = 0.2         # penalties smaller than this are not listed
LOW_MAX_PENALTIES = 3         # list at most this many penalties per hour
# An hour is "cloud-driven" when clouds (total + extra high-cloud penalty) are its largest penalty.

# ============================================================================
#                         METEOR RADIANT SKY MAPS
# ============================================================================
SKYMAPS = True
SKYMAP_PX = 1000                                   # sky disc size in pixels
SKYMAP_BROWSERS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
