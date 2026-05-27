from fastapi import APIRouter, Depends
from api.auth import get_current_user

router = APIRouter()


@router.get("/search")
async def search_stocks(q: str = "", _user=Depends(get_current_user)):
    if len(q) < 1:
        return []
    from data.spy_universe import search_sp500
    return search_sp500(q, limit=20)
