import logging

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from data.storage import Storage

router = APIRouter()
logger = logging.getLogger(__name__)
db = Storage()


@router.get("/prices")
async def get_prices(_user=Depends(get_current_user)):
    return db.get_price_snapshots()
