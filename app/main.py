"""
FastAPI application entry point and REST API routes.

Serves two roles:
  1. JSON API at /api/* for schedule upload, queries, and predictions
  2. Static web dashboard at / for the browser-based UI

Upload flow: receive XML → parse & persist → rebuild captain patterns → return summary
"""
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.xml_parser import import_schedules
from app.database import get_db, init_db
from app.models import ScheduleEntry, ShipCapacity, UploadLog
from app.predictor import (
    get_busy_calendar,
    get_captain_summaries,
    predict_captain_schedule,
    rebuild_patterns,
)
from app.schemas import (
    CaptainPrediction,
    CaptainSummary,
    ScheduleEntryOut,
    ShipCapacityOut,
    StatsOut,
    UploadResult,
)
from app.ship_data import lookup_ship_online, seed_ship_capacities


def _resolve_upload_filename(filename: str | None, content_type: str | None) -> str:
    """Use the uploaded filename, or fall back when browsers omit it on drag-and-drop."""
    if filename and filename.strip():
        return filename.strip()
    if content_type and "xml" in content_type.lower():
        return "upload.xml"
    return "upload.xml"


def _looks_like_xml(filename: str | None, content_type: str | None, content: bytes) -> bool:
    """
    Determine whether uploaded bytes are likely a dispatch XML file.

    Checks file extension, MIME type, and a content sniff for XML declaration
    or schedule element tags. This avoids rejecting valid XML when the browser
    sends no filename.
    """
    if filename and filename.strip():
        lower = filename.strip().lower()
        if lower.endswith(".xml"):
            return True

    if content_type:
        ct = content_type.lower().split(";")[0].strip()
        if ct in (
            "text/xml",
            "application/xml",
            "application/xhtml+xml",
            "application/octet-stream",
        ):
            return True

    if not content:
        return False

    try:
        sample = content[:4096].decode("utf-8-sig", errors="ignore").strip()
    except Exception:
        return False

    if not sample:
        return False

    lower = sample.lower()
    if lower.startswith("<?xml") or lower.startswith("<"):
        return any(tag in lower for tag in ("<schedules", "<schedule", "<entry", "<dispatch"))
    return False

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="Captain Schedule Predictor",
    description="Upload dispatch XML schedules, store them in a database, and predict future captain work days.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Always return JSON error bodies so the frontend can display meaningful messages."""
    if isinstance(exc, StarletteHTTPException):
        detail = exc.detail
        if not isinstance(detail, str):
            detail = str(detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server error: {exc}"},
    )


@app.on_event("startup")
def on_startup():
    """Initialize database tables and seed the ship capacity registry."""
    init_db()
    db = next(get_db())
    try:
        seed_ship_capacities(db)
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload", response_model=UploadResult)
async def upload_xml(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Accept a dispatch XML upload, persist rows, and rebuild prediction patterns.

    Returns counts of imported vs. skipped (duplicate) rows. Entry-level parse
    warnings are included in the `notes` field.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        filename = _resolve_upload_filename(file.filename, file.content_type)
        if not _looks_like_xml(file.filename, file.content_type, content):
            raise HTTPException(
                status_code=400,
                detail="Could not recognize file as XML. Use an .xml file with <schedule> entries containing: date_header, ship, checkin_time, return_time, boat_codes",
            )

        batch_id, imported, skipped, errors = import_schedules(db, content, filename)
        rebuild_patterns(db)

        if imported == 0 and skipped == 0:
            detail = errors[0] if errors else "No valid entries could be imported from this XML"
            raise HTTPException(status_code=400, detail=detail)

        return UploadResult(
            batch_id=batch_id,
            filename=filename,
            rows_imported=imported,
            rows_skipped=skipped,
            notes="; ".join(errors[:5]) if errors else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc


@app.get("/api/schedules", response_model=list[ScheduleEntryOut])
def list_schedules(
    start_date: date | None = None,
    end_date: date | None = None,
    ship: str | None = None,
    boat_code: str | None = None,
    limit: int = Query(default=500, le=2000),
    db: Session = Depends(get_db),
):
    """Return stored schedule entries with optional date, ship, and captain filters."""
    q = db.query(ScheduleEntry)
    if start_date:
        q = q.filter(ScheduleEntry.schedule_date >= start_date)
    if end_date:
        q = q.filter(ScheduleEntry.schedule_date <= end_date)
    if ship:
        q = q.filter(ScheduleEntry.ship.ilike(f"%{ship}%"))
    if boat_code:
        q = q.filter(ScheduleEntry.boat_codes.ilike(f"%{boat_code}%"))

    return q.order_by(ScheduleEntry.schedule_date, ScheduleEntry.checkin_time).limit(limit).all()


@app.get("/api/predictions", response_model=list[CaptainPrediction])
def get_predictions(
    boat_code: str | None = None,
    days_ahead: int = Query(default=90, ge=7, le=365),
    min_confidence: float = Query(default=0.15, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    """Return forecasted captain shifts for the next N days."""
    return predict_captain_schedule(
        db,
        boat_code=boat_code,
        days_ahead=days_ahead,
        min_confidence=min_confidence,
    )


@app.get("/api/captains", response_model=list[CaptainSummary])
def list_captains(
    days_ahead: int = Query(default=90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Return per-captain summaries with next predicted shift."""
    return get_captain_summaries(db, days_ahead=days_ahead)


@app.get("/api/busy-calendar")
def busy_calendar(
    days_ahead: int = Query(default=90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Return daily port busyness estimates based on ship passenger capacity."""
    return get_busy_calendar(db, days_ahead=days_ahead)


@app.get("/api/ships", response_model=list[ShipCapacityOut])
def list_ships(db: Session = Depends(get_db)):
    """Return all known ship capacity records, sorted by passenger count."""
    return db.query(ShipCapacity).order_by(ShipCapacity.passenger_capacity.desc()).all()


@app.post("/api/ships/lookup")
def lookup_ship(ship_name: str, db: Session = Depends(get_db)):
    """Look up or enrich passenger capacity for a ship by name."""
    record = lookup_ship_online(db, ship_name)
    if not record:
        raise HTTPException(status_code=404, detail=f"No capacity data found for '{ship_name}'")
    return ShipCapacityOut.model_validate(record)


@app.post("/api/patterns/rebuild")
def patterns_rebuild(db: Session = Depends(get_db)):
    """Manually trigger a full rebuild of learned captain patterns."""
    count = rebuild_patterns(db)
    return {"patterns_rebuilt": count}


@app.get("/api/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    """Return aggregate counts for the dashboard header."""
    total = db.query(func.count(ScheduleEntry.id)).scalar() or 0
    ships = db.query(func.count(func.distinct(ScheduleEntry.ship))).scalar() or 0
    captains = db.query(func.count(func.distinct(ScheduleEntry.boat_codes))).scalar() or 0
    start = db.query(func.min(ScheduleEntry.schedule_date)).scalar()
    end = db.query(func.max(ScheduleEntry.schedule_date)).scalar()
    uploads = db.query(func.count(UploadLog.id)).scalar() or 0

    return StatsOut(
        total_entries=total,
        unique_ships=ships,
        unique_captains=captains,
        date_range_start=start,
        date_range_end=end,
        uploads=uploads,
    )


@app.get("/api/uploads")
def list_uploads(db: Session = Depends(get_db)):
    """Return recent XML upload history for the dashboard."""
    logs = db.query(UploadLog).order_by(UploadLog.uploaded_at.desc()).limit(20).all()
    return [
        {
            "batch_id": log.batch_id,
            "filename": log.filename,
            "rows_imported": log.rows_imported,
            "rows_skipped": log.rows_skipped,
            "notes": log.notes,
            "uploaded_at": log.uploaded_at.isoformat(),
        }
        for log in logs
    ]


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    """Serve the web dashboard (falls back to API info if static files are missing)."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Captain Schedule Predictor API. Place static files in /static or use /docs."}
