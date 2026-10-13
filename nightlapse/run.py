#!/usr/bin/env python3
"""Entry point:  python3 /workspace/nightlapse/run.py [--days N] [--date YYYY-MM-DD]

Re-executes itself inside /workspace/nightlapse/.venv if the system Python lacks the deps.
"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"

try:
    import skyfield, matplotlib, requests, numpy  # noqa: F401
except ImportError:
    if VENV_PY.exists() and not os.environ.get("NIGHTLAPSE_REEXEC"):
        os.environ["NIGHTLAPSE_REEXEC"] = "1"
        os.execv(str(VENV_PY), [str(VENV_PY), __file__, *sys.argv[1:]])
    sys.exit("Missing dependencies. Run: python3 -m venv /workspace/nightlapse/.venv && "
             "/workspace/nightlapse/.venv/bin/pip install -r /workspace/nightlapse/requirements.txt")

sys.path.insert(0, str(ROOT))
from nightlapse import sites  # noqa: E402
sites.apply(sys.argv[1:])     # must happen before the other modules read config.TZ
from nightlapse.main import run  # noqa: E402

if __name__ == "__main__":
    run()
