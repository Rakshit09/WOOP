"""
Seamless Timesheet Application
A Flask-based web app for weekly resource scheduling with zero-friction design.
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import pandas as pd
import os

# Initialize Flask app
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
    week_commencing = db.Column(db.String(10), nullable=False, index=True)  # YYYY-MM-DD format
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
        # Filter for active projects only
        active_df = df[df['Active'] == True]
        projects = active_df['ProjectName'].tolist()
        return sorted(projects)
    except Exception as e:
        print(f"Error loading projects.csv: {e}")
        return []


# ============================
# Helper Functions
# ============================
def get_user_email():
    """Extract user email/identity from Posit Connect headers."""
    
    # ============================================
    # DEBUG: Uncomment to see all available headers
    # Check your Posit Connect logs after accessing the app
    # ============================================
    import sys
    print("=== Request Headers ===", file=sys.stderr)
    for key, value in request.headers:
         print(f"{key}: {value}", file=sys.stderr)
    print("=== Environment Variables ===", file=sys.stderr)
    for key, value in os.environ.items():
         if 'USER' in key.upper() or 'CONNECT' in key.upper() or 'RSC' in key.upper():
             print(f"{key}: {value}", file=sys.stderr)
    print("========================", file=sys.stderr)
    
    # -----------------------------------------
    # 1. Modern Posit Connect Headers (v2023+)
    # -----------------------------------------
    user_email = request.headers.get('X-RSC-User-Email')
    if user_email:
        return user_email
    
    user_name = request.headers.get('X-RSC-User-Name')
    if user_name:
        return user_name
    
    # Also try with different casing
    user_email = request.headers.get('X-Rsc-User-Email')
    if user_email:
        return user_email
    
    # -----------------------------------------
    # 2. Legacy RStudio Connect Headers
    # -----------------------------------------
    legacy_headers = [
        'RStudio-Connect-Credentials',
        'Rstudio-Connect-User',
        'Posit-Connect-User',
        'Posit-Connect-Email',
        'RStudio-Connect-Email',
    ]
    
    for header in legacy_headers:
        value = request.headers.get(header)
        if value:
            return value
    
    # -----------------------------------------
    # 3. Proxy Authentication Headers
    # -----------------------------------------
    proxy_headers = [
        'X-Auth-Username',
        'X-Auth-Email',
        'X-Forwarded-User',
        'X-Remote-User',
        'Remote-User',
    ]
    
    for header in proxy_headers:
        value = request.headers.get(header)
        if value:
            return value
    
    # -----------------------------------------
    # 4. Environment Variables (for some contexts)
    # -----------------------------------------
    env_vars = [
        'RSTUDIO_CONNECT_USER',
        'CONNECT_USER',
        'SHINY_USER',
    ]
    
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            return value
    
    # -----------------------------------------
    # 5. Fallback for Local Development Only
    # -----------------------------------------
    if os.environ.get('FLASK_DEBUG') or os.environ.get('FLASK_ENV') == 'development':
        return request.args.get('user', 'dev.user@example.com')
    
    # Return None or a clear indicator if no auth found
    return None

def get_next_monday():
    """Calculate the date of the upcoming Monday in YYYY-MM-DD format."""
    today = datetime.now().date()
    days_ahead = 0 - today.weekday()  # Monday is 0
    
    # If today is Monday or later in the week, get next Monday
    if days_ahead <= 0:
        days_ahead += 7
    
    next_monday = today + timedelta(days=days_ahead)
    return next_monday.strftime('%Y-%m-%d')


# ============================
# Routes
# ============================
@app.route('/')
def index():
    """Render the main timesheet interface."""
    user_email = get_user_email()
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
    """
    Retrieve the most recent week's timesheet entries for the authenticated user.
    Used for the "Copy Last Week" feature.
    """
    user_email = get_user_email()
    
    # Find the most recent week_commencing date for this user
    most_recent = db.session.query(TimesheetEntry.week_commencing)\
        .filter_by(user_email=user_email)\
        .order_by(TimesheetEntry.week_commencing.desc())\
        .first()
    
    if not most_recent:
        return jsonify([])
    
    # Get all entries from that week
    entries = TimesheetEntry.query.filter_by(
        user_email=user_email,
        week_commencing=most_recent[0]
    ).all()
    
    # Format response
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
    """
    Submit timesheet entries for a specific week.
    Overwrites any existing entries for that user/week combination.
    """
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
            
            # Only insert if project and days are provided
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

