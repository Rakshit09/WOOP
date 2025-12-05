"""
WOOP 2.0 Architecture - Forecast & Actuals Split
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from functools import lru_cache
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import pandas as pd
import requests
import urllib3
import urllib.parse 
import json
import os
import logging
from dotenv import load_dotenv
load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== MSSQL Connection for Team List & Projects ====================
# Hardcoded database configuration
MSSQL_SERVER = 'GREAZUK1DB036P'
MSSQL_PORT = 51018
MSSQL_DATABASE = 'EMEA_activity_tracker'

def get_mssql_engine():
    """Create MSSQL engine - SQL Auth for deployment, Windows Auth for local."""
    
    username = os.environ.get('MSSQL_USERNAME')
    password = os.environ.get('MSSQL_PASSWORD')
    use_sql_auth = os.environ.get('USE_SQL_AUTH', 'false').lower() == 'true'
    
    try:
        if use_sql_auth and username and password:
            # SQL Auth for Posit
            connection_string = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={MSSQL_SERVER},{MSSQL_PORT};"
                f"DATABASE={MSSQL_DATABASE};"
                f"UID={username};"
                f"PWD={password};"
            )
            logger.info("Attempting SQL Authentication...")
        else:
            # Windows Auth for local dev
            connection_string = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={MSSQL_SERVER},{MSSQL_PORT};"
                f"DATABASE={MSSQL_DATABASE};"
                f"Trusted_Connection=yes;"
            )
            logger.info("Attempting Windows Authentication...")
        
        params = urllib.parse.quote_plus(connection_string)
        engine = create_engine(
            f"mssql+pyodbc:///?odbc_connect={params}",
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        
        # test
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        logger.info("✓ MSSQL connection successful!")
        return engine
        
    except Exception as exc:
        logger.error(f"Failed to create MSSQL engine: {exc}")
        return None

# cache engine
_mssql_engine = None

def get_engine():
    """Get or create the cached MSSQL engine."""
    global _mssql_engine
    if _mssql_engine is None:
        _mssql_engine = get_mssql_engine()
    return _mssql_engine

# app config for deployment
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timesheet_forecast.db'  # Default/fallback
app.config['SQLALCHEMY_BINDS'] = {
    'forecast': 'sqlite:///timesheet_forecast.db',
    'current': 'sqlite:///timesheet_current.db'
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# database init
db = SQLAlchemy(app)


# database models
class ForecastEntry(db.Model):
    """Stores forecast timesheet entries"""
    __tablename__ = 'forecast_entry'
    __bind_key__ = 'forecast'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    survey_id = db.Column(db.String(50), nullable=False, index=True)  # "YYYY-MM-DD style forecast"
    team_member = db.Column(db.String(255), nullable=False, index=True)
    assignment = db.Column(db.String(255), nullable=False)
    days_allocated = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    modified = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<ForecastEntry {self.team_member} - {self.survey_id} - {self.assignment}>'


class CurrentEntry(db.Model):
    """Stores actual timesheet entries"""
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


# date utils
def get_next_monday():
    """Calculate the date of the upcoming Monday in YYYY-MM-DD style.
    If today is Monday, return today. else return next monday.
    """
    today = datetime.now().date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0 and today.weekday() != 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)
    return next_monday.strftime('%Y-%m-%d')


def get_last_friday():
    """ get the date of the most recent friday."""
    today = datetime.now().date()
    days_since_friday = (today.weekday() - 4) % 7
    if days_since_friday == 0 and today.weekday() != 4:
        days_since_friday = 7
    last_friday = today - timedelta(days=days_since_friday)
    return last_friday.strftime('%Y-%m-%d')


def get_mondays_range(weeks_back=8, weeks_forward=2):
    """Get list of mondays for the activity map (full year)."""
    today = datetime.now().date()
    year_start = datetime(today.year, 1, 1).date()
    
    # find first monday of the year
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
    """Get list of fridays for the activity map (full year)."""
    today = datetime.now().date()
    year_start = datetime(today.year, 1, 1).date()
    
    # find first friday of the year
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
    get the status of a cell in the activity map.
    
    Out: 'green', 'red', 'blue', 'gray'
    - Green: entry exists in DB
    - Red:missing actual (past Friday without entry)
    - Blue:next forecast (upcoming Monday for input)
    - Gray: expired forecast or future actual (disabled)
    """
    today = datetime.now().date()
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    if has_entry:
        return 'green'
    
    if entry_type == 'forecast':
        next_monday = datetime.strptime(get_next_monday(), '%Y-%m-%d').date()
        if date_obj == next_monday:
            return 'blue' 
        elif date_obj < today:
            return 'gray'
        else:
            return 'gray'
    
    else:  # actuals
       
        if date_obj > today:
            return 'gray' 
        else:
            return 'red'


# load projects from MSSQL database
def load_active_projects():
    """Load active projects from dbo.projects table in MSSQL."""
    engine = get_engine()
    
    if engine is None:
        logger.warning("MSSQL engine not available. Using empty project list.")
        return []
    
    try:
        query = """
            SELECT Title, [Sorting] 
            FROM dbo.projects 
            WHERE LOWER(Active) = 'true'
            ORDER BY [Sorting] ASC
        """
        df = pd.read_sql(query, engine)
        projects = df['Title'].tolist()
        return projects
    except Exception as e:
        logger.error(f"Error loading projects from database: {e}")
        return []


# user auth - will try X-Auth, Posit auth, otherwise fallback example name
@lru_cache(maxsize=100)
def lookup_email_by_username(username):
    """ user email from Posit Connect """
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
    """ user email from Posit Connect."""
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
            return request.args.get('user', 'thomas_kiessling@gallagherre.com')
        return None
    
    email = lookup_email_by_username(username)
    return email if email else username


def get_user_name(email):
    """Find user's name from dbo.EMEA_team_list table based on email."""
    engine = get_engine()
    
    if engine is None:
        return email  # Fallback
    
    try:
        query = text("SELECT Title FROM dbo.EMEA_team_list WHERE LOWER(Email) = LOWER(:email)")
        with engine.connect() as conn:
            result = conn.execute(query, {"email": email}).fetchone()
            if result:
                return result[0]
    except Exception as e:
        logger.error(f"Error looking up user name: {e}")
    
    return email  


def get_direct_reports(email):
    """Get direct reports for a user from dbo.EMEA_team_list table based on their email."""
    engine = get_engine()
    
    if engine is None:
        return []
    
    try:
        # First, get the Reports field for this user
        query = text("SELECT Reports FROM dbo.EMEA_team_list WHERE LOWER(Email) = LOWER(:email)")
        with engine.connect() as conn:
            result = conn.execute(query, {"email": email}).fetchone()
            
            if not result or not result[0]:
                return []
            
            reports_field = result[0]
            
            # Handle empty values
            if pd.isna(reports_field) or not str(reports_field).strip():
                return []
            
            # Split by comma and trim whitespace
            report_names = [name.strip() for name in str(reports_field).split(',') if name.strip()]
            
            if not report_names:
                return []
            
            # Look up each report's email by their Title (name)
            direct_reports = []
            for name in report_names:
                lookup_query = text("SELECT Title, Email FROM dbo.EMEA_team_list WHERE LOWER(Title) = LOWER(:name)")
                report_result = conn.execute(lookup_query, {"name": name}).fetchone()
                if report_result:
                    direct_reports.append({
                        'name': report_result[0],
                        'email': report_result[1]
                    })
            
            return direct_reports
        
    except Exception as e:
        logger.error(f"Error getting direct reports: {e}")
        return []


# main Routes
@app.route('/')
def index():
    """disp main timesheet interface with activity map."""
    user_email = get_user_email()
    
    if not user_email:
        return "Unable to identify user. Please ensure you are logged in.", 401 # should be 420 instead?
    
    user_name = get_user_name(user_email)
    default_date = get_next_monday()
    projects = load_active_projects()
    direct_reports = get_direct_reports(user_email)
    
    return render_template(
        'index.html',
        user_email=user_email,
        user_name=user_name,
        default_date=default_date,
        projects=projects,
        direct_reports=direct_reports
    )


@app.route('/templates/logo.png')
def serve_logo():
    """get logo image"""
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    return send_from_directory(templates_dir, 'logo.png')


@app.route('/api/activity_map')
def get_activity_map():
    """activity map data that returns status for each Monday and friday"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    mondays = get_mondays_range()
    fridays = get_fridays_range()
    
    # get all forecast entries
    forecast_entries = ForecastEntry.query.filter_by(team_member=user_email).all()
    forecast_dates = set()
    for entry in forecast_entries:
        # extract date "YYYY-MM-DD (Forecast)"
        date_part = entry.survey_id.split(' ')[0]
        forecast_dates.add(date_part)
    
    # get all actual entries
    current_entries = CurrentEntry.query.filter_by(team_member=user_email).all()
    current_dates = set()
    for entry in current_entries:
        date_part = entry.survey_id.split(' ')[0]
        current_dates.add(date_part)
    
    # build activity map data
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


@app.route('/api/team_activity_map')
def get_team_activity_map():
    """Get activity map data for a specific team member (for manager view)."""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    # Get the requested team member email from query params
    member_email = request.args.get('member_email')
    if not member_email:
        return jsonify({'error': 'Member email required'}), 400
    
    # Verify that the requesting user is the manager of this team member
    direct_reports = get_direct_reports(user_email)
    is_authorized = any(r['email'].lower() == member_email.lower() for r in direct_reports)
    
    if not is_authorized:
        return jsonify({'error': 'Unauthorized to view this team member'}), 403
    
    mondays = get_mondays_range()
    fridays = get_fridays_range()
    
    # Get all forecast entries for the team member
    forecast_entries = ForecastEntry.query.filter_by(team_member=member_email).all()
    forecast_dates = set()
    for entry in forecast_entries:
        date_part = entry.survey_id.split(' ')[0]
        forecast_dates.add(date_part)
    
    # Get all actual entries for the team member
    current_entries = CurrentEntry.query.filter_by(team_member=member_email).all()
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
        'member_email': member_email,
        'member_name': get_user_name(member_email)
    })


@app.route('/api/outstanding_items')
def get_outstanding_items():
    """
    get outstanding items.
    Out: Past Fridays that are Missing - shown as week commencing (Monday)
    2. The Next Monday (for forecast)
    note--does not include completed weeksand expired forecasts.
    """
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    today = datetime.now().date()
    items = []
    
    # Get existing entries
    current_entries = CurrentEntry.query.filter_by(team_member=user_email).all()
    current_dates = set()
    for entry in current_entries:
        date_part = entry.survey_id.split(' ')[0]
        current_dates.add(date_part)
    
    # Check past Fridaysfor missing actuals
    fridays = get_fridays_range(weeks_back=8, weeks_forward=0)
    for friday in fridays:
        friday_date = datetime.strptime(friday, '%Y-%m-%d').date()
        if friday_date <= today and friday not in current_dates:
            week_commencing_date = friday_date - timedelta(days=4)
            week_commencing = week_commencing_date.strftime('%Y-%m-%d')
            week_commencing_label = week_commencing_date.strftime('%b %d, %Y')
            
            items.append({
                'date': friday,  
                'week_commencing': week_commencing,  
                'week_commencing_label': week_commencing_label,
                'type': 'actual',
                'label': f"Week commencing {week_commencing_label} - Missing Actuals",
                'status': 'missing',
                'priority': 1  #  priority for missing actuals
            })
    
    # add next monday for forecast
    next_monday = get_next_monday()
    next_monday_date = datetime.strptime(next_monday, '%Y-%m-%d').date()
    
    # check if forecast already exists for next monday
    forecast_entries = ForecastEntry.query.filter_by(team_member=user_email).all()
    forecast_dates = set()
    for entry in forecast_entries:
        date_part = entry.survey_id.split(' ')[0]
        forecast_dates.add(date_part)
    
    if next_monday not in forecast_dates:
        week_commencing_label = next_monday_date.strftime('%b %d, %Y')
        items.append({
            'date': next_monday,
            'week_commencing': next_monday, 
            'week_commencing_label': week_commencing_label,
            'type': 'forecast',
            'label': f"Week commencing {week_commencing_label} - Forecast",
            'status': 'open',
            'priority': 2
        })
    
    #  priority sort(missing actuals first, then forecast)
    items.sort(key=lambda x: (x['priority'], x['date']))
    
    return jsonify(items)


@app.route('/api/get_entry')
def get_entry():
    """Get entries for a specific date and type. params: date, type (forecast|actual)
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
    """get the most recent timesheet entries for the user (from either DB)."""
    user_email = get_user_email()
    
    # try forecast first
    most_recent_forecast = db.session.query(ForecastEntry.survey_id, ForecastEntry.modified)\
        .filter_by(team_member=user_email)\
        .order_by(ForecastEntry.modified.desc())\
        .first()
    
    # try current/actual
    most_recent_current = db.session.query(CurrentEntry.survey_id, CurrentEntry.modified)\
        .filter_by(team_member=user_email)\
        .order_by(CurrentEntry.modified.desc())\
        .first()
    
    # find which is more recent
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
    """Submit entries for date."""
    user_email = get_user_email()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    selected_date = data.get('date')
    entry_type = data.get('type', 'forecast')  # 'forecast' or 'actual'
    rows = data.get('rows', [])
    
    if not selected_date:
        return jsonify({'error': 'Date is required'}), 400
    
    # Validate entry 
    today = datetime.now().date()
    date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
    
    if entry_type == 'forecast':
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
        # Delete existing entries 
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
    """  database init tables for both binds"""
    with app.app_context():
        db.create_all()
        print("Databases initialized successfully (forecast + current).")


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
