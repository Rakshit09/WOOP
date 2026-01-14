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



_mssql_engine = None

def get_today():
    """get current date"""
    #today = datetime.now().date()
    today = datetime(2026, 2, 17).date()
    return today


def get_open_actuals_friday():
    """get the Friday that is currently open for actuals input
    
    Actuals open on Friday and stay open through the following Thursday.
    Window: Friday to Thursday (7 days).
    """
    today = get_today()
    # days since the most recent Friday (0 if today is Friday)
    days_since_friday = (today.weekday() - 4) % 7
    open_friday = today - timedelta(days=days_since_friday)
    return open_friday.strftime('%Y-%m-%d')


def get_open_forecast_monday():
    """get Monday that is currently open for forecast input.
    
    NOte: Forecast opens on Friday for the next week and stays open through that week -- should be cut off on Monday/Tuesday instead?
    Window: Friday to Thursday (7 days).
    """
    today = get_today()
    # days since the most recent Friday
    days_since_friday = (today.weekday() - 4) % 7
    most_recent_friday = today - timedelta(days=days_since_friday)
    # Open forecast Monday is 3 days after that Friday (next Monday)
    open_monday = most_recent_friday + timedelta(days=3)
    return open_monday.strftime('%Y-%m-%d')


def get_date_status(date_str, entry_type, has_entry):
    """et status for activity map cell."""
    if has_entry:
        return 'green', 'Completed'
    
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    if entry_type == 'forecast':
        open_forecast_monday = datetime.strptime(get_open_forecast_monday(), '%Y-%m-%d').date()
        
        if date_obj == open_forecast_monday:
            return 'blue', 'Open for Input'
        
        # Gray - determine if expired or locked
        if date_obj < open_forecast_monday:
            return 'gray', 'Expired'
        else:
            return 'gray', 'Locked'
    else:
        # actuals logic
        open_actuals_friday = datetime.strptime(get_open_actuals_friday(), '%Y-%m-%d').date()
        
        if date_obj > open_actuals_friday:
            return 'gray', 'Locked'
        
        if date_obj == open_actuals_friday:
            return 'blue', 'Open for Input'
        
        return 'red', 'Missing Actuals'


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
    today = get_today()
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


def get_weeks_for_year():
    """
    get all weeks for the current year as (Monday, Friday) pairs - ensures forecast and actual rows are aligned
    Returns: List of dicts with 'monday' and 'friday' keys.
    """
    current_day = get_today()
    year_start = datetime(current_day.year, 1, 1).date()
    year_end = datetime(current_day.year, 12, 31).date()
    
    # find first Monday of the year
    days_since_monday = year_start.weekday()  # Monday = 0
    if days_since_monday == 0:
        first_monday = year_start
    else:
        # go next Monday
        first_monday = year_start + timedelta(days=(7 - days_since_monday))
    
    weeks = []
    current_monday = first_monday
    
    while current_monday <= year_end:
        current_friday = current_monday + timedelta(days=4)
        weeks.append({
            'monday': current_monday.strftime('%Y-%m-%d'),
            'friday': current_friday.strftime('%Y-%m-%d')
        })
        current_monday += timedelta(weeks=1)
    
    return weeks


def get_mondays_range():
    """get all Mondays for the year - aligned with Fridays"""
    return [week['monday'] for week in get_weeks_for_year()]


def get_fridays_range():
    """get all Fridays for the year - aligned with Mondays"""
    return [week['friday'] for week in get_weeks_for_year()]



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

def get_current_week_monday():
    """get Monday of the current week"""
    today = get_today()
    days_since_monday = today.weekday()  # Monday = 0
    return (today - timedelta(days=days_since_monday)).strftime('%Y-%m-%d')


def get_current_week_friday():
    """get Friday of the current week"""
    today = get_today()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    friday = monday + timedelta(days=4)
    return friday.strftime('%Y-%m-%d')


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
            # DELETE EXISTING ENTRIES FIRST for the same activity week and colleague - used in modifying submitted entries
            conn.execute(
                text(f"""
                    DELETE FROM {table_name}
                    WHERE CAST(activity_week AS DATE) = CAST(:activity_week AS DATE)
                    AND (LOWER(colleague) = LOWER(:colleague_email) 
                         OR LOWER(colleague) = LOWER(:colleague_name))"""),
                {
                    'activity_week': activity_week_str,
                    'colleague_email': colleague_email,
                    'colleague_name': colleague_name,
                }
            )
            
            # insert new entries
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
                                :allocation_days, :notes, :record_created)"""),
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
                        ORDER BY record_created DESC"""),
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
            #return credentials_header
            return "unknown_user@gallagherre.com"
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


def verify_user_exists(email):
    """check if the user exists in the team list table"""
    engine = get_engine()
    if engine is None:
        return False

    try:
        with engine.connect() as conn:
            # check if email exists 
            result = conn.execute(
                text("SELECT COUNT(*) FROM dbo.EMEA_team_list WHERE LOWER(Email) = LOWER(:email)"),
                {"email": email}
            ).fetchone()
            
            return result[0] > 0
    except Exception as e:
        logger.error(f"Error verifying user existence: {e}")
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
    logger.info(f"User email retrieved: {user_email}")
    
    if not user_email:
        return "Unable to identify user. Please ensure you are logged in.", 401
    
    is_authorized = verify_user_exists(user_email)
    logger.info(f"User {user_email} authorization check: {is_authorized}")
    
    if not is_authorized:
        logger.warning(f"Unauthorized user: {user_email}")
    
    return render_template(
        'index.html',
        user_email=user_email,
        user_name=get_user_name(user_email) if is_authorized else user_email,
        default_date=get_next_monday(),
        projects=load_active_projects(),
        direct_reports=get_direct_reports(user_email),
        is_authorized=is_authorized,
        server_date=get_today().strftime('%Y-%m-%d')  
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
        result = []
        for d in dates:
            status, label = get_date_status(d, entry_type, d in existing_dates)
            result.append({
                'date': d,
                'status': status,
                'status_label': label,  # Add this
                'has_entry': d in existing_dates,
                'label': datetime.strptime(d, '%Y-%m-%d').strftime('%b %d')
            })
        return result
    
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
        result = []
        for d in dates:
            status, label = get_date_status(d, entry_type, d in existing_dates)
            result.append({
                'date': d,
                'status': status,
                'status_label': label,
                'has_entry': d in existing_dates,
                'label': datetime.strptime(d, '%Y-%m-%d').strftime('%b %d')
            })
        return result
    
    return jsonify({
        'forecasts': build_map(get_mondays_range(), 'forecast', forecast_dates),
        'actuals': build_map(get_fridays_range(), 'actual', current_dates),
        'member_email': member_email,
        'member_name': get_user_name(member_email)
    })


@app.route('/api/outstanding_items')
def get_outstanding_items():
    """get outstanding items: missing actuals and open forecast"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    items = []
    
    # Check missing actuals - use the new open window logic
    current_dates = {
        extract_date_string(e['activity_week']) 
        for e in get_current_entries_mssql(colleague=user_email)
        if extract_date_string(e['activity_week'])
    }
    
    # Get the currently open actuals Friday (Friday-Thursday window)
    open_actuals_friday = datetime.strptime(get_open_actuals_friday(), '%Y-%m-%d').date()
    
    for friday in get_fridays_range():
        friday_date = datetime.strptime(friday, '%Y-%m-%d').date()
        
        # Skip weeks beyond the currently open week
        if friday_date > open_actuals_friday:
            continue
            
        # Skip if already submitted
        if friday in current_dates:
            continue
        
        week_start = friday_date - timedelta(days=4)
        date_range_str = f"{week_start.strftime('%b %d, %Y')} - {friday_date.strftime('%b %d, %Y')}"
        
        # Current open week = open, past weeks = missing
        if friday_date == open_actuals_friday:
            items.append({
                'date': friday,
                'week_commencing': week_start.strftime('%Y-%m-%d'),
                'week_commencing_label': date_range_str,
                'type': 'actual',
                'label': f"Week {date_range_str} - Actuals",
                'status': 'open',
                'priority': 1
            })
        else:
            items.append({
                'date': friday,
                'week_commencing': week_start.strftime('%Y-%m-%d'),
                'week_commencing_label': date_range_str,
                'type': 'actual',
                'label': f"Week {date_range_str} - Missing Actuals",
                'status': 'missing',
                'priority': 0
            })
    
    # check forecast - use the new open window logic
    forecast_dates = {
        extract_date_string(e['activity_week']) 
        for e in get_forecast_entries_mssql(colleague=user_email)
        if extract_date_string(e['activity_week'])
    }
    
    # Get the currently open forecast Monday (Friday-Thursday window)
    open_forecast_monday = get_open_forecast_monday()
    open_forecast_monday_date = datetime.strptime(open_forecast_monday, '%Y-%m-%d').date()
    
    # Add open forecast ONLY if not already submitted
    if open_forecast_monday not in forecast_dates:
        friday_date = open_forecast_monday_date + timedelta(days=4)
        date_range_str = f"{open_forecast_monday_date.strftime('%b %d, %Y')} - {friday_date.strftime('%b %d, %Y')}"
        
        items.append({
            'date': open_forecast_monday,
            'week_commencing': open_forecast_monday,
            'week_commencing_label': date_range_str,
            'type': 'forecast',
            'label': f"Week {date_range_str} - Forecast",
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
    """get strictly the previous week's entries relative to selected date"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401

    current_date_str = request.args.get('date')
    entry_type = request.args.get('type', 'forecast')

    if not current_date_str:
        return jsonify({'error': 'Current date required'}), 400

    try:
        current_date_obj = datetime.strptime(current_date_str, '%Y-%m-%d').date()
        prev_week_date = current_date_obj - timedelta(days=7)
        prev_week_str = prev_week_date.strftime('%Y-%m-%d')
        
        getter = get_forecast_entries_mssql if entry_type == 'forecast' else get_current_entries_mssql
        entries = getter(colleague=user_email, activity_week=prev_week_str)

        if not entries:
            return jsonify({'error': 'Not found'}), 404

        return jsonify([
            {
                'project': e['assignment_ID'], 
                'days': e['allocation_days'], 
                'notes': e['notes'] or ''
            }
            for e in entries
        ])

    except Exception as e:
        logger.error(f"Error getting history: {e}")
        return jsonify({'error': str(e)}), 500


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
    
    date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
    
    if entry_type == 'forecast':
        # Forecast opens Friday for next week, stays open through that week (until next Friday)
        open_forecast_monday = datetime.strptime(get_open_forecast_monday(), '%Y-%m-%d').date()
        
        if date_obj != open_forecast_monday:
            return jsonify({'error': 'Cannot submit forecast for this week'}), 400
    else:  # actuals
        # Actuals open Friday, stay open through following Thursday (until next Friday)
        open_actuals_friday = datetime.strptime(get_open_actuals_friday(), '%Y-%m-%d').date()
        if date_obj > open_actuals_friday:
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
            team_members, get_forecast_entries_mssql, get_open_forecast_monday(), 'forecast'
        )
    
    if reminder_type in ['actual', 'both']:
        results['actual_reminders'] = send_reminder(
            team_members, get_current_entries_mssql, get_open_actuals_friday(), 'actual'
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
    
    # Check Authorization (Direct Reports Only)
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
    
    engine = get_engine()
    if not engine:
        return jsonify({'error': 'Database connection failed'}), 500

    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO dbo.nudges (from_email, from_name, to_email, message, created, dismissed)
                    VALUES (:from_email, :from_name, :to_email, :message, GETUTCDATE(), 0)
                """),
                {
                    'from_email': user_email.lower(),
                    'from_name': get_user_name(user_email),
                    'to_email': to_email.lower(),
                    'message': random.choice(nudge_messages)
                }
            )
        return jsonify({'success': True, 'message': 'Nudge sent!'})
    except Exception as e:
        logger.error(f"Error sending nudge: {e}")
        return jsonify({'error': str(e)}), 500



@app.route('/api/get_nudges')
def get_nudges():
    """get pending nudges for current user"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    engine = get_engine()
    if not engine:
        return jsonify([])

    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT id, from_name, message, created 
                    FROM dbo.nudges 
                    WHERE LOWER(to_email) = LOWER(:email) 
                      AND dismissed = 0
                    ORDER BY created DESC
                """),
                {'email': user_email}
            ).fetchall()
            
            nudges = []
            for row in result:
                # row[3] =datetime object from MSSQL
                created_str = row[3].strftime('%b %d at %H:%M') if row[3] else ''
                nudges.append({
                    'id': row[0],
                    'from_name': row[1],
                    'message': row[2],
                    'created': created_str
                })
            
            return jsonify(nudges)
    except Exception as e:
        logger.error(f"Error fetching nudges: {e}")
        return jsonify([])

@app.route('/api/dismiss_nudge', methods=['POST'])
def dismiss_nudge():
    """dismiss nudge"""
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not authenticated'}), 401
    
    data = request.get_json()
    nudge_id = data.get('nudge_id') if data else None
    
    if not nudge_id:
        return jsonify({'error': 'Nudge ID required'}), 400
    
    engine = get_engine()
    if not engine:
        return jsonify({'error': 'Database connection failed'}), 500

    try:
        with engine.begin() as conn:
            # only dismiss if belongs to current user
            result = conn.execute(
                text("""
                    UPDATE dbo.nudges 
                    SET dismissed = 1 
                    WHERE id = :id AND LOWER(to_email) = LOWER(:email)
                """),
                {'id': nudge_id, 'email': user_email}
            )
            
            if result.rowcount == 0:
                 # either ID didn't exist or belonged to someone else
                 pass 

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error dismissing nudge: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug_auth')
def debug_auth():
    """Debug authorization issues"""
    user_email = get_user_email()
    engine = get_engine()
    
    debug_info = {
        'user_email': user_email,
        'engine_available': engine is not None,
        'verify_result': None,
        'db_emails_like_user': []
    }
    
    if engine and user_email:
        try:
            with engine.connect() as conn:
                debug_info['verify_result'] = verify_user_exists(user_email)
                result = conn.execute(
                    text("""
                        SELECT Email, LEN(Email) as len, 
                               LOWER(LTRIM(RTRIM(Email))) as cleaned
                        FROM dbo.EMEA_team_list 
                        WHERE Email LIKE :pattern
                    """),
                    {"pattern": f"%{user_email.split('@')[0]}%"}
                ).fetchall()
                
                debug_info['db_emails_like_user'] = [
                    {'email': r[0], 'length': r[1], 'cleaned': r[2]} 
                    for r in result
                ]
                
                # exact comparison
                debug_info['user_email_lower'] = user_email.lower()
                debug_info['user_email_length'] = len(user_email)
                
        except Exception as e:
            debug_info['error'] = str(e)
    
    return jsonify(debug_info)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)