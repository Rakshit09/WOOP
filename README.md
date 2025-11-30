# Seamless Timesheet App

A zero-friction Flask-based web application for weekly resource scheduling and timesheet management. Designed to replace legacy Power Apps solutions with a modern, fast, and intuitive interface.

## Features

- **Zero-Friction Design**: Minimal clicks, instant feedback, no page reloads
- **Type-Ahead Project Search**: Fast client-side filtering with HTML5 datalist
- **Real-Time Validation**: Visual feedback on total days (target: 5.0 days/week)
- **Copy Last Week**: One-click replication of previous timesheet
- **Granular Time Entry**: Support for 0.5 day increments
- **Transaction-Safe**: Atomic database operations prevent data corruption
- **SSO Integration**: Authentication via Posit Connect headers

## Technology Stack

- **Backend**: Python 3.10+, Flask, Flask-SQLAlchemy
- **Database**: SQLite (production-ready for MSSQL migration)
- **Frontend**: HTML5, Vanilla JavaScript (ES6), Tailwind CSS
- **Data Source**: CSV-based project reference data

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialize Database

The database will be automatically created on first run. To manually initialize:

```python
from app import init_db
init_db()
```

### 3. Configure Projects

Edit `projects.csv` to add your organization's projects:

```csv
ProjectName,Active
Project Alpha,True
Internal - Admin,True
Legacy Project,False
```

### 4. Run the Application

```bash
python app.py
```

The app will start on `http://localhost:5000`

## Development Mode

For local testing without Posit Connect headers:

```
http://localhost:5000?user=your.email@example.com
```

## Project Structure

```
/root
  ├── app.py                # Main Flask application
  ├── projects.csv          # Project reference data
  ├── timesheet.db          # SQLite database (auto-created)
  ├── requirements.txt      # Python dependencies
  ├── README.md             # Documentation
  └── templates/
      └── index.html        # Single Page Application
```

## Database Schema

### TimesheetEntry Table

| Column           | Type     | Description                          |
|------------------|----------|--------------------------------------|
| id               | Integer  | Primary key (auto-increment)         |
| user_email       | String   | User identifier from SSO             |
| week_commencing  | String   | Monday date (YYYY-MM-DD)             |
| project_name     | String   | Selected project name                |
| days             | Float    | Time allocated (0.5 increments)      |
| notes            | Text     | Task description                     |
| submitted_at     | DateTime | Submission timestamp                 |

## API Endpoints

### `GET /`
Renders the main timesheet interface with user context and project list.

### `GET /api/get_history`
Returns the most recent week's timesheet entries for the authenticated user.

**Response:**
```json
[
  {
    "project": "Project Alpha",
    "days": 2.5,
    "notes": "Feature development"
  }
]
```

### `POST /submit`
Submits timesheet entries for a specific week (overwrites existing entries).

**Request:**
```json
{
  "date": "2025-12-02",
  "rows": [
    {
      "project": "Project Alpha",
      "days": 3.0,
      "notes": "Implementation"
    }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "message": "Timesheet submitted successfully for week of 2025-12-02"
}
```

## User Interface

### Color-Coded Totals
- **Orange**: < 5.0 days (incomplete)
- **Green**: = 5.0 days (target met)
- **Red**: > 5.0 days (over-allocation)

### Key Interactions
1. **Add Row**: Click "➕ Add Row" to create new entries
2. **Delete Row**: Click 🗑️ to remove individual entries
3. **Copy Last Week**: Click "📋 Copy Last Week" to replicate previous timesheet
4. **Submit**: Click "✓ Submit Timesheet" to save entries

## Deployment to Posit Connect

1. Package the application directory
2. Deploy via Posit Connect dashboard or CLI
3. Configure authentication to pass `X-Auth-User` header
4. Set appropriate permissions for user access

## Migration to MSSQL

To migrate from SQLite to MSSQL:

1. Update `app.py` configuration:

```python
app.config['SQLALCHEMY_DATABASE_URI'] = 'mssql+pyodbc://user:pass@server/db?driver=ODBC+Driver+17+for+SQL+Server'
```

2. Install additional dependency:

```bash
pip install pyodbc
```

3. Run database migration or recreate tables

## Future Enhancements

- **Magic Link "Chaser" Workflow**: Automated email reminders with one-click submission
- **Historical View**: Display past timesheet submissions
- **Export Functionality**: Download timesheets as CSV/Excel
- **Admin Dashboard**: View team-wide submissions and analytics

## License

Internal use only - [Your Organization]

## Support

For issues or questions, contact: [Your Support Email]

