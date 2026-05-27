from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import PlainTextResponse

from api.auth import get_current_user

router = APIRouter()

_ALLOWED = {"settings", "thresholds", "volatility"}
_CONFIG_DIR = Path("config")


@router.get("/config/{name}")
async def get_config(name: str, _user=Depends(get_current_user)):
    if name not in _ALLOWED:
        raise HTTPException(400, f"Unknown config: {name}")
    path = _CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, f"{name}.yaml not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@router.put("/config/{name}")
async def update_config(name: str, body: str = Body(..., media_type="text/plain"), _user=Depends(get_current_user)):
    if name not in _ALLOWED:
        raise HTTPException(400, f"Unknown config: {name}")
    try:
        yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise HTTPException(422, f"Invalid YAML: {e}")
    path = _CONFIG_DIR / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return {"saved": name}
