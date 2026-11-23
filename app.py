"""KM - a small knowledge base for training batches.

Run:     python app.py              (serves on http://0.0.0.0:8080)
Backup:  python app.py backup D:\\km-backup
"""
import html as htmllib
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("KM_DATA", BASE / "data"))
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "km.db"
PORT = int(os.environ.get("KM_PORT", 8080))
ADMIN_IDLE = 2 * 60 * 60  # admin mode drops back after 2h idle (shared PCs)
VIEW_INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}  # other uploads download
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS pages(
  id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id),
  title TEXT NOT NULL, html TEXT NOT NULL DEFAULT '', updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS revisions(
  id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL, title TEXT, html TEXT, saved_by TEXT, saved_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(title, body);

CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created_at TEXT);
CREATE TABLE IF NOT EXISTS users(
  name TEXT PRIMARY KEY, display_name TEXT, batch_id TEXT REFERENCES batches(id),
  is_admin INTEGER NOT NULL DEFAULT 0, pin_hash TEXT);
-- subject is a username ("0521nic") or a whole batch ("batch:0521")
CREATE TABLE IF NOT EXISTS perms(folder_id INTEGER NOT NULL, subject TEXT NOT NULL, PRIMARY KEY(folder_id, subject));

CREATE TABLE IF NOT EXISTS aar_entries(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
  author TEXT NOT NULL, created_at TEXT NOT NULL, edited_by TEXT, edited_at TEXT);
CREATE TABLE IF NOT EXISTS aar_history(
  id INTEGER PRIMARY KEY, entry_id INTEGER, title TEXT, body TEXT, written_by TEXT, written_at TEXT, replaced_at TEXT);

-- AAR rules enforced by the database itself, not just the app:
CREATE TRIGGER IF NOT EXISTS aar_no_delete BEFORE DELETE ON aar_entries
  BEGIN SELECT RAISE(ABORT, 'AAR entries cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS aar_keep_author BEFORE UPDATE OF author, created_at ON aar_entries
  BEGIN SELECT RAISE(ABORT, 'AAR author and date cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS aar_keep_history BEFORE UPDATE ON aar_entries BEGIN
  INSERT INTO aar_history(entry_id, title, body, written_by, written_at, replaced_at)
  VALUES (OLD.id, OLD.title, OLD.body, COALESCE(OLD.edited_by, OLD.author),
          COALESCE(OLD.edited_at, OLD.created_at), strftime('%Y-%m-%d %H:%M', 'now', 'localtime'));
END;
CREATE TRIGGER IF NOT EXISTS aar_history_no_delete BEFORE DELETE ON aar_history
  BEGIN SELECT RAISE(ABORT, 'AAR history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS aar_history_no_update BEFORE UPDATE ON aar_history
  BEGIN SELECT RAISE(ABORT, 'AAR history cannot be changed'); END;
"""


def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_data():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
    key = DATA / "secret.key"
    if not key.exists():
        key.write_bytes(os.urandom(32))
    return key.read_bytes()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=init_data(),
    SESSION_COOKIE_SAMESITE="Lax",  # blocks other websites from posting to KM with your cookie
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    MAX_CONTENT_LENGTH=500 * 1024 * 1024,
)


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c:
        c.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- identity & permissions ----------

@app.before_request
def load_user():
    g.user = None
    if request.endpoint == "static":
        return
    if not db().execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
        return None if request.endpoint == "setup" else redirect(url_for("setup"))
    u = db().execute("SELECT * FROM users WHERE name=?", (session.get("user"),)).fetchone()
    if u and u["is_admin"]:
        if time.time() - session.get("admin_at", 0) > ADMIN_IDLE:
            u = None
            session.pop("user", None)
        else:
            session["admin_at"] = time.time()
    g.user = u
    if not u and request.endpoint not in ("who", "setup"):
        return redirect(url_for("who", next=request.full_path))


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: blob:; "
        "font-src 'self' data: https://cdn.jsdelivr.net; frame-src https://embed.diagrams.net; "
        "connect-src 'self' https://cdn.jsdelivr.net")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def is_admin():
    return bool(g.user and g.user["is_admin"])


def can_edit(folder_id):
    if is_admin():
        return True
    return db().execute(
        "SELECT 1 FROM perms WHERE folder_id=? AND subject IN (?, ?)",
        (folder_id, g.user["name"], f"batch:{g.user['batch_id']}")).fetchone() is not None


def require_edit(folder_id):
    if not can_edit(folder_id):
        abort(403, "You don't have edit rights for this folder. Ask an admin.")


def require_admin():
    if not is_admin():
        abort(403, "Admins only.")


app.jinja_env.globals.update(can_edit=can_edit, is_admin=is_admin)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db().execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
        return redirect(url_for("home"))
    if request.method == "POST":
        name, pin = clean_name(request.form["name"]), request.form["pin"]
        if not name or len(pin) < 4:
            flash("Pick a name (letters/numbers) and a PIN of at least 4 characters.")
        else:
            db().execute("INSERT INTO users(name, display_name, is_admin, pin_hash) VALUES (?,?,1,?)",
                         (name, request.form["name"].strip(), generate_password_hash(pin)))
            db().commit()
            return redirect(url_for("who"))
    return render_template("setup.html")


@app.route("/who", methods=["GET", "POST"])
def who():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE name=?", (request.form.get("name"),)).fetchone()
        if not u:
            flash("Pick your name from the list.")
        elif u["is_admin"] and not check_password_hash(u["pin_hash"], request.form.get("pin", "")):
            flash("Wrong PIN.")
        else:
            session.clear()
            session["user"] = u["name"]
            session["admin_at"] = time.time()
            session.permanent = not u["is_admin"]  # trainees stay remembered; admins don't
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
    users = db().execute(
        "SELECT * FROM users ORDER BY is_admin DESC, batch_id DESC, name").fetchall()
    return render_template("who.html", users=users)


# ---------- folders & pages ----------

@app.route("/")
def home():
    folders = db().execute("""SELECT f.*, COUNT(p.id) AS n FROM folders f
                              LEFT JOIN pages p ON p.folder_id=f.id GROUP BY f.id ORDER BY f.name""").fetchall()
    recent = db().execute("""SELECT p.id, p.title, p.updated_by, p.updated_at, f.name AS folder
                             FROM pages p JOIN folders f ON f.id=p.folder_id
                             ORDER BY p.updated_at DESC LIMIT 10""").fetchall()
    return render_template("home.html", folders=folders, recent=recent)


@app.post("/folders")
def new_folder():
    require_admin()
    name = request.form["name"].strip()
    if name:
        try:
            db().execute("INSERT INTO folders(name) VALUES (?)", (name,))
            db().commit()
        except sqlite3.IntegrityError:
            flash(f"A folder called {name!r} already exists.")
    return redirect(url_for("home"))


def get_or_404(sql, *args):
    row = db().execute(sql, args).fetchone()
    if not row:
        abort(404)
    return row


@app.route("/folder/<int:fid>")
def folder(fid):
    f = get_or_404("SELECT * FROM folders WHERE id=?", fid)
    pages = db().execute("SELECT * FROM pages WHERE folder_id=? ORDER BY title", (fid,)).fetchall()
    editors = [r["subject"] for r in db().execute(
        "SELECT subject FROM perms WHERE folder_id=? ORDER BY subject", (fid,))]
    return render_template("folder.html", folder=f, pages=pages, editors=editors)


@app.route("/page/<int:pid>")
def page(pid):
    p = get_or_404("SELECT p.*, f.name AS folder FROM pages p JOIN folders f ON f.id=p.folder_id WHERE p.id=?", pid)
    return render_template("page.html", page=p, rev=None)


def to_text(html):
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", html))


def save_page(pid, folder_id, title, html):
    d, t, who_ = db(), now(), g.user["name"]
    if pid:
        d.execute("UPDATE pages SET title=?, html=?, updated_by=?, updated_at=? WHERE id=?",
                  (title, html, who_, t, pid))
    else:
        pid = d.execute("INSERT INTO pages(folder_id, title, html, updated_by, updated_at) VALUES (?,?,?,?,?)",
                        (folder_id, title, html, who_, t)).lastrowid
    d.execute("INSERT INTO revisions(page_id, title, html, saved_by, saved_at) VALUES (?,?,?,?,?)",
              (pid, title, html, who_, t))
    d.execute("DELETE FROM pages_fts WHERE rowid=?", (pid,))
    d.execute("INSERT INTO pages_fts(rowid, title, body) VALUES (?,?,?)", (pid, title, to_text(html)))
    d.commit()
    return pid


@app.route("/folder/<int:fid>/new", methods=["GET", "POST"])
@app.route("/page/<int:pid>/edit", methods=["GET", "POST"])
def edit(fid=None, pid=None):
    p = get_or_404("SELECT * FROM pages WHERE id=?", pid) if pid else None
    fid = p["folder_id"] if p else get_or_404("SELECT id FROM folders WHERE id=?", fid)["id"]
    require_edit(fid)
    if request.method == "POST":
        title = request.form["title"].strip() or "Untitled"
        pid = save_page(pid, fid, title, request.form.get("html", ""))
        return redirect(url_for("page", pid=pid))
    return render_template("edit.html", page=p, folder_id=fid)


@app.post("/page/<int:pid>/delete")
def delete_page(pid):
    require_admin()
    p = get_or_404("SELECT * FROM pages WHERE id=?", pid)
    db().execute("DELETE FROM pages_fts WHERE rowid=?", (pid,))
    db().execute("DELETE FROM pages WHERE id=?", (pid,))  # revisions are kept
    db().commit()
    flash(f"Deleted {p['title']!r}.")
    return redirect(url_for("folder", fid=p["folder_id"]))


@app.route("/page/<int:pid>/history")
def history(pid):
    p = get_or_404("SELECT * FROM pages WHERE id=?", pid)
    revs = db().execute("SELECT id, title, saved_by, saved_at FROM revisions WHERE page_id=? ORDER BY id DESC",
                        (pid,)).fetchall()
    return render_template("history.html", page=p, revs=revs)


@app.route("/revision/<int:rid>")
def revision(rid):
    r = get_or_404("SELECT * FROM revisions WHERE id=?", rid)
    p = get_or_404("SELECT p.*, f.name AS folder FROM pages p JOIN folders f ON f.id=p.folder_id WHERE p.id=?",
                   r["page_id"])
    return render_template("page.html", page=p, rev=r)


@app.post("/revision/<int:rid>/restore")
def restore(rid):
    r = get_or_404("SELECT * FROM revisions WHERE id=?", rid)
    p = get_or_404("SELECT * FROM pages WHERE id=?", r["page_id"])
    require_edit(p["folder_id"])
    save_page(p["id"], p["folder_id"], r["title"], r["html"])
    flash(f"Restored the version from {r['saved_at']}.")
    return redirect(url_for("page", pid=p["id"]))


# ---------- uploads (drag & drop, draw.io) ----------

@app.post("/upload")
def upload():
    require_edit(request.form.get("folder_id", type=int))
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400, "No file.")
    name = secure_filename(f.filename) or "file"
    sub = datetime.now().strftime("%Y/%m")
    (UPLOADS / sub).mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex[:8]}-{name}"
    f.save(UPLOADS / sub / stored)
    return {"url": f"/uploads/{sub}/{stored}", "name": f.filename,
            "image": Path(name).suffix.lower() in IMAGES}


@app.route("/uploads/<path:path>")
def uploads(path):
    inline = Path(path).suffix.lower() in VIEW_INLINE
    return send_from_directory(UPLOADS, path, as_attachment=not inline)


# ---------- AAR (add-only; admins may edit, nobody may delete) ----------

@app.route("/aar", methods=["GET", "POST"])
def aar():
    if request.method == "POST":
        title, body = request.form["title"].strip(), request.form["body"].strip()
        if title and body:
            db().execute("INSERT INTO aar_entries(title, body, author, created_at) VALUES (?,?,?,?)",
                         (title, body, g.user["name"], now()))
            db().commit()
            return redirect(url_for("aar"))
        flash("An AAR entry needs a title and a body.")
    entries = db().execute("SELECT * FROM aar_entries ORDER BY id DESC").fetchall()
    return render_template("aar.html", entries=entries)


@app.route("/aar/<int:eid>", methods=["GET", "POST"])
def aar_entry(eid):
    e = get_or_404("SELECT * FROM aar_entries WHERE id=?", eid)
    if request.method == "POST":
        require_admin()
        db().execute("UPDATE aar_entries SET title=?, body=?, edited_by=?, edited_at=? WHERE id=?",
                     (request.form["title"].strip() or e["title"], request.form["body"].strip() or e["body"],
                      g.user["name"], now(), eid))
        db().commit()
        return redirect(url_for("aar_entry", eid=eid))
    hist = db().execute("SELECT * FROM aar_history WHERE entry_id=? ORDER BY id DESC", (eid,)).fetchall()
    return render_template("aar_entry.html", e=e, hist=hist)


# ---------- search ----------

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    pages, aars = [], []
    if q:
        fts = " ".join('"' + t.replace('"', '""') + '"' for t in q.split())
        pages = db().execute("""SELECT p.id, p.title, f.name AS folder,
                                       snippet(pages_fts, 1, '[[', ']]', '…', 20) AS snip
                                FROM pages_fts JOIN pages p ON p.id=pages_fts.rowid
                                JOIN folders f ON f.id=p.folder_id
                                WHERE pages_fts MATCH ? ORDER BY rank LIMIT 50""", (fts,)).fetchall()
        like = f"%{q}%"
        aars = db().execute("SELECT * FROM aar_entries WHERE title LIKE ? OR body LIKE ? ORDER BY id DESC LIMIT 50",
                            (like, like)).fetchall()
    return render_template("search.html", q=q, pages=pages, aars=aars)


# ---------- admin ----------

def clean_name(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


@app.route("/admin")
def admin():
    require_admin()
    d = db()
    batches = d.execute("""SELECT b.id, b.created_at, GROUP_CONCAT(u.name, ', ') AS members
                           FROM batches b LEFT JOIN users u ON u.batch_id=b.id
                           GROUP BY b.id ORDER BY b.created_at DESC""").fetchall()
    folders = d.execute("SELECT * FROM folders ORDER BY name").fetchall()
    perms = {}
    for r in d.execute("SELECT * FROM perms ORDER BY subject"):
        perms.setdefault(r["folder_id"], []).append(r["subject"])
    users = d.execute("SELECT name FROM users WHERE is_admin=0 ORDER BY batch_id DESC, name").fetchall()
    admins = d.execute("SELECT name FROM users WHERE is_admin=1 ORDER BY name").fetchall()
    return render_template("admin.html", batches=batches, folders=folders, perms=perms,
                           users=users, admins=admins)


@app.post("/admin/batch")
def admin_batch():
    require_admin()
    batch = clean_name(request.form["batch"])
    if not batch:
        flash("Batch number must be letters/numbers, e.g. 0521.")
        return redirect(url_for("admin"))
    d = db()
    d.execute("INSERT OR IGNORE INTO batches(id, created_at) VALUES (?,?)", (batch, now()))
    added, skipped = [], []
    for raw in re.split(r"[,\n]", request.form["names"]):
        n = clean_name(raw)
        if not n:
            continue
        username = batch + n
        cur = d.execute("INSERT OR IGNORE INTO users(name, display_name, batch_id) VALUES (?,?,?)",
                        (username, raw.strip(), batch))
        (added if cur.rowcount else skipped).append(username)
    d.commit()
    flash(f"Batch {batch}: added {', '.join(added) or 'nobody'}."
          + (f" Already existed: {', '.join(skipped)}." if skipped else ""))
    return redirect(url_for("admin"))


@app.post("/admin/perm")
def admin_perm():
    require_admin()
    fid, subject = request.form.get("folder_id", type=int), request.form.get("subject", "")
    if request.form.get("action") == "revoke":
        db().execute("DELETE FROM perms WHERE folder_id=? AND subject=?", (fid, subject))
    elif subject:
        db().execute("INSERT OR IGNORE INTO perms(folder_id, subject) VALUES (?,?)", (fid, subject))
    db().commit()
    return redirect(url_for("admin") + "#perms")


@app.post("/admin/admins")
def admin_admins():
    require_admin()
    name, pin = clean_name(request.form["name"]), request.form["pin"]
    if not name or len(pin) < 4:
        flash("Admin needs a name and a PIN of at least 4 characters.")
    else:
        db().execute("""INSERT INTO users(name, display_name, is_admin, pin_hash) VALUES (?,?,1,?)
                        ON CONFLICT(name) DO UPDATE SET is_admin=1, pin_hash=excluded.pin_hash""",
                     (name, request.form["name"].strip(), generate_password_hash(pin)))
        db().commit()
        flash(f"{name} is an admin with the new PIN.")
    return redirect(url_for("admin"))


# ---------- run / backup ----------

def backup(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with connect() as src, sqlite3.connect(dest / f"km-{datetime.now():%Y%m%d}.db") as out:
        src.backup(out)  # consistent copy even while the server is running
    shutil.copytree(UPLOADS, dest / "uploads", dirs_exist_ok=True)
    print(f"Backed up to {dest}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["backup"]:
        backup(sys.argv[2] if len(sys.argv) > 2 else BASE / "backup")
    else:
        import logging
        from waitress import serve
        if sys.stdout is None:  # started by the scheduled task (pythonw): no console, so log to a file
            sys.stdout = sys.stderr = open(DATA / "km.log", "a", buffering=1)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        print(f"KM running on http://0.0.0.0:{PORT}  (other PCs: http://<this PC's IP>:{PORT})")
        serve(app, host="0.0.0.0", port=PORT, threads=8)
