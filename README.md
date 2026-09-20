# School Council Election Website

## Features
- Candidate profiles grouped by post
- Approved-student verification
- One vote per student per post, enforced by SQLite UNIQUE(student_id, post_id)
- Admin dashboard
- Candidate photo uploads
- CSV student import
- Election open/close control
- Results hidden/visible control
- Results CSV export
- Audit log
- Responsive UI

## Run locally
Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

Default admin:
- username: admin
- password: admin123

CHANGE THESE CREDENTIALS BEFORE REAL USE.

## CSV import
Use a CSV with exactly these headers:

```csv
admission_number,name,class_name
1001,Student One,12 A
1002,Student Two,12 B
```

## Production
This starter is designed for local development and school testing. Before a real election:
- Use HTTPS and a production WSGI server.
- Change SECRET_KEY and admin credentials.
- Use PostgreSQL if required by scale/hosting.
- Keep the database and uploads outside public static hosting where appropriate.
- Configure backups.
- Have school authorities approve the collection and processing of student data.
- Do not publish student admission numbers or individual voting choices.
- Keep results hidden until the authorized election closing time.
