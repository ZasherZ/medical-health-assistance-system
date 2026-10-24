"""Medical Health Assistance System for Students and Faculty (Flask + SQLite)."""
import os, re, sqlite3
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("HEALTH_DB", os.path.join(BASE, "health.db"))
SLOTS = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00"]
STATUSES = ["Pending", "In Review", "Immediate Assistance Provided", "Advice Given", "Resolved"]
CLOSED = ("Immediate Assistance Provided", "Advice Given", "Resolved")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL, mobile TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL,
  department TEXT DEFAULT '', blood_group TEXT DEFAULT '', emergency_contact TEXT DEFAULT '',
  notes TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER,
  doctor_id INTEGER, date TEXT, time TEXT, reason TEXT DEFAULT '', status TEXT DEFAULT 'Confirmed');
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symptoms TEXT,
  details TEXT, location TEXT, contact TEXT, emergency INTEGER DEFAULT 0, status TEXT DEFAULT 'Pending',
  advice TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER, doctor_id INTEGER,
  request_id INTEGER, diagnosis TEXT, prescription TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT,
  created TEXT, seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT,
  text TEXT, remind_at TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

def init_db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM users").fetchone():
        for n, e, m, p, r, d in [
            ("Administrator", "admin@college.edu", "9000000000", "Admin@123", "admin", "Administration"),
            ("Dr. Asha Rao", "asha@college.edu", "9000000001", "Doctor@123", "doctor", "General Physician"),
            ("Dr. Rahul Patil", "rahul@college.edu", "9000000002", "Doctor@123", "doctor", "Emergency Medicine")]:
            con.execute("INSERT INTO users(name,email,mobile,password,role,department) VALUES(?,?,?,?,?,?)",
                        (n, e, m, generate_password_hash(p), r, d))
    for k, v in {"emergency_number": "112", "ambulance": "108",
                 "health_centre": "Set in Admin > Configure"}.items():
        con.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    con.commit(); con.close()

# ---------- helpers ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, a=(), one=False):
    r = db().execute(sql, a).fetchall()
    return (r[0] if r else None) if one else r

def ex(sql, a=()):
    c = db().execute(sql, a); db().commit(); return c.lastrowid

def count(sql, a=()):
    return q(sql, a, True)[0]

def now(): return datetime.now().strftime("%Y-%m-%d %H:%M")

def notify(uid, msg):
    ex("INSERT INTO notifications(user_id,message,created) VALUES(?,?,?)", (uid, msg, now()))

def me():
    uid = session.get("uid")
    return q("SELECT * FROM users WHERE id=? AND active=1", (uid,), True) if uid else None

def login_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = me()
            if not u:
                flash("Please log in.", "error"); return redirect(url_for("login"))
            if roles and u["role"] not in roles:
                flash("Access denied.", "error"); return redirect(url_for("dashboard"))
            g.user = u
            return f(*a, **k)
        return w
    return deco

@app.context_processor
def inject():
    u = me()
    return dict(user=u,
                unread=count("SELECT COUNT(*) FROM notifications WHERE user_id=? AND seen=0", (u["id"],)) if u else 0,
                contacts={r["key"]: r["value"] for r in q("SELECT * FROM settings")},
                today=date.today().isoformat(), slots=SLOTS, statuses=STATUSES)

EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"
PHONE_RE = r"\d{10}"

def validate_request(symptoms, location, contact):
    """Emergency/medical request validation (white box target, TC02/TC03/TC05)."""
    if not symptoms or not location or not contact:
        return "Please fill in all required fields."
    if not re.fullmatch(PHONE_RE, contact):
        return "Invalid contact details: enter a 10-digit mobile number."
    if len(location) < 3:
        return "Invalid location details."
    return None

GUIDE = {
    "fever": "Rest, drink plenty of fluids and monitor your temperature. See a doctor if fever lasts over 2 days or goes above 39 C.",
    "cough": "Drink warm fluids and avoid dust and smoke. See a doctor if the cough lasts over a week or has blood.",
    "cold": "Rest, stay hydrated and use steam inhalation. Most colds settle within a week.",
    "sore throat": "Gargle with warm salt water and drink warm fluids. See a doctor if it lasts over 3 days.",
    "headache": "Rest in a quiet, dark room and drink water. See a doctor if it is sudden, severe or follows a head injury.",
    "stomach": "Eat light food and sip fluids or ORS. See a doctor if pain is severe or lasts over a day.",
    "vomit": "Sip small amounts of fluid or ORS. Seek care if you cannot keep fluids down for 12 hours.",
    "diarrhea": "Drink ORS and fluids and eat light food. Seek care if there is blood or it lasts over 2 days.",
    "rash": "Avoid scratching and new products on the skin. See a doctor if it spreads fast or comes with fever.",
    "wound": "Wash with clean water, apply pressure to stop bleeding and cover with a clean dressing.",
    "injury": "Rest the area, apply a cold pack and keep it raised. Get an X-ray if you cannot move it or it is badly swollen.",
    "back pain": "Keep moving gently, avoid heavy lifting and use heat packs. See a doctor if it spreads to the legs.",
    "dizz": "Sit or lie down and drink water. See a doctor if dizziness is repeated or comes with fainting.",
    "stress": "Take breaks, sleep well and talk to someone you trust. Counselling support is available through the health centre.",
    "anxiety": "Try slow breathing and talk to someone you trust. Book an appointment if it affects daily life.",
}
URGENT = ["chest pain", "breathing", "breathless", "unconscious", "faint", "heavy bleeding", "seizure",
          "stroke", "poison", "suicid", "severe"]

def guidance_for(text):
    t = text.lower()
    return [v for k, v in GUIDE.items() if k in t], any(w in t for w in URGENT)

def book_appt(pid, did, d, t, reason):
    doc = q("SELECT * FROM users WHERE id=? AND role='doctor' AND active=1", (did,), True)
    if not doc: return "Select a valid doctor."
    try: when = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
    except ValueError: return "Select a valid date and time."
    if t not in SLOTS: return "Select a valid time slot."
    if when <= datetime.now(): return "Choose a future date and time."
    if q("SELECT 1 FROM appointments WHERE doctor_id=? AND date=? AND time=? AND status!='Cancelled'", (did, d, t), True):
        return "That slot is already booked. Choose another."
    if q("SELECT 1 FROM appointments WHERE patient_id=? AND date=? AND time=? AND status!='Cancelled'", (pid, d, t), True):
        return "You already have an appointment at that time."
    p = q("SELECT name FROM users WHERE id=?", (pid,), True)
    ex("INSERT INTO appointments(patient_id,doctor_id,date,time,reason) VALUES(?,?,?,?,?)", (pid, did, d, t, reason))
    notify(pid, f"Appointment confirmed with {doc['name']} on {d} at {t}.")
    notify(did, f"New appointment: {p['name']} on {d} at {t}.")
    rem = when - timedelta(hours=1)
    if rem > datetime.now():
        ex("INSERT INTO reminders(user_id,kind,text,remind_at) VALUES(?,?,?,?)",
           (pid, "Appointment", f"Appointment with {doc['name']} at {t} on {d}", rem.strftime("%Y-%m-%d %H:%M")))
    return None

# ---------- public / auth ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("name", "email", "mobile", "password", "role", "department")}
        f["email"] = f["email"].lower()
        if not f["name"]: err = "Name is required."
        elif not re.fullmatch(EMAIL_RE, f["email"]): err = "Enter a valid email address."
        elif not re.fullmatch(PHONE_RE, f["mobile"]): err = "Mobile number must be 10 digits."
        elif len(f["password"]) < 6: err = "Password must be at least 6 characters."
        elif f["role"] not in ("student", "faculty"): err = "Choose student or faculty."
        elif q("SELECT 1 FROM users WHERE email=? OR mobile=?", (f["email"], f["mobile"]), True):
            err = "Email or mobile is already registered."
        else: err = None
        if err: flash(err, "error")
        else:
            ex("INSERT INTO users(name,email,mobile,password,role,department) VALUES(?,?,?,?,?,?)",
               (f["name"], f["email"], f["mobile"], generate_password_hash(f["password"]), f["role"], f["department"]))
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ident = request.form.get("ident", "").strip().lower()
        u = q("SELECT * FROM users WHERE (email=? OR mobile=?) AND active=1", (ident, ident), True)
        if u and check_password_hash(u["password"], request.form.get("password", "")):
            session.clear(); session["uid"] = u["id"]
            return redirect(url_for("dashboard"))
        flash("Invalid login details.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); flash("Logged out.", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required()
def dashboard():
    u = g.user
    if u["role"] == "doctor": return redirect(url_for("staff"))
    if u["role"] == "admin": return redirect(url_for("admin"))
    html = render_template(
        "dashboard.html",
        notes=q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 10", (u["id"],)),
        appts=q("""SELECT a.*, d.name doctor FROM appointments a JOIN users d ON d.id=a.doctor_id
                   WHERE a.patient_id=? AND a.date>=? AND a.status='Confirmed' ORDER BY a.date,a.time""",
                (u["id"], date.today().isoformat())),
        reqs=q("SELECT * FROM requests WHERE user_id=? ORDER BY id DESC LIMIT 5", (u["id"],)),
        ann=q("SELECT * FROM announcements ORDER BY id DESC LIMIT 3"))
    ex("UPDATE notifications SET seen=1 WHERE user_id=?", (u["id"],))
    return html

@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    u = g.user
    if request.method == "POST":
        f = request.form
        mobile, newpw = f.get("mobile", "").strip(), f.get("new_password", "")
        if not f.get("name", "").strip(): flash("Name is required.", "error")
        elif not re.fullmatch(PHONE_RE, mobile): flash("Mobile number must be 10 digits.", "error")
        elif q("SELECT 1 FROM users WHERE mobile=? AND id!=?", (mobile, u["id"]), True):
            flash("That mobile number is already in use.", "error")
        elif newpw and len(newpw) < 6: flash("New password must be at least 6 characters.", "error")
        else:
            ex("UPDATE users SET name=?,mobile=?,department=?,blood_group=?,emergency_contact=?,notes=? WHERE id=?",
               (f["name"].strip(), mobile, f.get("department", "").strip(), f.get("blood_group", "").strip(),
                f.get("emergency_contact", "").strip(), f.get("notes", "").strip(), u["id"]))
            if newpw: ex("UPDATE users SET password=? WHERE id=?", (generate_password_hash(newpw), u["id"]))
            flash("Profile updated.", "success"); return redirect(url_for("profile"))
    return render_template("profile.html", u=q("SELECT * FROM users WHERE id=?", (u["id"],), True))

# ---------- patient features ----------
@app.route("/guidance", methods=["GET", "POST"])
@login_required("student", "faculty")
def guidance():
    tips = urgent = None; text = ""
    if request.method == "POST":
        text = request.form.get("symptoms", "").strip()
        if not text: flash("Enter your symptoms.", "error")
        else: tips, urgent = guidance_for(text)
    return render_template("guidance.html", tips=tips, urgent=urgent, text=text)

@app.route("/appointments", methods=["GET", "POST"])
@login_required("student", "faculty")
def appointments():
    u = g.user
    if request.method == "POST":
        try: did = int(request.form.get("doctor_id", 0))
        except ValueError: did = 0
        err = book_appt(u["id"], did, request.form.get("date", ""), request.form.get("time", ""),
                        request.form.get("reason", "").strip())
        flash(err or "Appointment booked and confirmed.", "error" if err else "success")
        return redirect(url_for("appointments"))
    return render_template("appointments.html",
        doctors=q("SELECT * FROM users WHERE role='doctor' AND active=1 ORDER BY name"),
        rows=q("""SELECT a.*, d.name doctor FROM appointments a JOIN users d ON d.id=a.doctor_id
                  WHERE a.patient_id=? ORDER BY a.date DESC, a.time DESC""", (u["id"],)))

@app.route("/appointments/<int:aid>/<action>", methods=["POST"])
@login_required()
def appt_action(aid, action):
    u = g.user
    a = q("SELECT * FROM appointments WHERE id=?", (aid,), True)
    if (not a or action not in ("cancel", "complete") or u["id"] not in (a["patient_id"], a["doctor_id"])
            or (action == "complete" and u["id"] != a["doctor_id"])):
        abort(403)
    if a["status"] != "Confirmed":
        flash("Only confirmed appointments can be changed.", "error")
    else:
        status = "Cancelled" if action == "cancel" else "Completed"
        ex("UPDATE appointments SET status=? WHERE id=?", (status, aid))
        other = a["doctor_id"] if u["id"] == a["patient_id"] else a["patient_id"]
        notify(other, f"Appointment on {a['date']} at {a['time']} was {status.lower()}.")
        flash(f"Appointment {status.lower()}.", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/request", methods=["GET", "POST"])
@login_required("student", "faculty")
def request_help():
    u = g.user
    if request.method == "POST":
        f = request.form
        symptoms, location, contact = (f.get(k, "").strip() for k in ("symptoms", "location", "contact"))
        emergency = 1 if f.get("emergency") else 0
        err = validate_request(symptoms, location, contact)
        if err: flash(err, "error")
        else:
            rid = ex("INSERT INTO requests(user_id,symptoms,details,location,contact,emergency,created) VALUES(?,?,?,?,?,?,?)",
                     (u["id"], symptoms, f.get("details", "").strip(), location, contact, emergency, now()))
            for d in q("SELECT id FROM users WHERE role='doctor' AND active=1"):
                notify(d["id"], (f"EMERGENCY alert from {u['name']} at {location}. Contact {contact}." if emergency
                                 else f"New medical request #{rid} from {u['name']}."))
            notify(u["id"], f"Your request #{rid} was submitted. Medical staff have been notified.")
            flash("Alert submitted. Medical staff have been notified." if emergency
                  else "Request submitted. Medical staff will review it.", "success")
            return redirect(url_for("request_help"))
    return render_template("request.html",
        rows=q("SELECT * FROM requests WHERE user_id=? ORDER BY id DESC", (u["id"],)))

@app.route("/records")
@login_required("student", "faculty")
def records():
    return render_template("records.html", rows=q(
        """SELECT r.*, d.name doctor FROM records r JOIN users d ON d.id=r.doctor_id
           WHERE r.patient_id=? ORDER BY r.id DESC""", (g.user["id"],)))

@app.route("/reminders", methods=["GET", "POST"])
@login_required("student", "faculty")
def reminders():
    u = g.user
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        when = request.form.get("remind_at", "").replace("T", " ")[:16]
        kind = request.form.get("kind", "Medicine")
        try: datetime.strptime(when, "%Y-%m-%d %H:%M"); ok = True
        except ValueError: ok = False
        if not text or not ok or kind not in ("Medicine", "Appointment"):
            flash("Enter a reminder text and a valid date and time.", "error")
        else:
            ex("INSERT INTO reminders(user_id,kind,text,remind_at) VALUES(?,?,?,?)", (u["id"], kind, text, when))
            flash("Reminder saved.", "success")
        return redirect(url_for("reminders"))
    return render_template("reminders.html",
        rows=q("SELECT * FROM reminders WHERE user_id=? ORDER BY remind_at", (u["id"],)))

@app.route("/reminders/<int:rid>/delete", methods=["POST"])
@login_required("student", "faculty")
def reminder_delete(rid):
    ex("DELETE FROM reminders WHERE id=? AND user_id=?", (rid, g.user["id"]))
    return redirect(url_for("reminders"))

@app.route("/api/due")
def api_due():
    """Polled by the browser; sends reminders whose scheduled time has arrived."""
    u = me()
    if not u: return jsonify([])
    due = q("SELECT * FROM reminders WHERE user_id=? AND sent=0 AND remind_at<=?", (u["id"], now()))
    out = []
    for r in due:
        ex("UPDATE reminders SET sent=1 WHERE id=?", (r["id"],))
        notify(u["id"], f"Reminder ({r['kind']}): {r['text']}")
        out.append({"kind": r["kind"], "text": r["text"]})
    return jsonify(out)

@app.route("/feedback", methods=["POST"])
@login_required("student", "faculty")
def feedback():
    msg = request.form.get("message", "").strip()
    if msg:
        ex("INSERT INTO feedback(user_id,message,created) VALUES(?,?,?)", (g.user["id"], msg, now()))
        flash("Thank you for your feedback.", "success")
    else: flash("Feedback cannot be empty.", "error")
    return redirect(url_for("dashboard"))

# ---------- medical staff ----------
@app.route("/staff")
@login_required("doctor")
def staff():
    u = g.user
    html = render_template("staff.html",
        reqs=q("""SELECT r.*, p.name pname FROM requests r JOIN users p ON p.id=r.user_id
                  ORDER BY (r.status IN ('Immediate Assistance Provided','Advice Given','Resolved')), r.emergency DESC, r.id DESC"""),
        appts=q("""SELECT a.*, p.name pname FROM appointments a JOIN users p ON p.id=a.patient_id
                   WHERE a.doctor_id=? AND a.date>=? AND a.status='Confirmed' ORDER BY a.date,a.time""",
                (u["id"], date.today().isoformat())),
        notes=q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 8", (u["id"],)))
    ex("UPDATE notifications SET seen=1 WHERE user_id=?", (u["id"],))
    return html

@app.route("/staff/request/<int:rid>", methods=["GET", "POST"])
@login_required("doctor")
def staff_request(rid):
    r = q("""SELECT r.*, p.name pname, p.email, p.mobile, p.blood_group, p.notes pnotes, p.emergency_contact econtact
             FROM requests r JOIN users p ON p.id=r.user_id WHERE r.id=?""", (rid,), True)
    if not r: abort(404)
    if request.method == "POST":
        f = request.form
        status = f.get("status", "")
        advice, dx, rx = (f.get(k, "").strip() for k in ("advice", "diagnosis", "prescription"))
        err = None
        if status not in STATUSES: err = "Choose a valid status."
        elif f.get("appt_date") and f.get("appt_time"):
            err = book_appt(r["user_id"], g.user["id"], f["appt_date"], f["appt_time"], "Follow-up: " + r["symptoms"][:60])
        if err: flash(err, "error")
        else:
            ex("UPDATE requests SET status=?, advice=? WHERE id=?", (status, advice, rid))
            if dx or rx:
                ex("INSERT INTO records(patient_id,doctor_id,request_id,diagnosis,prescription,created) VALUES(?,?,?,?,?,?)",
                   (r["user_id"], g.user["id"], rid, dx, rx, now()))
            notify(r["user_id"], f"Your request #{rid} is now '{status}'." + (f" Advice: {advice}" if advice else ""))
            flash("Request updated.", "success"); return redirect(url_for("staff"))
    return render_template("staff_request.html", r=r, history=q(
        """SELECT rc.*, d.name doctor FROM records rc JOIN users d ON d.id=rc.doctor_id
           WHERE rc.patient_id=? ORDER BY rc.id DESC""", (r["user_id"],)))

# ---------- admin ----------
@app.route("/admin")
@login_required("admin")
def admin():
    stats = {"Students / faculty": count("SELECT COUNT(*) FROM users WHERE role IN ('student','faculty')"),
             "Doctors": count("SELECT COUNT(*) FROM users WHERE role='doctor'"),
             "Appointments": count("SELECT COUNT(*) FROM appointments"),
             "Medical requests": count("SELECT COUNT(*) FROM requests"),
             "Emergencies": count("SELECT COUNT(*) FROM requests WHERE emergency=1"),
             "Pending requests": count("SELECT COUNT(*) FROM requests WHERE status='Pending'")}
    return render_template("admin.html", stats=stats,
        users=q("SELECT * FROM users ORDER BY role, name"),
        fb=q("SELECT f.*, u.name FROM feedback f JOIN users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT 10"))

@app.route("/admin/user/<int:uid>/<action>", methods=["POST"])
@login_required("admin")
def admin_user(uid, action):
    t = q("SELECT * FROM users WHERE id=?", (uid,), True)
    if not t or t["role"] == "admin" or action not in ("toggle", "delete"): abort(403)
    if action == "toggle":
        ex("UPDATE users SET active=1-active WHERE id=?", (uid,)); flash("User status changed.", "success")
    else:
        for tbl, col in [("appointments", "patient_id"), ("appointments", "doctor_id"), ("requests", "user_id"),
                         ("records", "patient_id"), ("records", "doctor_id"), ("notifications", "user_id"),
                         ("reminders", "user_id"), ("feedback", "user_id"), ("users", "id")]:
            db().execute(f"DELETE FROM {tbl} WHERE {col}=?", (uid,))
        db().commit(); flash("User deleted.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/doctor", methods=["POST"])
@login_required("admin")
def admin_doctor():
    f = {k: request.form.get(k, "").strip() for k in ("name", "email", "mobile", "password", "department")}
    f["email"] = f["email"].lower()
    if not f["name"] or not re.fullmatch(EMAIL_RE, f["email"]) or not re.fullmatch(PHONE_RE, f["mobile"]) or len(f["password"]) < 6:
        flash("Enter name, valid email, 10-digit mobile and a password of 6+ characters.", "error")
    elif q("SELECT 1 FROM users WHERE email=? OR mobile=?", (f["email"], f["mobile"]), True):
        flash("Email or mobile is already registered.", "error")
    else:
        ex("INSERT INTO users(name,email,mobile,password,role,department) VALUES(?,?,?,?,'doctor',?)",
           (f["name"], f["email"], f["mobile"], generate_password_hash(f["password"]), f["department"]))
        flash("Doctor account created.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/announce", methods=["POST"])
@login_required("admin")
def admin_announce():
    msg = request.form.get("message", "").strip()
    if msg:
        ex("INSERT INTO announcements(message,created) VALUES(?,?)", (msg, now()))
        for u in q("SELECT id FROM users WHERE role!='admin' AND active=1"): notify(u["id"], "Announcement: " + msg)
        flash("Announcement sent.", "success")
    else: flash("Announcement cannot be empty.", "error")
    return redirect(url_for("admin"))

@app.route("/admin/config", methods=["POST"])
@login_required("admin")
def admin_config():
    for k in ("emergency_number", "ambulance", "health_centre"):
        v = request.form.get(k, "").strip()
        if v: ex("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
    flash("Emergency contact information updated.", "success")
    return redirect(url_for("admin"))

init_db()

if __name__ == "__main__":
    app.run(debug=True)
