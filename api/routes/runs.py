import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from api.auth import get_current_user
from api.ws import manager
from data.storage import Storage

router = APIRouter()
logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
_running = False

db = Storage()


def _run_pipeline(run_id: int, run_mode: str, config: dict):
    global _running
    try:
        from main import run_portfolio_pipeline
        result = run_portfolio_pipeline(
            run_mode=run_mode,
            config=config,
            on_log=manager.send_log,
        )
        metrics = result.get("metrics", {}) if result else {}
        html_en = result.get("html_en", "") if result else ""
        html_zh = result.get("html_zh", "") if result else ""
        db.finish_run(run_id, metrics, html_en, html_zh)
        manager.send_run_status(run_id, "success",
                                nav_eur=metrics.get("nav_eur"),
                                rag=metrics.get("overall_rag"))
    except Exception as exc:
        logger.exception(f"Pipeline run {run_id} failed")
        db.fail_run(run_id, str(exc))
        manager.send_run_status(run_id, "failed")
    finally:
        _running = False


@router.post("/run/{mode}")
async def trigger_run(mode: str, _user=Depends(get_current_user)):
    global _running
    if mode not in ("full", "portfolio", "alert_check"):
        raise HTTPException(400, f"Unknown run mode: {mode}")
    if _running:
        raise HTTPException(409, "A pipeline run is already in progress")

    _running = True
    run_id = db.create_run(mode)

    from main import load_config
    config = load_config()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_pipeline, run_id, mode, config)

    manager.send_run_status(run_id, "running")
    return {"run_id": run_id, "status": "running"}


@router.get("/runs")
async def list_runs(_user=Depends(get_current_user)):
    return db.get_runs(limit=50)


@router.get("/runs/latest")
async def latest_run(_user=Depends(get_current_user)):
    run = db.get_latest_run()
    if not run:
        raise HTTPException(404, "No completed runs yet")
    return run


@router.get("/runs/{run_id}")
async def get_run(run_id: int, _user=Depends(get_current_user)):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}/report")
async def get_report(run_id: int, lang: str = "en", _user=Depends(get_current_user)):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    html = run.get("html_en") if lang == "en" else run.get("html_zh")
    if not html:
        raise HTTPException(404, "Report not available")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.get("/history")
async def metric_history(_user=Depends(get_current_user)):
    return db.get_metric_history(limit=90)


@router.get("/status")
async def run_status(_user=Depends(get_current_user)):
    return {"running": _running}
