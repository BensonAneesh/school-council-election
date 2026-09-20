import os, sqlite3, csv, io
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "election.db")
UPLOADS = os.path.join(BASE, "static", "uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "CHANGE-ME-IN-PRODUCTION")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
ALLOWED = {"png","jpg","jpeg","webp"}

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    c=conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS admins(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS students(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      admission_number TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      class_name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS posts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL
    );
    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      post_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      class_name TEXT NOT NULL,
      photo TEXT,
      description TEXT,
      manifesto TEXT,
      FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS votes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id INTEGER NOT NULL,
      post_id INTEGER NOT NULL,
      candidate_id INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(student_id, post_id),
      FOREIGN KEY(student_id) REFERENCES students(id),
      FOREIGN KEY(post_id) REFERENCES posts(id),
      FOREIGN KEY(candidate_id) REFERENCES candidates(id)
    );
    CREATE TABLE IF NOT EXISTS audit_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      admin_id INTEGER,
      action TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    if not c.execute("SELECT 1 FROM admins LIMIT 1").fetchone():
        c.execute("INSERT INTO admins(username,password_hash) VALUES(?,?)",
                  ("admin", generate_password_hash("admin123")))
    defaults={
      "election_status":"OPEN",
      "results_visibility":"HIDDEN",
      "school_name":"School Council Election"
    }
    for k,v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
    c.commit(); c.close()

def setting(key):
    c=conn(); r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); c.close()
    return r["value"] if r else ""

def set_setting(key,value):
    c=conn(); c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value)); c.commit(); c.close()

def audit(action):
    c=conn(); c.execute("INSERT INTO audit_logs(admin_id,action,created_at) VALUES(?,?,?)",(session.get("admin_id"),action,datetime.utcnow().isoformat())); c.commit(); c.close()

def admin_required(f):
    @wraps(f)
    def w(*a,**kw):
        if "admin_id" not in session: return redirect(url_for("admin_login"))
        return f(*a,**kw)
    return w

@app.context_processor
def common():
    return {"school_name":setting("school_name"),"election_status":setting("election_status"),"results_visibility":setting("results_visibility")}

@app.route("/")
def index():
    c=conn(); posts=c.execute("SELECT * FROM posts ORDER BY id").fetchall()
    candidates={p["id"]:c.execute("SELECT * FROM candidates WHERE post_id=? ORDER BY id",(p["id"],)).fetchall() for p in posts}
    c.close()
    return render_template("index.html",posts=posts,candidates=candidates)

@app.route("/candidate/<int:cid>")
def candidate(cid):
    c=conn(); x=c.execute("""SELECT c.*,p.name post_name FROM candidates c JOIN posts p ON p.id=c.post_id WHERE c.id=?""",(cid,)).fetchone(); c.close()
    if not x: abort(404)
    return render_template("candidate.html",candidate=x)

@app.route("/vote",methods=["GET","POST"])
def vote():
    if setting("election_status")!="OPEN":
        return render_template("closed.html")
    c=conn(); posts=c.execute("SELECT * FROM posts ORDER BY id").fetchall()
    candidates={p["id"]:c.execute("SELECT * FROM candidates WHERE post_id=? ORDER BY id",(p["id"],)).fetchall() for p in posts}
    if request.method=="POST":
        admission=request.form.get("admission_number","").strip()
        name=request.form.get("name","").strip()
        cls=request.form.get("class_name","").strip()
        if not all([admission,name,cls]) or not posts:
            flash("Please complete your details and ensure posts are configured.")
            c.close(); return redirect(url_for("vote"))
        student=c.execute("SELECT * FROM students WHERE admission_number=?",(admission,)).fetchone()
        if not student:
            flash("This admission number is not in the approved student list. Please contact the election administrator.")
            c.close(); return redirect(url_for("vote"))
        if student["name"].strip().lower()!=name.lower() or student["class_name"].strip().lower()!=cls.lower():
            flash("Student details do not match the approved student list.")
            c.close(); return redirect(url_for("vote"))
        choices={}
        for p in posts:
            cid=request.form.get(f"post_{p['id']}")
            if not cid:
                flash(f"Please select one candidate for {p['name']}."); c.close(); return redirect(url_for("vote"))
            ok=c.execute("SELECT id FROM candidates WHERE id=? AND post_id=?",(cid,p["id"])).fetchone()
            if not ok:
                flash("Invalid candidate selection."); c.close(); return redirect(url_for("vote"))
            choices[p["id"]]=cid
        # Confirmation data is held server-side in session; no vote is stored yet.
        session["pending_vote"]={"student_id":student["id"],"choices":choices}
        c.close(); return redirect(url_for("confirm_vote"))
    c.close()
    return render_template("vote.html",posts=posts,candidates=candidates)

@app.route("/vote/confirm",methods=["GET","POST"])
def confirm_vote():
    pending=session.get("pending_vote")
    if not pending or setting("election_status")!="OPEN": return redirect(url_for("vote"))
    c=conn()
    student=c.execute("SELECT * FROM students WHERE id=?",(pending["student_id"],)).fetchone()
    rows=[]
    for pid,cid in pending["choices"].items():
        p=c.execute("SELECT name FROM posts WHERE id=?",(pid,)).fetchone()
        cand=c.execute("SELECT name FROM candidates WHERE id=?",(cid,)).fetchone()
        rows.append((p["name"],cand["name"]))
    if request.method=="POST":
        try:
            for pid,cid in pending["choices"].items():
                if c.execute("SELECT 1 FROM votes WHERE student_id=? AND post_id=?",(student["id"],pid)).fetchone():
                    raise ValueError("This student has already voted for one or more posts.")
                c.execute("INSERT INTO votes(student_id,post_id,candidate_id,created_at) VALUES(?,?,?,?,?)",
                          (student["id"],pid,cid,datetime.utcnow().isoformat()))
            c.commit(); session.pop("pending_vote",None); c.close()
            return render_template("success.html")
        except (sqlite3.IntegrityError,ValueError) as e:
            c.rollback(); c.close(); flash(str(e)); return redirect(url_for("vote"))
    c.close()
    return render_template("confirm.html",student=student,rows=rows)

@app.route("/results")
def results():
    if setting("results_visibility")!="VISIBLE" and "admin_id" not in session:
        return render_template("results_hidden.html")
    c=conn(); posts=c.execute("SELECT * FROM posts ORDER BY id").fetchall()
    data={}
    total=0
    for p in posts:
        rs=c.execute("""SELECT c.id,c.name,c.class_name,c.photo,COUNT(v.id) votes
                        FROM candidates c LEFT JOIN votes v ON v.candidate_id=c.id
                        WHERE c.post_id=? GROUP BY c.id ORDER BY votes DESC,c.name""",(p["id"],)).fetchall()
        data[p["id"]]=rs
        total += sum(r["votes"] for r in rs)
    registered=c.execute("SELECT COUNT(*) n FROM students").fetchone()["n"]
    c.close()
    return render_template("results.html",posts=posts,data=data,total=total,registered=registered)

@app.route("/admin/login",methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        c=conn(); a=c.execute("SELECT * FROM admins WHERE username=?",(request.form.get("username",""),)).fetchone(); c.close()
        if a and check_password_hash(a["password_hash"],request.form.get("password","")):
            session["admin_id"]=a["id"]; session["admin_user"]=a["username"]; audit("Admin logged in")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid username or password.")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("index"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    c=conn()
    stats={
      "students":c.execute("SELECT COUNT(*) n FROM students").fetchone()["n"],
      "candidates":c.execute("SELECT COUNT(*) n FROM candidates").fetchone()["n"],
      "posts":c.execute("SELECT COUNT(*) n FROM posts").fetchone()["n"],
      "votes":c.execute("SELECT COUNT(*) n FROM votes").fetchone()["n"]
    }
    posts=c.execute("SELECT * FROM posts ORDER BY id").fetchall()
    candidates=c.execute("""SELECT c.*,p.name post_name FROM candidates c JOIN posts p ON p.id=c.post_id ORDER BY p.id,c.id""").fetchall()
    students=c.execute("SELECT * FROM students ORDER BY id DESC LIMIT 20").fetchall()
    c.close()
    return render_template("admin.html",stats=stats,posts=posts,candidates=candidates,students=students)

@app.route("/admin/post/add",methods=["POST"])
@admin_required
def add_post():
    name=request.form.get("name","").strip()
    if name:
        c=conn()
        try: c.execute("INSERT INTO posts(name) VALUES(?)",(name,)); c.commit(); audit(f"Added post: {name}"); flash("Post added.")
        except sqlite3.IntegrityError: c.rollback(); flash("That post already exists.")
        c.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/post/delete/<int:pid>",methods=["POST"])
@admin_required
def delete_post(pid):
    c=conn(); c.execute("DELETE FROM posts WHERE id=?",(pid,)); c.commit(); c.close(); audit(f"Deleted post #{pid}"); flash("Post deleted."); return redirect(url_for("admin_dashboard"))

@app.route("/admin/candidate/add",methods=["POST"])
@admin_required
def add_candidate():
    name=request.form.get("name","").strip(); cls=request.form.get("class_name","").strip()
    pid=request.form.get("post_id"); desc=request.form.get("description","").strip(); manifesto=request.form.get("manifesto","").strip()
    photo=None
    f=request.files.get("photo")
    if f and f.filename:
        ext=f.filename.rsplit(".",1)[-1].lower()
        if ext not in ALLOWED: flash("Invalid image type."); return redirect(url_for("admin_dashboard"))
        filename=secure_filename(f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.{ext}")
        f.save(os.path.join(UPLOADS,filename)); photo=filename
    c=conn(); c.execute("INSERT INTO candidates(post_id,name,class_name,photo,description,manifesto) VALUES(?,?,?,?,?,?)",(pid,name,cls,photo,desc,manifesto)); c.commit(); c.close()
    audit(f"Added candidate: {name}"); flash("Candidate added."); return redirect(url_for("admin_dashboard"))

@app.route("/admin/candidate/delete/<int:cid>",methods=["POST"])
@admin_required
def delete_candidate(cid):
    c=conn(); row=c.execute("SELECT photo,name FROM candidates WHERE id=?",(cid,)).fetchone()
    if row:
        c.execute("DELETE FROM candidates WHERE id=?",(cid,)); c.commit()
        if row["photo"]:
            try: os.remove(os.path.join(UPLOADS,row["photo"]))
            except OSError: pass
        audit(f"Deleted candidate: {row['name']}")
    c.close(); flash("Candidate deleted."); return redirect(url_for("admin_dashboard"))

@app.route("/admin/student/add",methods=["POST"])
@admin_required
def add_student():
    adm=request.form.get("admission_number","").strip(); name=request.form.get("name","").strip(); cls=request.form.get("class_name","").strip()
    c=conn()
    try: c.execute("INSERT INTO students(admission_number,name,class_name) VALUES(?,?,?)",(adm,name,cls)); c.commit(); flash("Student added.")
    except sqlite3.IntegrityError: c.rollback(); flash("Admission number already exists.")
    c.close(); return redirect(url_for("admin_dashboard"))

@app.route("/admin/students/import",methods=["POST"])
@admin_required
def import_students():
    f=request.files.get("csv_file")
    if not f: flash("Choose a CSV file."); return redirect(url_for("admin_dashboard"))
    text=f.read().decode("utf-8-sig")
    c=conn(); added=0
    reader=csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            c.execute("INSERT INTO students(admission_number,name,class_name) VALUES(?,?,?)",
                      (row["admission_number"].strip(),row["name"].strip(),row["class_name"].strip())); added+=1
        except (sqlite3.IntegrityError,KeyError): pass
    c.commit(); c.close(); audit(f"Imported {added} students"); flash(f"Imported {added} students."); return redirect(url_for("admin_dashboard"))

@app.route("/admin/settings",methods=["POST"])
@admin_required
def settings():
    status=request.form.get("election_status","CLOSED")
    visibility=request.form.get("results_visibility","HIDDEN")
    set_setting("election_status",status); set_setting("results_visibility",visibility)
    audit(f"Changed election settings: {status}, results {visibility}"); flash("Election settings updated."); return redirect(url_for("admin_dashboard"))

@app.route("/admin/results.csv")
@admin_required
def export_results():
    c=conn(); rows=c.execute("""SELECT p.name post_name,c.name candidate,c.class_name,COUNT(v.id) votes
                               FROM candidates c JOIN posts p ON p.id=c.post_id
                               LEFT JOIN votes v ON v.candidate_id=c.id
                               GROUP BY c.id ORDER BY p.id,votes DESC,c.name""").fetchall()
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Post","Candidate","Class","Votes"])
    for r in rows: w.writerow([r["post_name"],r["candidate"],r["class_name"],r["votes"]])
    c.close(); return send_file(io.BytesIO(out.getvalue().encode()),mimetype="text/csv",as_attachment=True,download_name="election_results.csv")

@app.route("/admin/audit")
@admin_required
def audit_page():
    c=conn(); logs=c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 200").fetchall(); c.close()
    return render_template("audit.html",logs=logs)

@app.errorhandler(413)
def too_large(e):
    flash("Image/file is too large. Maximum upload size is 4 MB.")
    return redirect(url_for("admin_dashboard"))

init_db()
if __name__=="__main__":
    app.run(host="127.0.0.1",port=5000,debug=True)
