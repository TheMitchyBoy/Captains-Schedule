"""
FastAPI application entry point and REST API routes.

Serves two roles:
  1. JSON API at /api/* for schedule upload, queries, and predictions
  2. Static web dashboard at / for the browser-based UI

Upload flow: receive XML → parse & persist → rebuild captain patterns → return summary
"""
from datetime import date
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai_predictor import clear_ai_prediction_cache
from app.config import get_openai_status, get_settings, init_openai_verification
from app.csv_parser import import_csv_schedules, looks_like_csv
from app.xml_parser import import_schedules
from app.database import get_db, get_database_label, get_database_path, init_db, DATA_DIR
from app.models import CaptainPattern, ScheduleEntry, ShipCapacity, UploadLog
from app.predictor import (
    get_busy_calendar,
    get_captain_summaries,
    predict_captain_schedule,
    rebuild_patterns,
)
from app.schemas import (
    AiPredictionMeta,
    BusyCalendarResponse,
    CaptainPrediction,
    CaptainSummariesResponse,
    CaptainSummary,
    PredictionsResponse,
    ScheduleEntryOut,
    ScheduleEntryUpdate,
    ScheduleEntryCreate,
    ScheduleBulkCreate,
    ScheduleBulkResult,
    ShipCapacityOut,
    StatsOut,
    StorageStatusOut,
    UploadResult,
    XmlCleanResult,
    RepairRecordOut,
)
from app.schedule_repair import repair_schedule_berth_mixups
from app.schedule_dedup import deduplicate_schedule_entries
from app.schedule_update import bulk_create_schedule_entries, create_schedule_entry, update_schedule_entry
from app.ship_data import lookup_ship_online, seed_ship_capacities
from app.xml_cleaner import clean_xml_content


def _resolve_upload_filename(filename: str | None, content_type: str | None) -> str:
    """Use the uploaded filename, or fall back when browsers omit it on drag-and-drop."""
    if filename and filename.strip():
        return filename.strip()
    if content_type and "csv" in content_type.lower():
        return "upload.csv"
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


def _finalize_upload(
    db: Session,
    batch_id: str,
    filename: str,
    imported: int,
    skipped: int,
    errors: list[str],
    *,
    replaced: int = 0,
) -> UploadResult:
    """Rebuild patterns after a successful import and return API response."""
    repaired = repair_schedule_berth_mixups(db)
    rebuild_patterns(db)
    clear_ai_prediction_cache()

    deduped = deduplicate_schedule_entries(db)
    if deduped["rows_deleted"]:
        rebuild_patterns(db)
        clear_ai_prediction_cache()

    notes_parts: list[str] = []
    if replaced:
        notes_parts.append(f"Replaced {replaced} existing schedule rows")
    if repaired:
        notes_parts.append(f"Repaired {repaired} berth/boat field mixups")
    if deduped["rows_deleted"]:
        notes_parts.append(
            f"Removed {deduped['rows_deleted']} duplicate rows ({deduped['rows_remaining']} remaining)"
        )
    if errors:
        notes_parts.extend(errors[:5])

    return UploadResult(
        batch_id=batch_id,
        filename=filename,
        rows_imported=imported,
        rows_skipped=skipped,
        notes="; ".join(notes_parts) if notes_parts else None,
    )


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
    """Initialize database tables, seed ship data, restore patterns, verify OpenAI."""
    init_db()
    db = next(get_db())
    try:
        seed_ship_capacities(db)
        entry_count = db.query(func.count(ScheduleEntry.id)).scalar() or 0
        pattern_count = db.query(func.count(CaptainPattern.id)).scalar() or 0
        if entry_count:
            repaired = repair_schedule_berth_mixups(db)
            if repaired:
                print(f"Repaired {repaired} schedule rows with berth/boat field mixups")
                rebuild_patterns(db)
            deduped = deduplicate_schedule_entries(db)
            if deduped["rows_deleted"]:
                print(
                    f"Removed {deduped['rows_deleted']} duplicate schedule rows "
                    f"({deduped['rows_remaining']} remaining)"
                )
                rebuild_patterns(db)
        if entry_count and pattern_count == 0:
            rebuilt = rebuild_patterns(db)
            print(f"Restored {rebuilt} captain patterns from {entry_count} stored schedule entries")
        elif entry_count:
            print(f"Loaded {entry_count} schedule entries from {get_database_path()}")
    finally:
        db.close()

    settings = get_settings()
    if settings.openai.is_configured:
        ok, message = init_openai_verification()
        if ok:
            print(f"OpenAI integration ready: {message}")
        else:
            print(f"OpenAI API key set but verification failed: {message}")


@app.get("/api/health")
def health():
    """Health check including whether AI-assisted XML recovery is available."""
    return {"status": "ok", "ai": get_openai_status()}


def _clean_result_response(result) -> XmlCleanResult:
    """Map internal CleanResult dataclass to API response schema."""
    return XmlCleanResult(
        cleaned_xml=result.cleaned_xml,
        entries_processed=result.analysis.entries_found,
        times_normalized=result.analysis.times_normalized,
        boat_fields_repaired=result.analysis.boat_fields_repaired,
        parse_method=result.analysis.parse_method,
        ai_assisted=result.analysis.ai_assisted,
        hour_distribution=result.analysis.hour_distribution,
        repairs=[
            RepairRecordOut(
                entry_index=r.entry_index,
                field=r.field,
                issue=r.issue,
                before=r.before,
                after=r.after,
                confidence=r.confidence,
            )
            for r in result.repairs
        ],
        warnings=result.analysis.warnings,
        errors=result.errors,
    )


@app.post("/api/clean-xml", response_model=XmlCleanResult)
async def clean_xml_endpoint(
    file: UploadFile | None = File(None),
    raw_xml: str | None = Form(None),
):
    """
    Accept raw XML (file upload or pasted text), re-parse, repair, and return cleaned XML.

    Repairs include 24-hour time normalization and moving leaked time fragments
    (15am:, 30am:, 15pm:, 30pm:) from boat_codes back into checkin_time.
    """
    content: bytes | str | None = None

    if file and file.filename:
        content = await file.read()
    elif raw_xml and raw_xml.strip():
        content = raw_xml
    else:
        raise HTTPException(status_code=400, detail="Provide an XML file or raw_xml form field")

    if not content:
        raise HTTPException(status_code=400, detail="XML input is empty")

    result = clean_xml_content(content)
    if not result.entries and not result.cleaned_xml:
        raise HTTPException(
            status_code=400,
            detail=result.errors[0] if result.errors else "Could not parse or repair XML",
        )

    return _clean_result_response(result)


@app.post("/api/clean-xml/json", response_model=XmlCleanResult)
async def clean_xml_json(payload: dict = Body(...)):
    """Accept raw XML in a JSON body: {"xml": "<schedules>...</schedules>"}."""
    raw_xml = payload.get("xml")
    if not raw_xml or not str(raw_xml).strip():
        raise HTTPException(status_code=400, detail="JSON body must include non-empty 'xml' field")

    result = clean_xml_content(str(raw_xml))
    if not result.entries and not result.cleaned_xml:
        raise HTTPException(
            status_code=400,
            detail=result.errors[0] if result.errors else "Could not parse or repair XML",
        )

    return _clean_result_response(result)


@app.post("/api/upload-csv", response_model=UploadResult)
async def upload_csv(
    file: UploadFile = File(...),
    replace: bool = Query(default=False, description="Delete all existing schedule data before import"),
    db: Session = Depends(get_db),
):
    """
    Upload a cruise ship schedule CSV file and save it to the database.

    Expected columns (flexible names): date, ship, arrival, departure, berth
    Set replace=true to wipe incorrect schedule data before importing the CSV.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        filename = file.filename.strip() if file.filename else "upload.csv"
        if not looks_like_csv(file.filename, file.content_type, content):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not recognize file as a schedule CSV. "
                    "Include headers such as: date, ship, arrival, departure, berth"
                ),
            )

        from app.schedule_import import clear_schedule_data

        replaced = clear_schedule_data(db) if replace else 0
        batch_id, imported, skipped, errors = import_csv_schedules(
            db, content, filename, replace_existing=False
        )

        if imported == 0 and skipped == 0:
            detail = errors[0] if errors else "No valid rows could be imported from this CSV"
            raise HTTPException(status_code=400, detail=detail)

        result = _finalize_upload(
            db, batch_id, filename, imported, skipped, errors, replaced=replaced
        )
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"CSV upload failed: {exc}") from exc


@app.post("/api/upload", response_model=UploadResult)
async def upload_xml(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Accept a dispatch XML or schedule CSV upload, persist rows, and rebuild patterns.

    Returns counts of imported vs. skipped (duplicate) rows. Entry-level parse
    warnings are included in the `notes` field.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        filename = _resolve_upload_filename(file.filename, file.content_type)
        if file.filename and file.filename.lower().endswith(".csv"):
            filename = file.filename.strip()

        if looks_like_csv(file.filename, file.content_type, content):
            batch_id, imported, skipped, errors = import_csv_schedules(db, content, filename)
        elif _looks_like_xml(file.filename, file.content_type, content):
            batch_id, imported, skipped, errors = import_schedules(db, content, filename)
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not recognize file type. Upload .csv (date, ship, arrival, departure) "
                    "or .xml dispatch schedule files."
                ),
            )

        if imported == 0 and skipped == 0:
            detail = errors[0] if errors else "No valid entries could be imported"
            raise HTTPException(status_code=400, detail=detail)

        return _finalize_upload(db, batch_id, filename, imported, skipped, errors)
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
    manual_only: bool = Query(default=False, description="Only rows added manually in the dashboard"),
    through_today: bool = Query(
        default=False,
        description="Exclude future schedule dates (for Raw Schedules historical view)",
    ),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=500, le=2000),
    db: Session = Depends(get_db),
):
    """Return stored schedule entries with optional date, ship, and captain filters."""
    q = db.query(ScheduleEntry)
    if start_date:
        q = q.filter(ScheduleEntry.schedule_date >= start_date)

    effective_end = end_date
    if through_today:
        today = date.today()
        effective_end = min(end_date, today) if end_date else today
    if effective_end:
        q = q.filter(ScheduleEntry.schedule_date <= effective_end)
    if ship:
        q = q.filter(ScheduleEntry.ship.ilike(f"%{ship}%"))
    if boat_code:
        q = q.filter(ScheduleEntry.boat_codes.ilike(f"%{boat_code}%"))
    if manual_only:
        q = q.filter(ScheduleEntry.upload_batch_id.like("manual%"))

    if order == "desc":
        q = q.order_by(ScheduleEntry.schedule_date.desc(), ScheduleEntry.checkin_time.desc())
    else:
        q = q.order_by(ScheduleEntry.schedule_date, ScheduleEntry.checkin_time)

    return q.limit(limit).all()


@app.post("/api/schedules", response_model=ScheduleEntryOut, status_code=201)
def create_schedule(
    payload: ScheduleEntryCreate,
    db: Session = Depends(get_db),
):
    """Add a new tour row for a date, ship, and time slot."""
    try:
        entry = create_schedule_entry(
            db,
            schedule_date=payload.schedule_date,
            ship=payload.ship,
            checkin_time=payload.checkin_time,
            return_time=payload.return_time,
            boat_codes=payload.boat_codes,
            berth=payload.berth,
            date_header=payload.date_header,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rebuild_patterns(db)
    clear_ai_prediction_cache()
    return entry


@app.post("/api/schedules/bulk", response_model=ScheduleBulkResult)
def create_schedules_bulk(
    payload: ScheduleBulkCreate,
    db: Session = Depends(get_db),
):
    """Parse and add multiple tour lines for a single day from pasted dispatch text."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Paste one or more tour lines to import")

    result = bulk_create_schedule_entries(
        db,
        text,
        payload.schedule_date,
        use_ai=payload.use_ai,
    )
    if result.rows_created == 0 and result.rows_merged == 0 and not result.rows_parsed:
        detail_parts = result.errors[:3] if result.errors else ["No tour lines could be imported"]
        detail = ". ".join(detail_parts)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{detail}. Try one ship per block with boat codes and times, "
                "or use dispatch format: Ship — BW, BWA — 6:15-10:30"
            ),
        )

    if result.rows_created or result.rows_merged:
        rebuild_patterns(db)
        clear_ai_prediction_cache()

    return ScheduleBulkResult(
        rows_parsed=result.rows_parsed,
        rows_created=result.rows_created,
        rows_merged=result.rows_merged,
        rows_skipped=result.rows_skipped,
        errors=result.errors,
        ai_assisted=result.ai_assisted,
        ai_message=result.ai_message,
    )


@app.patch("/api/schedules/{entry_id}", response_model=ScheduleEntryOut)
def patch_schedule_entry(
    entry_id: int,
    payload: ScheduleEntryUpdate,
    db: Session = Depends(get_db),
):
    """Update check-in, return, and/or boat codes on one schedule row."""
    if payload.checkin_time is None and payload.return_time is None and payload.boat_codes is None:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    try:
        entry = update_schedule_entry(
            db,
            entry_id,
            checkin_time=payload.checkin_time,
            return_time=payload.return_time,
            boat_codes=payload.boat_codes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rebuild_patterns(db)
    clear_ai_prediction_cache()
    return entry


@app.get("/api/predictions", response_model=PredictionsResponse)
def get_predictions(
    boat_code: str | None = None,
    days_ahead: int = Query(default=90, ge=7, le=365),
    min_confidence: float = Query(default=0.15, ge=0.0, le=1.0),
    use_ai: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Return forecasted captain shifts for the next N days, optionally AI-enhanced."""
    predictions, meta = predict_captain_schedule(
        db,
        boat_code=boat_code,
        days_ahead=days_ahead,
        min_confidence=min_confidence,
        use_ai=use_ai,
    )
    return PredictionsResponse(
        predictions=predictions,
        ai=AiPredictionMeta(**meta),
    )


@app.get("/api/captains", response_model=CaptainSummariesResponse)
def list_captains(
    days_ahead: int = Query(default=90, ge=7, le=365),
    use_ai: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Return per-captain summaries with next predicted shift."""
    captains, meta = get_captain_summaries(db, days_ahead=days_ahead, use_ai=use_ai)
    return CaptainSummariesResponse(
        captains=captains,
        ai=AiPredictionMeta(**meta),
    )


@app.get("/api/busy-calendar", response_model=BusyCalendarResponse)
def busy_calendar(
    days_ahead: int = Query(default=90, ge=7, le=365),
    use_ai: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Return daily port busyness estimates based on ship passenger capacity."""
    calendar, meta = get_busy_calendar(db, days_ahead=days_ahead, use_ai=use_ai)
    return BusyCalendarResponse(
        calendar=calendar,
        ai=AiPredictionMeta(**meta),
    )


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


@app.post("/api/schedules/deduplicate")
def deduplicate_schedules(db: Session = Depends(get_db)):
    """Merge and delete duplicate schedule rows (same ship/date/times)."""
    repair_schedule_berth_mixups(db)
    result = deduplicate_schedule_entries(db)
    if result["rows_deleted"]:
        rebuild_patterns(db)
        clear_ai_prediction_cache()
    return result


@app.get("/api/storage", response_model=StorageStatusOut)
def storage_status(db: Session = Depends(get_db)):
    """Return persistence status so clients know saved data is available without re-upload."""
    total = db.query(func.count(ScheduleEntry.id)).scalar() or 0
    uploads = db.query(func.count(UploadLog.id)).scalar() or 0
    patterns = db.query(func.count(CaptainPattern.id)).scalar() or 0
    start = db.query(func.min(ScheduleEntry.schedule_date)).scalar()
    end = db.query(func.max(ScheduleEntry.schedule_date)).scalar()
    last_upload = db.query(func.max(UploadLog.uploaded_at)).scalar()

    ready = total > 0 and patterns > 0
    if ready:
        message = (
            "Schedule data is saved in the database. Open the app anytime to view predictions "
            "— upload additional XML files only when you have new dispatch schedules."
        )
    elif total > 0:
        message = "Schedule entries found; rebuilding prediction patterns."
    else:
        message = "No schedule data yet. Upload one or more XML files to start — data will be saved for future visits."

    return StorageStatusOut(
        database_path=get_database_label(),
        data_dir=str(DATA_DIR.resolve()),
        total_entries=total,
        uploads=uploads,
        patterns_learned=patterns,
        date_range_start=start,
        date_range_end=end,
        last_upload_at=last_upload,
        ready_for_predictions=ready,
        message=message,
    )


@app.get("/api/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    """Return aggregate counts for the dashboard header."""
    total = db.query(func.count(ScheduleEntry.id)).scalar() or 0
    ships = db.query(func.count(func.distinct(ScheduleEntry.ship))).scalar() or 0
    captains = db.query(func.count(func.distinct(ScheduleEntry.boat_codes))).scalar() or 0
    start = db.query(func.min(ScheduleEntry.schedule_date)).scalar()
    end = db.query(func.max(ScheduleEntry.schedule_date)).scalar()
    uploads = db.query(func.count(UploadLog.id)).scalar() or 0
    patterns = db.query(func.count(CaptainPattern.id)).scalar() or 0
    last_upload = db.query(func.max(UploadLog.uploaded_at)).scalar()

    return StatsOut(
        total_entries=total,
        unique_ships=ships,
        unique_captains=captains,
        date_range_start=start,
        date_range_end=end,
        uploads=uploads,
        patterns_learned=patterns,
        last_upload_at=last_upload,
        has_stored_data=total > 0,
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
