"""FastAPI application: target registry, job control, live log streaming."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from sqlalchemy import select

from . import rman, runner
from .auth import require_token
from .config import API_TOKEN, BASE_DIR
from .database import SessionLocal, init_db
from .models import Job, Target
from .schemas import (
    BACKUP_TYPES,
    JobDetail,
    JobIn,
    JobOut,
    TargetIn,
    TargetOut,
    TestResult,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rman_console")

FRONTEND = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if not API_TOKEN:
        log.warning("RMAN_CONSOLE_TOKEN is not set. The API will refuse requests.")
    yield


app = FastAPI(title="RMAN Console", version="0.1.0", lifespan=lifespan)


def _target_out(t: Target) -> TargetOut:
    data = TargetOut.model_validate(t)
    data.has_catalog = bool(t.catalog_connect)
    return data


# ---------------------------------------------------------------- targets


@app.get("/api/targets", response_model=list[TargetOut],
         dependencies=[Depends(require_token)])
def list_targets() -> list[TargetOut]:
    with SessionLocal() as db:
        rows = db.scalars(select(Target).order_by(Target.name)).all()
        return [_target_out(t) for t in rows]


@app.post("/api/targets", response_model=TargetOut, status_code=201,
          dependencies=[Depends(require_token)])
def create_target(payload: TargetIn) -> TargetOut:
    with SessionLocal() as db:
        if db.scalar(select(Target).where(Target.name == payload.name)):
            raise HTTPException(409, f"a target named {payload.name} already exists")
        target = Target(**payload.model_dump())
        db.add(target)
        db.commit()
        db.refresh(target)
        return _target_out(target)


@app.delete("/api/targets/{target_id}", status_code=204,
            response_class=Response, dependencies=[Depends(require_token)])
def delete_target(target_id: int) -> Response:
    with SessionLocal() as db:
        target = db.get(Target, target_id)
        if target is None:
            raise HTTPException(404, "no such target")
        running = db.scalar(
            select(Job).where(Job.target_id == target_id,
                              Job.status.in_(("QUEUED", "RUNNING")))
        )
        if running:
            raise HTTPException(409, "a job is still running against this target")
        db.delete(target)
        db.commit()
    return Response(status_code=204)


@app.post("/api/targets/{target_id}/test", response_model=TestResult,
          dependencies=[Depends(require_token)])
async def test_target(target_id: int) -> TestResult:
    with SessionLocal() as db:
        target = db.get(Target, target_id)
        if target is None:
            raise HTTPException(404, "no such target")
    ok, detail = await runner.test_target(target)
    return TestResult(ok=ok, detail=detail)


# ------------------------------------------------------------------- jobs


@app.get("/api/backup-types", dependencies=[Depends(require_token)])
def backup_types() -> list[dict]:
    return [{"value": v, "label": rman.HUMAN_NAMES[v]} for v in BACKUP_TYPES]


@app.get("/api/jobs", response_model=list[JobOut],
         dependencies=[Depends(require_token)])
def list_jobs(limit: int = 50) -> list[JobOut]:
    limit = max(1, min(limit, 500))
    with SessionLocal() as db:
        rows = db.scalars(
            select(Job).order_by(Job.id.desc()).limit(limit)
        ).all()
        return [JobOut.model_validate(j) for j in rows]


@app.get("/api/jobs/{job_id}", response_model=JobDetail,
         dependencies=[Depends(require_token)])
def get_job(job_id: int) -> JobDetail:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return JobDetail.model_validate(job)


@app.post("/api/jobs", response_model=JobOut, status_code=202,
          dependencies=[Depends(require_token)])
async def create_job(payload: JobIn) -> JobOut:
    # Async so runner.start() has a running event loop to schedule onto. The
    # SQLite work below is brief enough not to matter on the loop thread.
    with SessionLocal() as db:
        target = db.get(Target, payload.target_id)
        if target is None:
            raise HTTPException(404, "no such target")
        busy = db.scalar(
            select(Job).where(Job.target_id == target.id,
                              Job.status.in_(("QUEUED", "RUNNING")))
        )
        if busy:
            raise HTTPException(
                409,
                f"job {busy.id} is already running against {target.name}. "
                "Concurrent RMAN runs on one database are rarely what you want.",
            )
        job = Job(
            target_id=target.id,
            backup_type=payload.backup_type,
            tag=rman.make_tag(target, payload.backup_type),
            include_archivelog=payload.include_archivelog,
            delete_archivelog=payload.delete_archivelog,
            backup_controlfile=payload.backup_controlfile,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
        out = JobOut.model_validate(job)

    runner.start(job_id)
    return out


@app.post("/api/jobs/{job_id}/cancel", response_model=TestResult,
          dependencies=[Depends(require_token)])
async def cancel_job(job_id: int) -> TestResult:
    ok, detail = await runner.cancel(job_id)
    return TestResult(ok=ok, detail=detail)


@app.get("/api/jobs/{job_id}/script", dependencies=[Depends(require_token)])
def job_script(job_id: int) -> dict:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return {"script": job.rman_script or ""}


# -------------------------------------------------------------- streaming


@app.websocket("/ws/jobs/{job_id}")
async def job_stream(websocket: WebSocket, job_id: int) -> None:
    """Live log for one job.

    The token travels as a query parameter because browsers cannot set
    headers on a WebSocket handshake.
    """
    token = websocket.query_params.get("token", "")
    if not API_TOKEN or token != API_TOKEN:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            await websocket.close(code=4404)
            return
        # Replay whatever is already stored so a late subscriber sees context.
        backlog = job.log_text or ""
        status = job.status

    if backlog:
        await websocket.send_json({"type": "log", "data": backlog})
    await websocket.send_json({"type": "status", "status": status})

    queue = runner.subscribe(job_id)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(msg)
            if msg.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        runner.unsubscribe(job_id, queue)


# --------------------------------------------------------------- frontend


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
