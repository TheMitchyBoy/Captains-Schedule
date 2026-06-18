# Captain Schedule Predictor

Upload cruise dispatch CSV schedules, store them in a SQLite database, and predict when captains will work — as far into the future as your historical data allows.

## Features

- **CSV upload** — Drag-and-drop dispatch schedules with columns: `date_header`, `ship`, `checkin_time`, `return_time`, `boat_codes`
- **Persistent database** — Every upload is stored and deduplicated; patterns improve with more data
- **Captain predictions** — Learns day-of-week patterns per captain/boat code and forecasts shifts up to 365 days ahead
- **Busy day calendar** — Estimates port busyness using passenger capacity data for 100+ major cruise ships
- **Web dashboard** — View predictions, captain overviews, upload history, and raw schedules

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

Upload the included sample file to try it out:

```
sample_data/sample_schedule.csv
```

## CSV Format

| Column       | Description                                                 |
| ------------ | ----------------------------------------------------------- |
| date_header  | Original dispatch date line (e.g. "Thursday 6/4 - 6 ships") |
| ship         | Cruise ship name                                            |
| checkin_time | Check-in time                                               |
| return_time  | Return time                                                 |
| boat_codes   | Assigned boat/operator codes (comma or slash separated)     |

Example:

```csv
date_header,ship,checkin_time,return_time,boat_codes
Thursday 6/4 - 6 ships,Symphony of the Seas,7:00 AM,4:30 PM,CPT-A / OP-12
Friday 6/5 - 5 ships,Carnival Horizon,8:00 AM,5:00 PM,CPT-B
```

## API Endpoints

| Method | Path                  | Description                          |
| ------ | --------------------- | ------------------------------------ |
| POST   | `/api/upload`         | Upload a CSV file                    |
| GET    | `/api/schedules`      | List stored schedule entries         |
| GET    | `/api/predictions`    | Get future captain shift predictions |
| GET    | `/api/captains`       | Captain overview with next shift     |
| GET    | `/api/busy-calendar`  | Daily busyness estimates             |
| GET    | `/api/ships`          | Ship capacity registry               |
| GET    | `/api/stats`          | Database statistics                  |
| POST   | `/api/patterns/rebuild` | Rebuild learned patterns           |

Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## How Predictions Work

1. **Pattern learning** — After each upload, the system analyzes which captains worked which ships on which days of the week
2. **Confidence scoring** — Patterns with more historical occurrences get higher confidence
3. **Future projection** — Patterns are projected forward for 30–365 days
4. **Busy day weighting** — Ship passenger counts (from a built-in registry of major cruise lines) estimate how busy each day will be

The more CSV data you upload over time, the more accurate and far-reaching the predictions become.

## Database

Data is stored in `captain_scheduler.db` (SQLite) with tables for:

- `schedule_entries` — Raw uploaded schedule rows
- `captain_patterns` — Learned day-of-week assignment patterns
- `ship_capacities` — Cruise ship passenger counts
- `upload_logs` — Upload history and import stats

## Ship Capacity Data

The app includes passenger capacity for 100+ ships from Royal Caribbean, Carnival, Norwegian, MSC, Disney, Celebrity, and other major lines. Unknown ships are estimated at 2,500 passengers and can be looked up via the API.
