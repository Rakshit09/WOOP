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
import json
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_url_path='/static', static_folder='static')

# config

MSSQL_SERVER = 'GREAZUK1DB036P'
MSSQL_PORT = 51018
MSSQL_DATABASE = 'EMEA_activity_tracker'
MSSQL_DOMAIN = 'emea'

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

db = SQLAlchemy(app)

# database models

class ForecastEntry(db.Model):
    __tablename__ = 'forecast_entry'
    __bind_key__ = 'forecast'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    activity_week = db.Column(db.String(50), nullable=False, index=True)
    colleague = db.Column(db.String(255), nullable=False, index=True)
    assignment_ID = db.Column(db.String(255), nullable=False)
    allocation_days = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    record_created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CurrentEntry(db.Model):
    __tablename__ = 'current_entry'
    __bind_key__ = 'current'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    activity_week = db.Column(db.String(50), nullable=False, index=True)
    colleague = db.Column(db.String(255), nullable=False, index=True)
    assignment_ID = db.Column(db.String(255), nullable=False)
    allocation_days = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    record_created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Nudge(db.Model):
    __tablename__ = 'nudge'
    __bind_key__ = 'current'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    from_email = db.Column(db.String(255), nullable=False, index=True)
    from_name = db.Column(db.String(255), nullable=False)
    to_email = db.Column(db.String(255), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    dismissed = db.Column(db.Boolean, default=False, nullable=False)


# database connection

_mssql_engine = None


def get_engine():
    """get/create cached MSSQL engine"""
    global _mssql_engine
    if _mssql_engine is not None:
        return _mssql_engine
    
    username = os.environ.get('MSSQL_USERNAME')
    password = os.environ.get('MSSQL_PASSWORD')
    
    if not username or not password:
        logger.error("MSSQL_USERNAME and MSSQL_PASSWORD environment variables required")
        return None
    
    full_username = f"{MSSQL_DOMAIN}\\{username}" if MSSQL_DOMAIN and '\\' not in username else username
    
    try:
        connection_url = URL.create(
            "mssql+pymssql",
            username=full_username,
            password=password,
            host=MSSQL_SERVER,
            port=MSSQL_PORT,
            database=MSSQL_DATABASE,
            query={"timeout": "30"}
        )
        
        _mssql_engine = create_engine(
            connection_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30,
        )
        
        with _mssql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        logger.info("MSSQL connection successful")
        return _mssql_engine
        
    except Exception as e:
        logger.error(f"Failed to create MSSQL engine: {e}")
        return None


# date utilities

def get_weekday_date(target_weekday, direction='next'):
    """
    get date for a specific weekday
    target_weekday: 0=Monday, 4=Friday
    direction: 'next' for upcoming, 'last' for most recent
    """
    today = datetime.now().date()
    days_diff = (target_weekday - today.weekday()) % 7
    
    if direction == 'next':
        if days_diff == 0 and today.weekday() == target_weekday:
            return today.strftime('%Y-%m-%d')
        return (today + timedelta(days=days_diff or 7)).strftime('%Y-%m-%d')
    else:  # last
        days_diff = (today.weekday() - target_weekday) % 7
        if days_diff == 0 and today.weekday() != target_weekday:
            days_diff = 7
        return (today - timedelta(days=days_diff)).strftime('%Y-%m-%d')


def get_next_monday():
    return get_weekday_date(0, 'next')


def get_last_friday():
    return get_weekday_date(4, 'last')


def get_weekdays_for_year(target_weekday):
    """get all dates for a specific weekday in current year"""
    today = datetime.now().date()
    year_start = datetime(today.year, 1, 1).date()
    year_end = datetime(today.year, 12, 31).date()
    
    days_until_target = (target_weekday - year_start.weekday()) % 7
    first_target = year_start + timedelta(days=days_until_target)
    
    dates = []
    current = first_target
    while current <= year_end:
        dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(weeks=1)
    
    return dates


def get_mondays_range(weeks_back=8, weeks_forward=2):
    return get_weekdays_for_year(0)  # Monday


def get_fridays_range(weeks_back=8, weeks_forward=2):
    return get_weekdays_for_year(4)  # Friday


def extract_date_string(activity_week):
    """extract YYYY-MM-DD from any type date formats"""
    if activity_week is None:
        return None
    
    if hasattr(activity_week, 'strftime'):
        return activity_week.strftime('%Y-%m-%d')
    
    date_str = str(activity_week)
    
    if 'T' in date_str:
        return date_str.split('T')[0]
    elif ' ' in date_str:
        return date_str.split(' ')[0]
    
    return date_str


def get_date_status(date_str, entry_type, has_entry):
    """Get status for activity map cell: 'green', 'red', 'blue', or 'gray'."""
    if has_entry:
        return 'green'
    
    today = datetime.now().date()
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    if entry_type == 'forecast':
        next_monday = datetime.strptime(get_next_monday(), '%Y-%m-%d').date()
        return 'blue' if date_obj == next_monday else 'gray'
    else:
        return 'gray' if date_obj > today else 'red'


# mssql data access

def get_activity_entries(table_name, alias, colleague=None, activity_week=None):
    """reader for activity_forecast and activity_actual tables"""
    engine = get_engine()
    if engine is None:
        return []

    try:
        conditions = []
        params = {}

        if colleague:
            colleague_name = get_colleague_name_from_email(colleague)
            conditions.append(
                f"(LOWER({alias}.colleague) = LOWER(:colleague_email) "
                f"OR LOWER({alias}.colleague) = LOWER(:colleague_name))"
            )
            params['colleague_email'] = colleague
            params['colleague_name'] = colleague_name

        if activity_week:
            conditions.append(f"CAST({alias}.activity_week AS DATE) = CAST(:activity_week AS DATE)")
            params['activity_week'] = (
                datetime.strptime(activity_week, '%Y-%m-%d') 
                if isinstance(activity_week, str) else activity_week
            )

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = text(f"""
            SELECT CAST({alias}.activity_week AS DATE) as activity_week,
                   {alias}.colleague,
                   COALESCE(p.Title, CAST({alias}.assignment_ID AS VARCHAR)) as assignment_ID,
                   {alias}.allocation_days, {alias}.notes, {alias}.record_created
            FROM {table_name} {alias}
            LEFT JOIN dbo.projects p ON TRY_CAST(CAST({alias}.assignment_ID AS FLOAT) AS INT) = p.ProjectID
            WHERE {where_clause}
        """)

        with engine.connect() as conn:
            result = conn.execute(query, params).fetchall()
            return [
                {
                    'activity_week': r[0],
                    'colleague': r[1],
                    'assignment_ID': r[2],
                    'allocation_days': r[3],
                    'notes': r[4],
                    'record_created': r[5],
                }
                for r in result
            ]
    except Exception as e:
        logger.error(f"Error reading from {table_name}: {e}")
        return []


def get_forecast_entries_mssql(colleague=None, activity_week=None):
    return get_activity_entries("dbo.activity_forecast", "f", colleague, activity_week)


def get_current_entries_mssql(colleague=None, activity_week=None):
    return get_activity_entries("dbo.activity_actual", "a", colleague, activity_week)


def save_activity_entries(table_name, colleague_email, activity_week, rows):
    """writer for activity_forecast and activity_actual tables"""
    engine = get_engine()
    if engine is None:
        raise Exception("MSSQL engine not available")

    activity_week_str = (
        activity_week if isinstance(activity_week, str) 
        else activity_week.strftime('%Y-%m-%d')
    )

    project_id_map = get_project_id_mapping()
    colleague_name = get_colleague_name_from_email(colleague_email)

    try:
        with engine.begin() as conn:
            for row in rows:
                project = row.get('project', '').strip()
                days = row.get('days')
                notes = row.get('notes', '').strip()

                if not project or days is None or days <= 0:
                    continue
                    
                project_id = project_id_map.get(project)
                if project_id is None:
                    logger.warning(f"Project '{project}' not found, skipping")
                    continue

                conn.execute(
                    text(f"""
                        INSERT INTO {table_name} 
                        (activity_week, colleague, assignment_ID, allocation_days, notes, record_created)
                        VALUES (CONVERT(date, :activity_week), :colleague, :assignment_ID, 
                                :allocation_days, :notes, :record_created)
                    """),
                    {
                        'activity_week': activity_week_str,
                        'colleague': colleague_name,
                        'assignment_ID': float(project_id),
                        'allocation_days': float(days),
                        'notes': notes or None,
                        'record_created': datetime.utcnow(),
                    },
                )
        return True
    except Exception as e:
        logger.error(f"Error saving to {table_name}: {e}")
        raise


def save_forecast_entries_mssql(colleague_email, activity_week, rows):
    return save_activity_entries("dbo.activity_forecast", colleague_email, activity_week, rows)


def save_current_entries_mssql(colleague_email, activity_week, rows):
    return save_activity_entries("dbo.activity_actual", colleague_email, activity_week, rows)


def get_most_recent_entry_mssql(colleague):
    """fetchmost recent entry (forecast / actual)"""
    engine = get_engine()
    if engine is None:
        return None, None
    
    try:
        colleague_name = get_colleague_name_from_email(colleague)
        with engine.connect() as conn:
            results = {}
            for entry_type, table in [('forecast', 'activity_forecast'), ('actual', 'activity_actual')]:
                result = conn.execute(
                    text(f"""
                        SELECT TOP 1 CAST(activity_week AS DATE), record_created
                        FROM dbo.{table} 
                        WHERE (LOWER(colleague) = LOWER(:colleague_email) OR LOWER(colleague) = LOWER(:colleague_name))
                        ORDER BY record_created DESC
                    """),
                    {'colleague_email': colleague, 'colleague_name': colleague_name}
                ).fetchone()
                if result:
                    results[entry_type] = result
            
            if not results:
                return None, None
            
            if len(results) == 2:
                # results: {entry_type: (activity_week, record_created)}
                entry_type, data = max(results.items(), key=lambda x: x[1][1])
                return entry_type, data[0]
            
            entry_type, data = next(iter(results.items()))
            return entry_type, data[0]
            
    except Exception as e:
        logger.error(f"Error getting most recent entry: {e}")
        return None, None


# project & team data

def load_active_projects():
    """load projects from database"""
    engine = get_engine()
    if engine is None:
        return []
    
    try:
        df = pd.read_sql("SELECT Title FROM dbo.projects ORDER BY [Sorting] ASC", engine)
        return df['Title'].tolist()
    except Exception as e:
        logger.error(f"Error loading projects: {e}")
        return []


def get_project_id_mapping():
    """get mapping of project Title -> ProjectID"""
    engine = get_engine()
    if engine is None:
        return {}
    
    try:
        df = pd.read_sql("SELECT ProjectID, Title FROM dbo.projects", engine)
        return dict(zip(df['Title'], df['ProjectID']))
    except Exception as e:
        logger.error(f"Error loading project mapping: {e}")
        return {}


def get_colleague_name_from_email(email):
    """find name from email"""
    engine = get_engine()
    if engine is None:
        return email

    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT Title FROM dbo.EMEA_team_list WHERE LOWER(Email) = LOWER(:email)"),
                {"email": email}
            ).fetchone()
            return result[0] if result and result[0] else email
    except Exception as e:
        logger.error(f"Error looking up colleague name: {e}")
        return email


def get_direct_reports(email):
    """get direct reports for managers"""
    engine = get_engine()
    if engine is None:
        return []
    
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT Reports FROM dbo.EMEA_team_list WHERE LOWER(Email) = LOWER(:email)"),
                {"email": email}
            ).fetchone()
            
            if not result or not result[0] or pd.isna(result[0]):
                return []
            
            report_names = [name.strip() for name in str(result[0]).split(',') if name.strip()]
            
            direct_reports = []
            for name in report_names:
                report = conn.execute(
                    text("SELECT Title, Email FROM dbo.EMEA_team_list WHERE LOWER(Title) = LOWER(:name)"),
                    {"name": name}
                ).fetchone()
                if report:
                    direct_reports.append({'name': report[0], 'email': report[1]})
            
            return direct_reports
    except Exception as e:
        logger.error(f"Error getting direct reports: {e}")
        return []


def get_all_team_members():
    """get all team members with emails"""
    engine = get_engine()
    if engine is None:
        return []
    
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT Title, Email FROM dbo.EMEA_team_list WHERE Email IS NOT NULL")
            ).fetchall()
            return [{'name': row[0], 'email': row[1]} for row in result]
    except Exception as e:
        logger.error(f"Error getting team members: {e}")
        return []


# user auth

@lru_cache(maxsize=100)
def lookup_email_by_username(username):
    """lookup user email from Posit Connect"""
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
            for user in response.json().get('results', []):
                if user.get('username') == username:
                    return user.get('email')
    except Exception as e:
        logger.error(f"Error looking up user email: {e}")
    
    return None


def get_user_email():
    """get current user email from Posit headers"""
    username = None
    
    credentials_header = request.headers.get('Rstudio-Connect-Credentials')
    
    if credentials_header:
        try:
            credentials = json.loads(credentials_header)
            username = credentials.get('user')
        except json.JSONDecodeError:
            logger.error(f"Failed to parse credentials")
    
    if not username:
        if os.environ.get('FLASK_DEBUG') or app.debug:
            #return request.args.get('user', 'holger_cammerer@gallagherre.com')
            return 'rakshit_joshi@gallagherre.com'
        return None
    
    return lookup_email_by_username(username) or username


def get_user_name(email):
    return get_colleague_name_from_email(email)


# email utilities

def send_reminder_email(to_email, subject, body, send=False):
    """send a reminder email using SMTP"""
    if not send:
        logger.info(f"Email disabled. Would send to {to_email}: {subject}")
        return True
    
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.office365.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    
    if not smtp_user or not smtp_password:
        logger.warning(f"SMTP not configured. Would send to {to_email}")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = os.environ.get('SMTP_FROM', smtp_user)
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


# api routes

@app.route('/api/health')
def health_check():
    """debug endpoint to check app status"""
    import sys
    return jsonify({
        'status': 'ok',
        'python_version': sys.version,
        'debug_mode': app.debug,
        'env_vars': {
            'CONNECT_SERVER': bool(os.environ.get('CONNECT_SERVER')),
            'CONNECT_API_KEY': bool(os.environ.get('CONNECT_API_KEY')),
            'MSSQL_USERNAME': bool(os.environ.get('MSSQL_USERNAME')),
            'MSSQL_PASSWORD': bool(os.environ.get('MSSQL_PASSWORD')),
        }
    })


@app.route('/')
def index():
    """main timesheet interface"""
    user_email = get_user_email()
    
    if not user_email:
        return "Unable to identify user. Please ensure you are logged in.", 401
    
    return render_template(
        'index.html',
        user_email=user_email,
        user_name=get_user_name(user_email),
        default_date=get_next_monday(),
        projects=load_active_projects(),
        direct_reports=get_direct_reports(user_email)
    )


@app.route('/api/activity_map')
def get_activity_map():
    """activity map data with status for each Monday and Friday"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    def build_date_set(entries):
        return {extract_date_string(e['activity_week']) for e in entries if extract_date_string(e['activity_week'])}
    
    forecast_dates = build_date_set(get_forecast_entries_mssql(colleague=user_email))
    current_dates = build_date_set(get_current_entries_mssql(colleague=user_email))
    
    def build_map(dates, entry_type, existing_dates):
        return [
            {
                'date': d,
                'status': get_date_status(d, entry_type, d in existing_dates),
                'has_entry': d in existing_dates,
                'label': datetime.strptime(d, '%Y-%m-%d').strftime('%b %d')
            }
            for d in dates
        ]
    
    return jsonify({
        'forecasts': build_map(get_mondays_range(), 'forecast', forecast_dates),
        'actuals': build_map(get_fridays_range(), 'actual', current_dates),
        'next_monday': get_next_monday(),
        'last_friday': get_last_friday()
    })


@app.route('/api/project_breakdown')
def get_project_breakdown():
    """get project breakdown for donut chart"""
    user_email = request.args.get('email') or get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    project_totals = {}
    for entry in get_current_entries_mssql(colleague=user_email):
        project = entry['assignment_ID']
        if project and project != 'None':
            project_totals[project] = project_totals.get(project, 0) + (entry['allocation_days'] or 0)
    
    total_days = sum(project_totals.values())
    breakdown = [
        {
            'project': project,
            'days': round(days, 1),
            'percentage': round(days / total_days * 100, 1) if total_days > 0 else 0
        }
        for project, days in sorted(project_totals.items(), key=lambda x: x[1], reverse=True)
    ]
    
    return jsonify({'breakdown': breakdown, 'total_days': round(total_days, 1)})


@app.route('/api/team_activity_map')
def get_team_activity_map():
    """activity map for a team member (managers only)"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    member_email = request.args.get('member_email')
    if not member_email:
        return jsonify({'error': 'Member email required'}), 400
    
    direct_reports = get_direct_reports(user_email)
    if not any(r['email'].lower() == member_email.lower() for r in direct_reports):
        return jsonify({'error': 'Unauthorized'}), 403
    
    def build_date_set(entries):
        return {extract_date_string(e['activity_week']) for e in entries if extract_date_string(e['activity_week'])}
    
    forecast_dates = build_date_set(get_forecast_entries_mssql(colleague=member_email))
    current_dates = build_date_set(get_current_entries_mssql(colleague=member_email))
    
    def build_map(dates, entry_type, existing_dates):
        return [
            {
                'date': d,
                'status': get_date_status(d, entry_type, d in existing_dates),
                'has_entry': d in existing_dates,
                'label': datetime.strptime(d, '%Y-%m-%d').strftime('%b %d')
            }
            for d in dates
        ]
    
    return jsonify({
        'forecasts': build_map(get_mondays_range(), 'forecast', forecast_dates),
        'actuals': build_map(get_fridays_range(), 'actual', current_dates),
        'member_email': member_email,
        'member_name': get_user_name(member_email)
    })


@app.route('/api/outstanding_items')
def get_outstanding_items():
    """get outstanding items: missing actuals and upcoming forecast"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    today = datetime.now().date()
    items = []
    
    # Check missing actuals
    current_dates = {
        extract_date_string(e['activity_week']) 
        for e in get_current_entries_mssql(colleague=user_email)
        if extract_date_string(e['activity_week'])
    }
    
    for friday in get_fridays_range():
        friday_date = datetime.strptime(friday, '%Y-%m-%d').date()
        if friday_date <= today and friday not in current_dates:
            week_start = friday_date - timedelta(days=4)
            items.append({
                'date': friday,
                'week_commencing': week_start.strftime('%Y-%m-%d'),
                'week_commencing_label': week_start.strftime('%b %d, %Y'),
                'type': 'actual',
                'label': f"Week commencing {week_start.strftime('%b %d, %Y')} - Missing Actuals",
                'status': 'missing',
                'priority': 1
            })
    
    # Check upcoming forecast
    next_monday = get_next_monday()
    forecast_dates = {
        extract_date_string(e['activity_week']) 
        for e in get_forecast_entries_mssql(colleague=user_email)
        if extract_date_string(e['activity_week'])
    }
    
    if next_monday not in forecast_dates:
        monday_date = datetime.strptime(next_monday, '%Y-%m-%d').date()
        items.append({
            'date': next_monday,
            'week_commencing': next_monday,
            'week_commencing_label': monday_date.strftime('%b %d, %Y'),
            'type': 'forecast',
            'label': f"Week commencing {monday_date.strftime('%b %d, %Y')} - Forecast",
            'status': 'open',
            'priority': 2
        })
    
    items.sort(key=lambda x: (x['priority'], x['date']))
    return jsonify(items)


@app.route('/api/get_entry')
def get_entry():
    """get entries for a specific date and type"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    date = request.args.get('date')
    entry_type = request.args.get('type', 'forecast')
    
    if not date:
        return jsonify({'error': 'Date required'}), 400
    
    getter = get_forecast_entries_mssql if entry_type == 'forecast' else get_current_entries_mssql
    entries = getter(colleague=user_email, activity_week=date)
    
    result = [
        {'project': e['assignment_ID'], 'days': e['allocation_days'], 'notes': e['notes'] or ''}
        for e in entries
    ]
    
    return jsonify({'entries': result, 'exists': len(result) > 0, 'date': date, 'type': entry_type})


@app.route('/api/get_history')
def get_history():
    """get most recent timesheet entries"""
    user_email = get_user_email()
    entry_type, activity_week = get_most_recent_entry_mssql(user_email)
    
    if not entry_type:
        return jsonify([])
    
    getter = get_forecast_entries_mssql if entry_type == 'forecast' else get_current_entries_mssql
    entries = getter(colleague=user_email, activity_week=activity_week)
    
    return jsonify([
        {'project': e['assignment_ID'], 'days': e['allocation_days'], 'notes': e['notes'] or ''}
        for e in entries
    ])


@app.route('/submit', methods=['POST'])
def submit():
    """submit entries for a date"""
    user_email = get_user_email()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    selected_date = data.get('date')
    entry_type = data.get('type', 'forecast')
    rows = data.get('rows', [])
    
    if not selected_date:
        return jsonify({'error': 'Date required'}), 400
    
    today = datetime.now().date()
    date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
    
    if entry_type == 'forecast':
        next_monday = datetime.strptime(get_next_monday(), '%Y-%m-%d').date()
        if date_obj < today and date_obj != next_monday:
            return jsonify({'error': 'Cannot submit forecast for expired week'}), 400
    elif date_obj > today:
        return jsonify({'error': 'Cannot submit actuals for future week'}), 400
    
    try:
        saver = save_forecast_entries_mssql if entry_type == 'forecast' else save_current_entries_mssql
        saver(user_email, selected_date, rows)
        return jsonify({'success': True, 'message': f'Submitted for {selected_date}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/send_reminders', methods=['POST'])
def send_reminders():
    """send reminder emails to team members"""
    reminder_type = request.args.get('type', 'both')
    
    api_key = request.headers.get('X-API-Key')
    expected_key = os.environ.get('REMINDER_API_KEY')
    if expected_key and api_key != expected_key:
        return jsonify({'error': 'Unauthorized'}), 401
    
    team_members = get_all_team_members()
    if not team_members:
        return jsonify({'error': 'Could not fetch team members'}), 500
    
    results = {'forecast_reminders': [], 'actual_reminders': []}
    
    def send_reminder(members, check_func, activity_week, template_type):
        reminders = []
        for member in members:
            if not check_func(colleague=member['email'], activity_week=activity_week):
                week_date = datetime.strptime(activity_week, '%Y-%m-%d').date()
                if template_type == 'forecast':
                    subject = "WOOP Reminder: Forecast Not Submitted"
                    week_label = activity_week
                else:
                    subject = "WOOP Reminder: Actuals Not Submitted"
                    week_label = (week_date - timedelta(days=4)).strftime('%Y-%m-%d')
                
                body = f"""
                <html><body>
                <p>Hi {member['name']},</p>
                <p>Your <strong>{template_type}</strong> for week commencing <strong>{week_label}</strong> 
                has not been submitted.</p>
                <p>Please submit as soon as possible.</p>
                </body></html>
                """
                sent = send_reminder_email(member['email'], subject, body)
                reminders.append({'email': member['email'], 'name': member['name'], 'sent': sent})
        return reminders
    
    if reminder_type in ['forecast', 'both']:
        results['forecast_reminders'] = send_reminder(
            team_members, get_forecast_entries_mssql, get_next_monday(), 'forecast'
        )
    
    if reminder_type in ['actual', 'both']:
        results['actual_reminders'] = send_reminder(
            team_members, get_current_entries_mssql, get_last_friday(), 'actual'
        )
    
    return jsonify({
        'success': True,
        'results': results,
        'forecast_count': len(results['forecast_reminders']),
        'actual_count': len(results['actual_reminders'])
    })


@app.route('/api/send_nudge', methods=['POST'])
def send_nudge():
    """send a nudge to a team member"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    data = request.get_json()
    to_email = data.get('to_email') if data else None
    
    if not to_email:
        return jsonify({'error': 'Recipient email required'}), 400
    
    direct_reports = get_direct_reports(user_email)
    if not any(r['email'].lower() == to_email.lower() for r in direct_reports):
        return jsonify({'error': 'Unauthorized'}), 403
    
    import random
    nudge_messages = [
        "Hey there! Your timesheet is looking a bit lonely... 🥺",
        "Knock knock! Who's there? Your empty timesheet! 🚪",
        "Your manager sent a gentle reminder... FILL YOUR TIMESHEET! 😤",
        "The timesheet fairy visited, but left empty-handed 🧚",
        "Alert: Your timesheet has been spotted... completely blank! 🔍",
        "Fun fact: Timesheets don't fill themselves. We checked. Twice. 📊",
        "Your timesheet misses you 💔",
    ]
    
    try:
        nudge = Nudge(
            from_email=user_email.lower(),
            from_name=get_user_name(user_email),
            to_email=to_email.lower(),
            message=random.choice(nudge_messages)
        )
        db.session.add(nudge)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Nudge sent!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get_nudges')
def get_nudges():
    """get pending nudges for current user"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    nudges = Nudge.query.filter_by(to_email=user_email.lower(), dismissed=False)\
                        .order_by(Nudge.created.desc()).all()
    
    return jsonify([
        {
            'id': n.id,
            'from_name': n.from_name,
            'message': n.message,
            'created': n.created.strftime('%b %d at %H:%M')
        }
        for n in nudges
    ])


@app.route('/api/dismiss_nudge', methods=['POST'])
def dismiss_nudge():
    """dismiss nudge"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    nudge_id = request.get_json().get('nudge_id') if request.get_json() else None
    if not nudge_id:
        return jsonify({'error': 'Nudge ID required'}), 400
    
    nudge = Nudge.query.filter_by(id=nudge_id, to_email=user_email.lower()).first()
    if not nudge:
        return jsonify({'error': 'Nudge not found'}), 404
    
    nudge.dismissed = True
    db.session.commit()
    return jsonify({'success': True})


# initialization

def init_db():
    with app.app_context():
        db.create_all()
        print("Databases initialized")


# Initialize on import
with app.app_context():
    db.create_all()


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)