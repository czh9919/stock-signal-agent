import asyncio
import logging
from contextlib import asynccontextmanager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware

from api.auth import get_current_user, oauth2_scheme
from api.ws import manager
from api.scheduler import create_scheduler
from api.massive_ws import run_massive_ws_task
from api.routes import auth, runs, prices, watchlist, config, search

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.set_loop(asyncio.get_event_loop())

    sched = create_scheduler()
    sched.start()
    logger.info("Background scheduler started")

    stop_event = asyncio.Event()
    ws_task = asyncio.create_task(run_massive_ws_task(stop_event))

    yield

    stop_event.set()
    ws_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(ws_task), timeout=5)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    sched.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(title="Stock AI Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(runs.router, prefix="/api", tags=["runs"])
app.include_router(prices.router, prefix="/api", tags=["prices"])
app.include_router(watchlist.router, prefix="/api", tags=["watchlist"])
app.include_router(config.router, prefix="/api", tags=["config"])
app.include_router(search.router, prefix="/api", tags=["search"])


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Verify JWT from query param ?token=...
    from jose import JWTError, jwt
    from api.auth import SECRET_KEY, ALGORITHM
    token = ws.query_params.get("token", "")
    try:
        jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        await ws.close(code=4001)
        return

    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
