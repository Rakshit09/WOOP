"""
Seamless Timesheet Application
A Flask-based web app for weekly resource scheduling with zero-friction design.
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

# Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timesheet.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)


# ============================
# Database Model
# ============================
class TimesheetEntry(db.Model):
    """Stores individual timesheet entries for users."""
    __tablename__ = 'timesheet_entry'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_email = db.Column(db.String(255), nullable=False, index=True)
    week_commencing = db.Column(db.String(10), nullable=False, index=True)
    project_name = db.Column(db.String(255), nullable=False)
    days = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<TimesheetEntry {self.user_email} - {self.week_commencing} - {self.project_name}>'


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
        active_df = df[df['Active'] == True]
        projects = active_df['ProjectName'].tolist()
        return sorted(projects)
    except Exception as e:
        print(f"Error loading projects.csv: {e}")
        return []


# ============================
# User Authentication
# ============================
@lru_cache(maxsize=100)
def lookup_email_by_username(username):
    """
    Fetch user email from Posit Connect API.
    Results are cached to avoid repeated API calls.
    """
    connect_server = os.environ.get('CONNECT_SERVER', '').rstrip('/')
    api_key = os.environ.get('CONNECT_API_KEY', '')
    
    if not connect_server or not api_key:
        print("Warning: CONNECT_SERVER or CONNECT_API_KEY not available")
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
        else:
            print(f"API returned status {response.status_code}")
                    
    except Exception as e:
        print(f"Error looking up user email: {e}")
    
    return None


def get_user_email():
    """
    Extract user email from Posit Connect.
    Parses credentials header and looks up email via API.
    """
    username = None
    
    # Parse the Rstudio-Connect-Credentials header
    credentials_header = request.headers.get('Rstudio-Connect-Credentials')
    if credentials_header:
        try:
            credentials = json.loads(credentials_header)
            username = credentials.get('user')
        except json.JSONDecodeError:
            print(f"Failed to parse credentials: {credentials_header}")
    
    if not username:
        # Local development fallback
        if os.environ.get('FLASK_DEBUG') or app.debug:
            return request.args.get('user', 'dev.user@gallagherre.com')
        return None
    
    # Look up email via Posit Connect API
    email = lookup_email_by_username(username)
    
    return email if email else username


def get_next_monday():
    """Calculate the date of the upcoming Monday in YYYY-MM-DD format.
    If today is Monday, returns today. Otherwise returns next Monday.
    """
    today = datetime.now().date()
    days_until_monday = (7 - today.weekday()) % 7
    # If today is Monday, days_until_monday is 0 (use today)
    # Otherwise, calculate days until next Monday
    next_monday = today + timedelta(days=days_until_monday)
    return next_monday.strftime('%Y-%m-%d')


# ============================
# Routes
# ============================
@app.route('/')
def index():
    """Render the main timesheet interface."""
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


@app.route('/api/get_history')
def get_history():
    """Retrieve the most recent week's timesheet entries for the user."""
    user_email = get_user_email()
    
    most_recent = db.session.query(TimesheetEntry.week_commencing)\
        .filter_by(user_email=user_email)\
        .order_by(TimesheetEntry.week_commencing.desc())\
        .first()
    
    if not most_recent:
        return jsonify([])
    
    entries = TimesheetEntry.query.filter_by(
        user_email=user_email,
        week_commencing=most_recent[0]
    ).all()
    
    result = [
        {
            'project': entry.project_name,
            'days': entry.days,
            'notes': entry.notes or ''
        }
        for entry in entries
    ]
    
    return jsonify(result)


@app.route('/submit', methods=['POST'])
def submit():
    """Submit timesheet entries for a specific week."""
    user_email = get_user_email()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    week_commencing = data.get('date')
    rows = data.get('rows', [])
    
    if not week_commencing:
        return jsonify({'error': 'Week commencing date is required'}), 400
    
    try:
        TimesheetEntry.query.filter_by(
            user_email=user_email,
            week_commencing=week_commencing
        ).delete()
        
        for row in rows:
            project = row.get('project', '').strip()
            days = row.get('days')
            notes = row.get('notes', '').strip()
            
            if project and days is not None and days > 0:
                entry = TimesheetEntry(
                    user_email=user_email,
                    week_commencing=week_commencing,
                    project_name=project,
                    days=float(days),
                    notes=notes
                )
                db.session.add(entry)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Timesheet submitted successfully for week of {week_commencing}'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


def init_db():
    """Initialize the database tables."""
    with app.app_context():
        db.create_all()
        print("Database initialized successfully.")


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)