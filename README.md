WOOP 2.0 

WOOP 2.0 replaces the old WOOP which was based on Power Apps. The goal was simple: make workload planning and timesheets easier and faster. It’s a fast, single-page Flask app that is deployed on posit Connect so you can get your scheduling done and get back to actual work.

Why this is better:
Speed: No page reloads. Everything happens instantly.
Smart Search: Just start typing a project name; the filter handles the rest.
Visual Cues: The UI changes color to let you know if you're under or over your 5-day target.
One-Click Copy: If this week looks like last week, just hit "Copy Last Week" and you're done.
Getting Started
1. Grab the dependencies:

pip install -r requirements.txt

2. Set up your projects:
Open projects.csv and list the projects you want available in the dropdown.

3. Run it:

python app.py
Head to http://localhost:5000.

Tech Stack
Backend: Python (Flask + SQLAlchemy)
Frontend: Vanilla JS &  CSS
Database: MSSQL

Project Layout
.
├── app.py              # Flask server & database logic
├── projects.csv        # List of active projects for the dropdown
├── requirements.txt    # Python packages
├── README.md           # readme
├── .gitignore          # gitignore
├── templates/
│   └── index.html      # frontend page
└── static/             # CSS, JS, images

Deployment Notes
This app is designed to run on Posit Connect. It looks for the X-Auth-User header for authentication.