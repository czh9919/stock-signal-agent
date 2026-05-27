import csv
import io
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user

router = APIRouter()
WATCHLIST_PATH = Path("config/watchlist.csv")


class WatchlistEntry(BaseModel):
    ticker: str
    weight: float = 1.0
    notes: str = ""
    asset_class: str = "equity"
    currency: str = "USD"


@router.get("/watchlist")
async def get_watchlist(_user=Depends(get_current_user)):
    if not WATCHLIST_PATH.exists():
        raise HTTPException(404, "watchlist.csv not found")
    with open(WATCHLIST_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@router.put("/watchlist")
async def update_watchlist(entries: List[WatchlistEntry], _user=Depends(get_current_user)):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["ticker", "weight", "notes", "asset_class", "currency"])
    writer.writeheader()
    for e in entries:
        writer.writerow(e.model_dump())
    WATCHLIST_PATH.write_text(buf.getvalue(), encoding="utf-8")

    from api.massive_ws import signal_reload
    signal_reload()

    return {"saved": len(entries)}
