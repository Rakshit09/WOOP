"""
WOOP 2.0 Architecture - Forecast & Actuals Split
"""

from flask import Flask, render_template, request, jsonify
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

app = Flask(__name__, static_url_path='/static', static_folder='static')

# Debug endpoint to check app health
@app.route('/api/health')
def health_check():
    """Debug endpoint to check app status."""
    import sys
    return jsonify({
        'status': 'ok',
        'python_version': sys.version,
        'debug_mode': app.debug,
        'static_folder': app.static_folder,
        'static_url_path': app.static_url_path,
        'env_vars': {
            'CONNECT_SERVER': bool(os.environ.get('CONNECT_SERVER')),
            'CONNECT_API_KEY': bool(os.environ.get('CONNECT_API_KEY')),
            'MSSQL_USERNAME': bool(os.environ.get('MSSQL_USERNAME')),
            'MSSQL_PASSWORD': bool(os.environ.get('MSSQL_PASSWORD')),
            'FLASK_DEBUG': os.environ.get('FLASK_DEBUG'),
        }
    })

# database config
MSSQL_SERVER = 'GREAZUK1DB036P'
MSSQL_PORT = 51018
MSSQL_DATABASE = 'EMEA_activity_tracker'
MSSQL_DOMAIN = 'emea'  

# Cache the engine
_mssql_engine = None


def get_mssql_engine():
    """ MSSQL engine using pymssql - will work on Windows and Posit"""
    
    # Get credentials from environment variables
    username = os.environ.get('MSSQL_USERNAME')
    password = os.environ.get('MSSQL_PASSWORD')
    
    if not username or not password:
        logger.error("MSSQL_USERNAME and MSSQL_PASSWORD environment variables are required")
        logger.error("Set these in your .env file (local) or Posit Connect environment variables")
        return None
    
    # Format: domain\\username
    if MSSQL_DOMAIN and '\\' not in username:
        full_username = f"{MSSQL_DOMAIN}\\{username}"
    else:
        full_username = username
    
    logger.info(f"Attempting connection to {MSSQL_SERVER}:{MSSQL_PORT}/{MSSQL_DATABASE}")
    logger.info(f"Username format: {MSSQL_DOMAIN}\\****")
    
    try:
        # use URL.create()
        connection_url = URL.create(
            "mssql+pymssql",
            username=full_username,
            password=password,
            host=MSSQL_SERVER,
            port=MSSQL_PORT,
            database=MSSQL_DATABASE,
            query={"timeout": "30"}
        )
        
        engine = create_engine(
            connection_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30,
        )
        
        # Test the connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        logger.info("✓ MSSQL connection successful!")
        return engine
        
    except Exception as exc:
        logger.error(f"Failed to create MSSQL engine: {exc}")
        return None


def get_engine():
    """Get or create the cached MSSQL engine."""
    global _mssql_engine
    if _mssql_engine is None:
        _mssql_engine = get_mssql_engine()
    return _mssql_engine


# App config for deployment (ensure writable path on Posit Connect)
DATA_DIR = os.environ.get('CONNECT_DATA_DIR') or app.instance_path
os.makedirs(DATA_DIR, exist_ok=True)
forecast_db = os.path.join(DATA_DIR, 'timesheet_forecast.db')
current_db = os.path.join(DATA_DIR, 'timesheet_current.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{forecast_db}'
app.config['SQLALCHEMY_BINDS'] = {
    'forecast': f'sqlite:///{forecast_db}',
    'current': f'sqlite:///{current_db}'
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database init
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


class Nudge(db.Model):
    """Stores nudge messages from managers to team members"""
    __tablename__ = 'nudge'
    __bind_key__ = 'current'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    from_email = db.Column(db.String(255), nullable=False, index=True)
    from_name = db.Column(db.String(255), nullable=False)
    to_email = db.Column(db.String(255), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    dismissed = db.Column(db.Boolean, default=False, nullable=False)
    
    def __repr__(self):
        return f'<Nudge from {self.from_name} to {self.to_email}>'


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
    """ user email from Posit """
    username = None
    
    credentials_header = request.headers.get('Rstudio-Connect-Credentials')
    logger.info(f"Credentials header present: {bool(credentials_header)}")
    
    if credentials_header:
        try:
            credentials = json.loads(credentials_header)
            username = credentials.get('user')
            logger.info(f"Parsed username from credentials: {username}")
        except json.JSONDecodeError:
            logger.error(f"Failed to parse credentials: {credentials_header}")
    
    if not username:
        if os.environ.get('FLASK_DEBUG') or app.debug:
            fallback = request.args.get('user', 'holger_cammerer@gallagherre.com')
            #fallback = request.args.get('user', 'rakshit_joshi@gallagherre.com')
            logger.info(f"Debug mode - using fallback user: {fallback}")
            return fallback
        logger.warning("No username found")
        return None
    
    email = lookup_email_by_username(username)
    result = email if email else username
    logger.info(f"Final user email: {result}")
    return result


def get_user_name(email):
    """get user name from EMEA_team_list from on email."""
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
    logger.info("=== Index page request ===")
    logger.info(f"Request headers: {dict(request.headers)}")
    
    user_email = get_user_email()
    
    if not user_email:
        logger.error("No user email found - returning 401")
        return "Unable to identify user. Please ensure you are logged in.", 401 # should be 420 instead?
    
    logger.info(f"User authenticated: {user_email}")
    
    user_name = get_user_name(user_email)
    default_date = get_next_monday()
    projects = load_active_projects()
    direct_reports = get_direct_reports(user_email)
    
    logger.info(f"Rendering page for {user_name} with {len(projects)} projects")
    
    return render_template(
        'index.html',
        user_email=user_email,
        user_name=user_name,
        default_date=default_date,
        projects=projects,
        direct_reports=direct_reports
    )


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
    logger.info(f"Outstanding items request - user_email: {user_email}")
    if not user_email:
        logger.warning("Outstanding items: User not authenticated")
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
    
    logger.info(f"Outstanding items: returning {len(items)} items")
    if items:
        logger.info(f"First item: {items[0]}")
    
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


@app.route('/api/send_nudge', methods=['POST'])
def send_nudge():
    """Send a nudge to a team member."""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    data = request.get_json()
    if not data or not data.get('to_email'):
        return jsonify({'error': 'Recipient email required'}), 400
    
    to_email = data['to_email']
    
    # Verify the sender is the manager of this team member
    direct_reports = get_direct_reports(user_email)
    is_authorized = any(r['email'].lower() == to_email.lower() for r in direct_reports)
    
    if not is_authorized:
        return jsonify({'error': 'Unauthorized to nudge this team member'}), 403
    
    # Get sender's name
    sender_name = get_user_name(user_email)
    
    # Fun nudge messages
    import random
    nudge_messages = [
        "Hey there! Your timesheet is looking a bit lonely... 🥺",
        "Knock knock! Who's there? Your empty timesheet! 🚪",
        "Your manager sent a gentle reminder... just kidding, FILL YOUR TIMESHEET! 😤",
        "The timesheet fairy visited, but left empty-handed. Don't make her sad! 🧚",
        "Alert: Your timesheet has been spotted in the wild... completely blank! 🔍",
        "Fun fact: Timesheets don't fill themselves. We checked. Twice. 📊",
        "Your timesheet misses you. It told us. Awkward. 💔",
        "Breaking news: Local timesheet remains unfilled. More at 11. 📰",
    ]
    
    message = random.choice(nudge_messages)
    
    try:
        nudge = Nudge(
            from_email=user_email.lower(),
            from_name=sender_name,
            to_email=to_email.lower(),  # Store lowercase for consistent matching
            message=message
        )
        db.session.add(nudge)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Nudge sent successfully!'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get_nudges')
def get_nudges():
    """Get pending nudges for the current user."""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    # Use lowercase for case-insensitive matching
    nudges = Nudge.query.filter_by(
        to_email=user_email.lower(),
        dismissed=False
    ).order_by(Nudge.created.desc()).all()
    
    result = [
        {
            'id': nudge.id,
            'from_name': nudge.from_name,
            'message': nudge.message,
            'created': nudge.created.strftime('%b %d at %H:%M')
        }
        for nudge in nudges
    ]
    
    return jsonify(result)


@app.route('/api/dismiss_nudge', methods=['POST'])
def dismiss_nudge():
    """Dismiss a nudge."""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    data = request.get_json()
    nudge_id = data.get('nudge_id')
    
    if not nudge_id:
        return jsonify({'error': 'Nudge ID required'}), 400
    
    nudge = Nudge.query.filter_by(id=nudge_id, to_email=user_email.lower()).first()
    
    if not nudge:
        return jsonify({'error': 'Nudge not found'}), 404
    
    nudge.dismissed = True
    db.session.commit()
    
    return jsonify({'success': True})


def calculate_member_score(member_email):
    """
    Calculate gamification score for a team member.
    
    Scoring system (based on last 8 weeks):
    - On-time Monday forecast (within 1 day grace): +10 points
    - Late Monday forecast: +5 points
    - On-time Friday actual (within 1 day grace): +10 points
    - Late Friday actual: +5 points  
    - Missing submission: 0 points
    - Nudge received: -2 points per nudge
    
    Returns score as percentage (0-100)
    """
    today = datetime.now().date()
    
    # Get last 8 Mondays for forecast scoring
    recent_mondays = []
    current_monday = today
    # Find the most recent Monday
    while current_monday.weekday() != 0:  # 0 = Monday
        current_monday -= timedelta(days=1)
    
    for i in range(8):
        monday = current_monday - timedelta(weeks=i)
        if monday <= today:  # Only past/current Mondays
            recent_mondays.append(monday.strftime('%Y-%m-%d'))
    
    # Get last 8 Fridays for actual scoring
    recent_fridays = []
    current_friday = today
    # Find the most recent Friday
    while current_friday.weekday() != 4:  # 4 = Friday
        current_friday -= timedelta(days=1)
    
    for i in range(8):
        friday = current_friday - timedelta(weeks=i)
        if friday <= today:  # Only past Fridays
            recent_fridays.append(friday.strftime('%Y-%m-%d'))
    
    logger.info(f"Scoring based on Mondays: {recent_mondays} and Fridays: {recent_fridays}")
    
    # Get all forecast entries for this member (case-insensitive)
    all_forecast_entries = ForecastEntry.query.all()
    forecast_entries = [e for e in all_forecast_entries if e.team_member.lower() == member_email.lower()]
    
    # Get all actual entries for this member (case-insensitive)
    all_actual_entries = CurrentEntry.query.all()
    actual_entries = [e for e in all_actual_entries if e.team_member.lower() == member_email.lower()]
    
    logger.info(f"Score calc for {member_email}: found {len(forecast_entries)} forecast, {len(actual_entries)} actual entries")
    
    # Build dict of forecast submission dates and modified times
    forecast_submissions = {}
    for entry in forecast_entries:
        date_part = entry.survey_id.split(' ')[0]
        if date_part not in forecast_submissions or entry.modified > forecast_submissions[date_part]:
            forecast_submissions[date_part] = entry.modified
    
    # Build dict of actual submission dates and modified times
    actual_submissions = {}
    for entry in actual_entries:
        date_part = entry.survey_id.split(' ')[0]
        if date_part not in actual_submissions or entry.modified > actual_submissions[date_part]:
            actual_submissions[date_part] = entry.modified
    
    logger.info(f"Forecast submissions found: {list(forecast_submissions.keys())}")
    logger.info(f"Actual submissions found: {list(actual_submissions.keys())}")
    
    # Calculate points
    total_points = 0
    max_points = 0
    
    # Score Monday forecasts (1 day grace period)
    for monday in recent_mondays:
        monday_date = datetime.strptime(monday, '%Y-%m-%d').date()
        
        max_points += 10  # Maximum possible for this forecast
        
        if monday in forecast_submissions:
            modified_date = forecast_submissions[monday].date() if hasattr(forecast_submissions[monday], 'date') else forecast_submissions[monday]
            
            # Grace period: 1 day after the Monday (submit by Tuesday end)
            grace_deadline = monday_date + timedelta(days=1)
            
            if modified_date <= grace_deadline:
                total_points += 10  # On-time
            else:
                total_points += 5   # Late
        # else: 0 points for missing
    
    # Score Friday actuals (1 day grace period)
    for friday in recent_fridays:
        friday_date = datetime.strptime(friday, '%Y-%m-%d').date()
        
        max_points += 10  # Maximum possible for this actual
        
        if friday in actual_submissions:
            modified_date = actual_submissions[friday].date() if hasattr(actual_submissions[friday], 'date') else actual_submissions[friday]
            
            # Grace period: 1 day after the Friday (submit by Saturday end)
            grace_deadline = friday_date + timedelta(days=1)
            
            if modified_date <= grace_deadline:
                total_points += 10  # On-time
            else:
                total_points += 5   # Late/backfill
        # else: 0 points for missing
    
    # Deduct points for nudges received (only count recent nudges - last 8 weeks)
    eight_weeks_ago = today - timedelta(weeks=8)
    nudge_count = Nudge.query.filter(
        Nudge.to_email == member_email.lower(),
        Nudge.created >= eight_weeks_ago
    ).count()
    nudge_penalty = nudge_count * 2
    total_points = max(0, total_points - nudge_penalty)
    
    logger.info(f"Score calc: total_points={total_points}, max_points={max_points}, nudges={nudge_count}")
    
    # Calculate percentage score
    if max_points == 0:
        return {'score': 100, 'nudges': nudge_count, 'weeks_completed': 0, 'weeks_total': 0}
    
    forecasts_completed = len([m for m in recent_mondays if m in forecast_submissions])
    actuals_completed = len([f for f in recent_fridays if f in actual_submissions])
    weeks_completed = forecasts_completed + actuals_completed
    weeks_total = len(recent_mondays) + len(recent_fridays)
    
    score = min(100, max(0, round((total_points / max_points) * 100)))
    
    logger.info(f"Final score for {member_email}: {score}% ({weeks_completed}/{weeks_total} submissions)")
    
    return {
        'score': score,
        'nudges': nudge_count,
        'weeks_completed': weeks_completed,
        'weeks_total': weeks_total
    }


def get_manager_name(manager_email):
    """Get the first name of a manager for team naming."""
    full_name = get_user_name(manager_email)
    if full_name and full_name != manager_email:
        # Extract first name
        return full_name.split()[0]
    return "Team"


@app.route('/api/debug_score')
def debug_score():
    """Debug endpoint to check score calculation."""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    member_email = request.args.get('email', user_email)
    
    # Get all entries in the database
    all_current = CurrentEntry.query.all()
    all_forecast = ForecastEntry.query.all()
    
    # Find entries for this member
    member_current = [e for e in all_current if e.team_member.lower() == member_email.lower()]
    
    # Get recent Fridays
    today = datetime.now().date()
    current_friday = today
    while current_friday.weekday() != 4:
        current_friday -= timedelta(days=1)
    
    recent_fridays = []
    for i in range(8):
        friday = current_friday - timedelta(weeks=i)
        if friday <= today:
            recent_fridays.append(friday.strftime('%Y-%m-%d'))
    
    # Build submissions dict
    submissions = {}
    for entry in member_current:
        date_part = entry.survey_id.split(' ')[0]
        submissions[date_part] = {
            'survey_id': entry.survey_id,
            'modified': entry.modified.isoformat() if entry.modified else None
        }
    
    # Get all unique team_members in database
    unique_members = list(set(e.team_member for e in all_current))
    
    return jsonify({
        'requested_email': member_email,
        'total_current_entries': len(all_current),
        'total_forecast_entries': len(all_forecast),
        'member_entries_found': len(member_current),
        'unique_team_members_in_db': unique_members,
        'recent_fridays': recent_fridays,
        'submissions_for_member': submissions,
        'score_data': calculate_member_score(member_email)
    })


@app.route('/api/team_scores')
def get_team_scores():
    """
    Get team scores for all teams visible to the current user.
    Teams are named after their line manager (e.g., "Team Thomas").
    """
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    engine = get_engine()
    if engine is None:
        return jsonify({'error': 'Database not available'}), 500
    
    try:
        with engine.connect() as conn:
            # Get all managers with reports
            managers_query = text("""
                SELECT DISTINCT Title, Email, Reports 
                FROM dbo.EMEA_team_list 
                WHERE Reports IS NOT NULL AND Reports != ''
            """)
            managers = conn.execute(managers_query).fetchall()
            
            # Get ALL team members in one query (avoid N+1)
            all_members_query = text("""
                SELECT LOWER(Title) as name_lower, Email 
                FROM dbo.EMEA_team_list
            """)
            all_members = {row[0]: row[1] for row in conn.execute(all_members_query).fetchall()}
        
        teams = []
        
        for row in managers:
            manager_name = row[0]
            manager_email = row[1]
            reports_field = row[2]
            
            if not reports_field or pd.isna(reports_field):
                continue
            
            # Get first name for team name
            first_name = manager_name.split()[0] if manager_name else "Unknown"
            team_name = f"Team {first_name}"
            
            # Get direct reports
            report_names = [name.strip().lower() for name in str(reports_field).split(',') if name.strip()]
            
            if not report_names:
                continue
            
            # Calculate scores using pre-fetched member emails
            member_scores = []
            for name in report_names:
                member_email = all_members.get(name)
                if member_email:
                    score_data = calculate_member_score(member_email)
                    member_scores.append(score_data['score'])
            
            if member_scores:
                avg_score = round(sum(member_scores) / len(member_scores))
                teams.append({
                    'team_name': team_name,
                    'manager_email': manager_email,
                    'score': avg_score,
                    'member_count': len(member_scores)
                })
        
        # Sort by score descending and add rank
        teams.sort(key=lambda x: x['score'], reverse=True)
        for i, team in enumerate(teams):
            team['rank'] = i + 1
        
        return jsonify({
            'teams': teams,
            'user_email': user_email
        })
        
    except Exception as e:
        logger.error(f"Error calculating team scores: {e}")
        return jsonify({'error': str(e)}), 500


def init_db():
    """  database init tables for both binds"""
    with app.app_context():
        db.create_all()
        print("Databases initialized successfully (forecast + current).")


# Ensure tables exist once (Flask 3 removed before_first_request)
_db_initialized = False

def ensure_db_initialized():
    global _db_initialized
    if _db_initialized:
        return
    with app.app_context():
        db.create_all()
    _db_initialized = True

# init db 
ensure_db_initialized()

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
