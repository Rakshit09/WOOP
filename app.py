"""
WOOP 2.0 Architecture - Forecast & Actuals Split
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from functools import lru_cache
import pandas as pd
import requests
import urllib3
import json
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
app = Flask(__name__)

# Configuration - Dual Database Binds
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timesheet_forecast.db'  # Default/fallback
app.config['SQLALCHEMY_BINDS'] = {
    'forecast': 'sqlite:///timesheet_forecast.db',
    'current': 'sqlite:///timesheet_current.db'
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)


# ============================
# Database Models
# ============================
class ForecastEntry(db.Model):
    """Stores forecast timesheet entries (Mondays - planning next week)."""
    __tablename__ = 'forecast_entry'
    __bind_key__ = 'forecast'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    survey_id = db.Column(db.String(50), nullable=False, index=True)  # "YYYY-MM-DD (Forecast)"
    team_member = db.Column(db.String(255), nullable=False, index=True)
    assignment = db.Column(db.String(255), nullable=False)
    days_allocated = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    modified = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<ForecastEntry {self.team_member} - {self.survey_id} - {self.assignment}>'


class CurrentEntry(db.Model):
    """Stores current/actual timesheet entries (Fridays - backcasting confirmed work)."""
    __tablename__ = 'current_entry'
    __bind_key__ = 'current'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    survey_id = db.Column(db.String(50), nullable=False, index=True)  # "YYYY-MM-DD (Actual)"
    team_member = db.Column(db.String(255), nullable=False, index=True)
    assignment = db.Column(db.String(255), nullable=False)
    days_allocated = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    modified = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<CurrentEntry {self.team_member} - {self.survey_id} - {self.assignment}>'


# ============================
# Date Utilities
# ============================
def get_next_monday():
    """Calculate the date of the upcoming Monday in YYYY-MM-DD format.
    If today is Monday, returns today. Otherwise returns next Monday.
    """
    today = datetime.now().date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0 and today.weekday() != 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)
    return next_monday.strftime('%Y-%m-%d')


def get_last_friday():
    """Calculate the date of the most recent Friday."""
    today = datetime.now().date()
    days_since_friday = (today.weekday() - 4) % 7
    if days_since_friday == 0 and today.weekday() != 4:
        days_since_friday = 7
    last_friday = today - timedelta(days=days_since_friday)
    return last_friday.strftime('%Y-%m-%d')


def get_mondays_range(weeks_back=8, weeks_forward=2):
    """Get list of Mondays for the activity map (from Jan 1 of current year to end of year)."""
    today = datetime.now().date()
    year_start = datetime(today.year, 1, 1).date()
    
    # Find first Monday of the year (or first Monday after Jan 1)
    days_until_monday = (7 - year_start.weekday()) % 7
    if year_start.weekday() == 0:
        first_monday = year_start
    else:
        first_monday = year_start + timedelta(days=days_until_monday)
    
    mondays = []
    current_monday = first_monday
    year_end = datetime(today.year, 12, 31).date()
    
    while current_monday <= year_end:
        mondays.append(current_monday.strftime('%Y-%m-%d'))
        current_monday += timedelta(weeks=1)
    
    return mondays


def get_fridays_range(weeks_back=8, weeks_forward=2):
    """Get list of Fridays for the activity map (from Jan 1 of current year to end of year)."""
    today = datetime.now().date()
    year_start = datetime(today.year, 1, 1).date()
    
    # Find first Friday of the year
    days_until_friday = (4 - year_start.weekday()) % 7
    if year_start.weekday() == 4:
        first_friday = year_start
    else:
        first_friday = year_start + timedelta(days=days_until_friday)
    
    fridays = []
    current_friday = first_friday
    year_end = datetime(today.year, 12, 31).date()
    
    while current_friday <= year_end:
        fridays.append(current_friday.strftime('%Y-%m-%d'))
        current_friday += timedelta(weeks=1)
    
    return fridays


def get_date_status(date_str, entry_type, has_entry):
    """
    Determine the status of a date cell for the activity map.
    
    Returns: 'green', 'red', 'blue', 'gray'
    - Green: Entry exists in DB
    - Red: Missing Actual (past Friday without entry)
    - Blue: Next Forecast (upcoming Monday for input)
    - Gray: Expired Forecast or Future Actual (disabled)
    """
    today = datetime.now().date()
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    if has_entry:
        return 'green'
    
    if entry_type == 'forecast':
        # Forecasts are for Mondays
        next_monday = datetime.strptime(get_next_monday(), '%Y-%m-%d').date()
        if date_obj == next_monday:
            return 'blue'  # Open for input
        elif date_obj < today:
            return 'gray'  # Expired - no backfill allowed
        else:
            return 'gray'  # Future - not yet available
    
    else:  # actuals
        # Actuals are for Fridays
        if date_obj > today:
            return 'gray'  # Future - locked
        else:
            return 'red'  # Past Friday without entry - Missing!


# ============================
# Load Projects from CSV
# ============================
def load_active_projects():
    """Load active projects from projects.csv file."""
    csv_path = os.path.join(os.path.dirname(__file__), 'projects.csv')
    
    if not os.path.exists(csv_path):
        print(f"Warning: {csv_path} not found. Using empty project list.")
        return []
    
    try:
        df = pd.read_csv(csv_path)
        active_df = df[(df['Active'] == True) | (df['Active'] == 'True')]
        active_df['Index'] = pd.to_numeric(active_df['Index'], errors='coerce')
        active_df = active_df.sort_values('Index', ascending=True)
        projects = active_df['Title'].tolist()
        return projects
    except Exception as e:
        print(f"Error loading projects.csv: {e}")
        return []


# ============================
# User Authentication
# ============================
@lru_cache(maxsize=100)
def lookup_email_by_username(username):
    """Fetch user email from Posit Connect API."""
    connect_server = os.environ.get('CONNECT_SERVER', '').rstrip('/')
    api_key = os.environ.get('CONNECT_API_KEY', '')
    
    if not connect_server or not api_key:
        return None
    
    try:
        response = requests.get(
            f'{connect_server}/__api__/v1/users',
            headers={'Authorization': f'Key {api_key}'},
            params={'prefix': username},
            timeout=10,
            verify=False 
        )
        
        if response.status_code == 200:
            users = response.json().get('results', [])
            for user in users:
                if user.get('username') == username:
                    return user.get('email')
                    
    except Exception as e:
        print(f"Error looking up user email: {e}")
    
    return None


def get_user_email():
    """Extract user email from Posit Connect."""
    username = None
    
    credentials_header = request.headers.get('Rstudio-Connect-Credentials')
    if credentials_header:
        try:
            credentials = json.loads(credentials_header)
            username = credentials.get('user')
        except json.JSONDecodeError:
            print(f"Failed to parse credentials: {credentials_header}")
    
    if not username:
        if os.environ.get('FLASK_DEBUG') or app.debug:
            return request.args.get('user', 'dev.user@gallagherre.com')
        return None
    
    email = lookup_email_by_username(username)
    return email if email else username


# ============================
# Routes
# ============================
@app.route('/')
def index():
    """Render the main timesheet interface with Activity Map."""
    user_email = get_user_email()
    
    if not user_email:
        return "Unable to identify user. Please ensure you are logged in.", 401
    
    default_date = get_next_monday()
    projects = load_active_projects()
    
    return render_template(
        'index.html',
        user_email=user_email,
        default_date=default_date,
        projects=projects
    )


@app.route('/templates/logo.png')
def serve_logo():
    """Serve the logo image from the templates folder."""
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    return send_from_directory(templates_dir, 'logo.png')


@app.route('/api/activity_map')
def get_activity_map():
    """
    Get activity map data for the GitHub-style heatmap.
    Returns status for each Monday (forecast) and Friday (actual) in the range.
    """
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    mondays = get_mondays_range()
    fridays = get_fridays_range()
    
    # Get all forecast entries for this user
    forecast_entries = ForecastEntry.query.filter_by(team_member=user_email).all()
    forecast_dates = set()
    for entry in forecast_entries:
        # Extract date from survey_id "YYYY-MM-DD (Forecast)"
        date_part = entry.survey_id.split(' ')[0]
        forecast_dates.add(date_part)
    
    # Get all current/actual entries for this user
    current_entries = CurrentEntry.query.filter_by(team_member=user_email).all()
    current_dates = set()
    for entry in current_entries:
        date_part = entry.survey_id.split(' ')[0]
        current_dates.add(date_part)
    
    # Build activity map data
    forecast_map = []
    for monday in mondays:
        has_entry = monday in forecast_dates
        status = get_date_status(monday, 'forecast', has_entry)
        forecast_map.append({
            'date': monday,
            'status': status,
            'has_entry': has_entry,
            'label': datetime.strptime(monday, '%Y-%m-%d').strftime('%b %d')
        })
    
    actual_map = []
    for friday in fridays:
        has_entry = friday in current_dates
        status = get_date_status(friday, 'actual', has_entry)
        actual_map.append({
            'date': friday,
            'status': status,
            'has_entry': has_entry,
            'label': datetime.strptime(friday, '%Y-%m-%d').strftime('%b %d')
        })
    
    return jsonify({
        'forecasts': forecast_map,
        'actuals': actual_map,
        'next_monday': get_next_monday(),
        'last_friday': get_last_friday()
    })


@app.route('/api/outstanding_items')
def get_outstanding_items():
    """
    Get outstanding items for the Week Commencing dropdown.
    Returns:
    - Past Fridays that are Missing (no actual entry) - shown as week commencing (Monday)
    - The Next Monday (for forecast)
    Does NOT include completed weeks or expired forecasts.
    """
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    today = datetime.now().date()
    items = []
    
    # Get existing actual entries
    current_entries = CurrentEntry.query.filter_by(team_member=user_email).all()
    current_dates = set()
    for entry in current_entries:
        date_part = entry.survey_id.split(' ')[0]
        current_dates.add(date_part)
    
    # Check past Fridays (last 8 weeks) for missing actuals
    fridays = get_fridays_range(weeks_back=8, weeks_forward=0)
    for friday in fridays:
        friday_date = datetime.strptime(friday, '%Y-%m-%d').date()
        if friday_date <= today and friday not in current_dates:
            # Calculate week commencing (Monday of that week = Friday - 4 days)
            week_commencing_date = friday_date - timedelta(days=4)
            week_commencing = week_commencing_date.strftime('%Y-%m-%d')
            week_commencing_label = week_commencing_date.strftime('%b %d, %Y')
            
            items.append({
                'date': friday,  # Keep Friday for backend submission
                'week_commencing': week_commencing,  # Monday date for display
                'week_commencing_label': week_commencing_label,
                'type': 'actual',
                'label': f"Week commencing {week_commencing_label} - Missing Actuals",
                'status': 'missing',
                'priority': 1  # Higher priority for missing actuals
            })
    
    # Add next Monday for forecast
    next_monday = get_next_monday()
    next_monday_date = datetime.strptime(next_monday, '%Y-%m-%d').date()
    
    # Check if forecast already exists for next Monday
    forecast_entries = ForecastEntry.query.filter_by(team_member=user_email).all()
    forecast_dates = set()
    for entry in forecast_entries:
        date_part = entry.survey_id.split(' ')[0]
        forecast_dates.add(date_part)
    
    if next_monday not in forecast_dates:
        week_commencing_label = next_monday_date.strftime('%b %d, %Y')
        items.append({
            'date': next_monday,
            'week_commencing': next_monday,  # Monday is already week commencing for forecast
            'week_commencing_label': week_commencing_label,
            'type': 'forecast',
            'label': f"Week commencing {week_commencing_label} - Forecast",
            'status': 'open',
            'priority': 2
        })
    
    # Sort by priority (missing actuals first, then forecast)
    items.sort(key=lambda x: (x['priority'], x['date']))
    
    return jsonify(items)


@app.route('/api/get_entry')
def get_entry():
    """
    Get entries for a specific date and type (forecast or actual).
    Query params: date, type (forecast|actual)
    """
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    date = request.args.get('date')
    entry_type = request.args.get('type', 'forecast')
    
    if not date:
        return jsonify({'error': 'Date required'}), 400
    
    if entry_type == 'forecast':
        survey_id = f"{date} (Forecast)"
        entries = ForecastEntry.query.filter_by(
            team_member=user_email,
            survey_id=survey_id
        ).all()
    else:
        survey_id = f"{date} (Actual)"
        entries = CurrentEntry.query.filter_by(
            team_member=user_email,
            survey_id=survey_id
        ).all()
    
    result = [
        {
            'project': entry.assignment,
            'days': entry.days_allocated,
            'notes': entry.notes or ''
        }
        for entry in entries
    ]
    
    return jsonify({
        'entries': result,
        'exists': len(result) > 0,
        'date': date,
        'type': entry_type
    })


@app.route('/api/get_history')
def get_history():
    """Retrieve the most recent timesheet entries for the user (from either DB)."""
    user_email = get_user_email()
    
    # Try forecast first
    most_recent_forecast = db.session.query(ForecastEntry.survey_id, ForecastEntry.modified)\
        .filter_by(team_member=user_email)\
        .order_by(ForecastEntry.modified.desc())\
        .first()
    
    # Try current/actual
    most_recent_current = db.session.query(CurrentEntry.survey_id, CurrentEntry.modified)\
        .filter_by(team_member=user_email)\
        .order_by(CurrentEntry.modified.desc())\
        .first()
    
    # Determine which is more recent
    entries = []
    if most_recent_forecast and most_recent_current:
        if most_recent_forecast[1] > most_recent_current[1]:
            entries = ForecastEntry.query.filter_by(
                team_member=user_email,
                survey_id=most_recent_forecast[0]
            ).all()
        else:
            entries = CurrentEntry.query.filter_by(
                team_member=user_email,
                survey_id=most_recent_current[0]
            ).all()
    elif most_recent_forecast:
        entries = ForecastEntry.query.filter_by(
            team_member=user_email,
            survey_id=most_recent_forecast[0]
        ).all()
    elif most_recent_current:
        entries = CurrentEntry.query.filter_by(
            team_member=user_email,
            survey_id=most_recent_current[0]
        ).all()
    
    if not entries:
        return jsonify([])
    
    result = [
        {
            'project': entry.assignment,
            'days': entry.days_allocated,
            'notes': entry.notes or ''
        }
        for entry in entries
    ]
    
    return jsonify(result)


@app.route('/submit', methods=['POST'])
def submit():
    """Submit timesheet entries for a specific date (forecast or actual)."""
    user_email = get_user_email()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    selected_date = data.get('date')
    entry_type = data.get('type', 'forecast')  # 'forecast' or 'actual'
    rows = data.get('rows', [])
    
    if not selected_date:
        return jsonify({'error': 'Date is required'}), 400
    
    # Validate entry based on business rules
    today = datetime.now().date()
    date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
    
    if entry_type == 'forecast':
        # Forecasts: Cannot backfill past Mondays
        next_monday = datetime.strptime(get_next_monday(), '%Y-%m-%d').date()
        if date_obj < today and date_obj != next_monday:
            return jsonify({'error': 'Cannot submit forecast for expired week'}), 400
        
        survey_id = f"{selected_date} (Forecast)"
        Model = ForecastEntry
    else:
        # Actuals: Cannot fill future Fridays
        if date_obj > today:
            return jsonify({'error': 'Cannot submit actuals for future week'}), 400
        
        survey_id = f"{selected_date} (Actual)"
        Model = CurrentEntry
    
    try:
        # Delete existing entries for this user and survey_id
        Model.query.filter_by(
            team_member=user_email,
            survey_id=survey_id
        ).delete()
        
        # Add new entries
        for row in rows:
            project = row.get('project', '').strip()
            days = row.get('days')
            notes = row.get('notes', '').strip()
            
            if project and days is not None and days > 0:
                entry = Model(
                    survey_id=survey_id,
                    team_member=user_email,
                    assignment=project,
                    days_allocated=float(days),
                    notes=notes if notes else None
                )
                db.session.add(entry)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Timesheet submitted successfully for {survey_id}'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


def init_db():
    """Initialize the database tables for both binds."""
    with app.app_context():
        db.create_all()
        print("Databases initialized successfully (forecast + current).")


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
