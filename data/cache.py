"""
Daily snapshot cache — 7 rolling JSON files in cache/.
Used for week-on-week delta and API fallback.
"""
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache")
RETENTION = 7  # days


def save_snapshot(data: dict, run_date: date = None):
    CACHE_DIR.mkdir(exist_ok=True)
    d    = run_date or date.today()
    path = CACHE_DIR / f"portfolio_{d.isoformat()}.json"
    # Atomic write: serialise to a .tmp file then rename so a mid-write crash
    # never leaves a partially-written (corrupt) JSON file.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    tmp.replace(path)   # os.rename — atomic on the same filesystem
    _prune()
    logger.info(f"Snapshot saved: {path}")


def load_snapshot(run_date: date = None) -> Optional[dict]:
    d    = run_date or date.today()
    path = CACHE_DIR / f"portfolio_{d.isoformat()}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Corrupt snapshot {path.name}: {exc} — ignoring")
    return None


def load_latest_snapshot(max_age_days: int = RETENTION) -> Optional[tuple[dict, date]]:
    """Return (data, snapshot_date) of the most recent cached file, or None."""
    for i in range(max_age_days):
        d    = date.today() - timedelta(days=i)
        path = CACHE_DIR / f"portfolio_{d.isoformat()}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Corrupt snapshot {path.name}: {exc} — skipping")
                continue
            if i > 2:
                logger.warning(
                    f"Live holdings unavailable — falling back to {i}-day-old "
                    f"snapshot ({d}).  Risk metrics may not reflect current positions."
                )
            return data, d
    return None


def load_week_ago_snapshot() -> Optional[dict]:
    for i in range(7, 10):
        d    = date.today() - timedelta(days=i)
        path = CACHE_DIR / f"portfolio_{d.isoformat()}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _prune():
    cutoff = date.today() - timedelta(days=RETENTION)
    for f in CACHE_DIR.glob("portfolio_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("portfolio_", ""))
            if d < cutoff:
                f.unlink()
        except ValueError:
            pass
