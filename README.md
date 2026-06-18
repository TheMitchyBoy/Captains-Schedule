# Captain Schedule Predictor

Upload cruise dispatch XML schedules, store them in a SQLite database, and predict when captains will work — as far into the future as your historical data allows.

## Features

- **XML upload** — Drag-and-drop dispatch schedules with elements: `date_header`, `ship`, `checkin_time`, `return_time`, `boat_codes`
- **Persistent database** — Every upload is stored and deduplicated; patterns improve with more data
- **Captain predictions** — Learns day-of-week patterns per captain/boat code and forecasts shifts up to 365 days ahead
- **Busy day calendar** — Estimates port busyness using passenger capacity data for 100+ major cruise ships
- **XML repair tool** — Re-parse raw XML, normalize times to 24-hour `HH:MM`, and fix corrupted boat fields (`15am:`, `30am:`, `15pm:`, `30pm:`)

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
sample_data/sample_schedule.xml
```

## XML Format

Each schedule file contains one or more `<schedule>` entries inside a root `<schedules>` element:

| Element        | Description                                                 |
| -------------- | ----------------------------------------------------------- |
| date_header    | Original dispatch date line (e.g. "Thursday 6/4 - 6 ships") |
| ship           | Cruise ship name                                            |
| checkin_time   | Check-in time                                               |
| return_time    | Return time                                                 |
| boat_codes     | Assigned boat/operator codes (comma or slash separated)     |

Example:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<schedules>
  <schedule>
    <date_header>Thursday 6/4 - 5 ships</date_header>
    <ship>Norwegian Bliss</ship>
    <checkin_time>6:00 AM</checkin_time>
    <return_time>1:15 PM</return_time>
    <boat_codes>CPT-A</boat_codes>
  </schedule>
  <schedule>
    <date_header>Friday 6/5 - 5 ships</date_header>
    <ship>Eurodam</ship>
    <checkin_time>8:00 AM</checkin_time>
    <return_time>5:00 PM</return_time>
    <boat_codes>CPT-B</boat_codes>
  </schedule>
</schedules>
```

Alternative element names are also accepted (e.g. `<entry>`, `<vessel>`, `<check_in_time>`, `<boat_code>`).

## XML Repair Tool

The cleaner re-parses raw XML and applies automated data analysis repairs before import.

**Repairs performed:**
- Converts `checkin_time` and `return_time` to 24-hour `HH:MM` format (e.g. `7:00 AM` → `07:00`, `4:30 PM` → `16:30`)
- Detects boat fields that incorrectly start with `15am:`, `30am:`, `15pm:`, or `30pm:` and moves those minutes back into check-in time (e.g. `7am` + `30am:CPT-A` → `07:30` / `CPT-A`)

**CLI usage:**

```bash
python repair_xml.py sample_data/malformed_schedule.xml           # print cleaned XML
python repair_xml.py sample_data/malformed_schedule.xml -o out.xml
python repair_xml.py sample_data/malformed_schedule.xml --report  # JSON repair report
```

**API usage:**

```bash
curl -X POST http://localhost:8000/api/clean-xml/json \
  -H "Content-Type: application/json" \
  -d '{"xml": "<schedules>...</schedules>"}'
```

The web dashboard includes an **XML Repair** tab for pasting raw XML, viewing the repair report, and copying cleaned output.

Optional AI-assisted recovery for badly malformed XML runs automatically when `OPENAI_API_KEY` is set (see `.env.example`). Check status at `GET /api/health`.

## API Endpoints

| Method | Path                  | Description                          |
| ------ | --------------------- | ------------------------------------ |
| POST   | `/api/upload`         | Upload an XML file (auto-cleaned on import) |
| POST   | `/api/clean-xml`      | Clean/repair XML (file upload or form field) |
| POST   | `/api/clean-xml/json` | Clean/repair XML (`{"xml": "..."}` body)     |
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
4. **Scheduling constraints** — Predictions enforce one boat per ship at a time, no overlapping captain shifts, alphabetical boat dispatch, and a 3-hour minimum between consecutive tours for the same boat
5. **Busy day weighting** — Ship passenger counts (from a built-in registry of major cruise lines) estimate how busy each day will be

The more XML data you upload over time, the more accurate and far-reaching the predictions become.

## Database

Data is stored in `captain_scheduler.db` (SQLite) with tables for:

- `schedule_entries` — Raw uploaded schedule rows
- `captain_patterns` — Learned day-of-week assignment patterns
- `ship_capacities` — Cruise ship passenger counts
- `upload_logs` — Upload history and import stats

## Ship Capacity Data

The app includes passenger capacity for 100+ ships from Royal Caribbean, Carnival, Norwegian, MSC, Disney, Celebrity, and other major lines. Unknown ships are estimated at 2,500 passengers and can be looked up via the API.
