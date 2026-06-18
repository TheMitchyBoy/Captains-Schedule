from datetime import date, datetime

from pydantic import BaseModel, Field


class ScheduleEntryOut(BaseModel):
    id: int
    date_header: str
    schedule_date: date
    ship: str
    checkin_time: str
    return_time: str
    boat_codes: str
    ship_count: int | None
    upload_batch_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UploadResult(BaseModel):
    batch_id: str
    filename: str
    rows_imported: int
    rows_skipped: int
    notes: str | None = None


class CaptainPrediction(BaseModel):
    boat_code: str
    schedule_date: date
    day_of_week: str
    ship: str
    checkin_time: str
    return_time: str
    confidence: float = Field(ge=0.0, le=1.0)
    passenger_estimate: int | None = None
    busy_score: float = Field(ge=0.0, le=1.0, description="How busy the day is expected to be")
    source: str = "pattern"


class CaptainSummary(BaseModel):
    boat_code: str
    total_historical_shifts: int
    predicted_shifts: int
    next_shift: CaptainPrediction | None = None


class StatsOut(BaseModel):
    total_entries: int
    unique_ships: int
    unique_captains: int
    date_range_start: date | None
    date_range_end: date | None
    uploads: int


class ShipCapacityOut(BaseModel):
    ship_name: str
    passenger_capacity: int
    cruise_line: str | None
    source: str

    model_config = {"from_attributes": True}
