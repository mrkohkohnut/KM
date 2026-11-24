"""KM - a small knowledge base for training groups.

Run:     python app.py              (serves on http://0.0.0.0:8080)
Backup:  python app.py backup D:\\km-backup
"""
import base64
import hashlib
import hmac
import html as htmllib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from itertools import groupby
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("KM_DATA", BASE / "data"))
UPLOADS = Path(os.environ.get("KM_FILES", DATA / "uploads"))  # all files, laid out as <Folder>/<Section>/<name>
OLD_UPLOADS = DATA / "uploads"  # where files lived before KM_FILES existed
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
  title TEXT NOT NULL, html TEXT NOT NULL DEFAULT '', updated_by TEXT, updated_at TEXT,
  is_readme INTEGER NOT NULL DEFAULT 0);  -- 1 = the folder's overview page
CREATE TABLE IF NOT EXISTS revisions(
  id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL, title TEXT, html TEXT, saved_by TEXT, saved_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(title, body);

CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created_at TEXT);
CREATE TABLE IF NOT EXISTS users(
  name TEXT PRIMARY KEY, display_name TEXT, batch_id TEXT REFERENCES batches(id),
  is_admin INTEGER NOT NULL DEFAULT 0, pin_hash TEXT);
-- trainees belong to a group (stored as "batches"); usernames are group-name, e.g. "alpha-nic", and record who
-- wrote or changed what (ranks are not part of them; they go in a folder's Done by)
-- subject is a username ("alpha-nic") or a whole group ("batch:alpha")
CREATE TABLE IF NOT EXISTS perms(folder_id INTEGER NOT NULL, subject TEXT NOT NULL, PRIMARY KEY(folder_id, subject));

CREATE TABLE IF NOT EXISTS aar_entries(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
  author TEXT NOT NULL, created_at TEXT NOT NULL, edited_by TEXT, edited_at TEXT);
-- Resources: files (e.g. packet captures) and links listed beside a folder's overview
CREATE TABLE IF NOT EXISTS resources(
  id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id), kind TEXT NOT NULL,
  title TEXT NOT NULL, url TEXT NOT NULL, size INTEGER, added_by TEXT, added_at TEXT);
-- Documentation and Slides: office files opened in OnlyOffice; every save is a new version
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id), kind TEXT NOT NULL,
  name TEXT NOT NULL, url TEXT NOT NULL, size INTEGER, version INTEGER NOT NULL DEFAULT 1, updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS document_versions(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, version INTEGER, url TEXT, size INTEGER, saved_by TEXT, saved_at TEXT);
CREATE TABLE IF NOT EXISTS reflections(
  id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id), author TEXT NOT NULL,
  html TEXT NOT NULL, created_at TEXT, updated_at TEXT);

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


# A folder's overview is one page made of these sections, each edited on its own from the folder page.
SECTIONS = {"background": "Background", "diagram": "Network diagram", "people": "Done by", "resources": "Links to resources"}


def sections_html(sections):
    return "\n".join(f'<section data-section="{k}"><h2>{title}</h2>\n{sections.get(k, "")}\n</section>'
                     for k, title in SECTIONS.items())


def overview_sections(html):
    """{section key: html} from an overview. Older overviews without sections are split on their <h2> headings."""
    found = re.findall(r'<section data-section="(\w+)">\s*(?:<h2>.*?</h2>)?(.*?)</section>', html, re.S)
    if found:
        return tidy_diagram({k: dict(found).get(k, "").strip() for k in SECTIONS})
    out, key = dict.fromkeys(SECTIONS, ""), "background"
    for chunk in re.split(r"(<h2>.*?</h2>)", html, flags=re.S):
        heading = re.fullmatch(r"<h2>(.*?)</h2>", chunk, re.S)
        if not heading:
            out[key] += chunk
            continue
        t = re.sub(r"<[^>]+>", "", heading.group(1)).strip().lower()
        key = ("background" if "background" in t else "diagram" if "diagram" in t else
               "people" if any(w in t for w in ("contact", "people", "done by", "team")) else "resources")
        if key == "resources" and t != "links to resources":
            out[key] += f"<h3>{heading.group(1)}</h3>"  # keep sub-headings such as Files or Artefacts
    return tidy_diagram({k: v.strip() for k, v in out.items()})


def diagram_only(html):
    """Just the draw.io diagram from some HTML: a sheets box (one picture per sheet plus the .drawio file),
    rebuilt from its parts so nothing else gets through, or older single diagram pictures."""
    box = re.search(r'<div class="drawio-sheets"(?: data-source="([^"<>]*)")?>(.*?)</div>', html, re.S)
    if box:
        sheets = re.findall(r'<figure data-sheet="([^"<>]*)"><img\b[^>]*?\bsrc="([^"<>]+)"', box.group(2))
        source = f' data-source="{box.group(1)}"' if box.group(1) else ""
        return f'<div class="drawio-sheets"{source}>' + "".join(
            f'<figure data-sheet="{name}"><img data-drawio="1" src="{src}" alt="{name or "Network diagram"}"></figure>'
            for name, src in sheets) + "</div>" if sheets else ""
    return "".join(f"<p>{img}</p>" for img in re.findall(r"<img\b[^>]*data-drawio[^>]*>", html))


def tidy_diagram(sections):
    """The Network diagram section holds only the draw.io diagram. .drawio files that older overviews kept
    there move to Links to resources (the pen offers them as a starting point); any text there is dropped."""
    files = re.findall(r'<p><a href="[^"]+\.drawio">.*?</p>', sections["diagram"], re.S)
    if files:
        sections["resources"] = (sections["resources"] + "\n<h3>Diagram files</h3>" + "".join(files)).strip()
    sections["diagram"] = diagram_only(sections["diagram"])
    return sections


README_TEMPLATE = sections_html({
    "background": "<p>What this folder covers, who it is for and why it matters.</p>",
})


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def to_text(html):
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", html))


def write_page(c, pid, folder_id, title, html, who, is_readme=0):
    """Create or update a page, keep a revision and refresh the search index."""
    t = now()
    if pid:
        c.execute("UPDATE pages SET title=?, html=?, updated_by=?, updated_at=? WHERE id=?", (title, html, who, t, pid))
    else:
        pid = c.execute("INSERT INTO pages(folder_id, title, html, updated_by, updated_at, is_readme) VALUES (?,?,?,?,?,?)",
                        (folder_id, title, html, who, t, is_readme)).lastrowid
    c.execute("INSERT INTO revisions(page_id, title, html, saved_by, saved_at) VALUES (?,?,?,?,?)",
              (pid, title, html, who, t))
    c.execute("DELETE FROM pages_fts WHERE rowid=?", (pid,))
    c.execute("INSERT INTO pages_fts(rowid, title, body) VALUES (?,?,?)", (pid, title, to_text(html)))
    return pid


def init_data():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        # upgrade databases created before folder overviews existed
        if "is_readme" not in [r["name"] for r in c.execute("PRAGMA table_info(pages)")]:
            c.execute("ALTER TABLE pages ADD COLUMN is_readme INTEGER NOT NULL DEFAULT 0")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_overview_per_folder ON pages(folder_id) WHERE is_readme=1")
        for r in c.execute("SELECT id FROM folders WHERE id NOT IN (SELECT folder_id FROM pages WHERE is_readme=1)").fetchall():
            write_page(c, None, r["id"], "Overview", README_TEMPLATE, "KM", is_readme=1)
    key = DATA / "secret.key"
    if not key.exists():
        key.write_bytes(os.urandom(32))
    return key.read_bytes()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=init_data(),
    SESSION_COOKIE_SAMESITE="Lax",  # blocks other websites from posting to KM with your cookie
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    MAX_CONTENT_LENGTH=2 * 1024 ** 3,  # 2 GB, e.g. packet captures
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


# ---------- identity & permissions ----------

@app.before_request
def load_user():
    g.user = None
    if request.endpoint in ("static", "onlyoffice_file", "onlyoffice_callback"):  # the latter two check a token instead
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
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "img-src 'self' data: blob:; font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        f" frame-src https://embed.diagrams.net {office_origin()}; "
        "connect-src 'self' https://cdn.jsdelivr.net")
    resp.headers["Content-Security-Policy"] = resp.headers["Content-Security-Policy"].replace(
        "script-src 'self'", f"script-src 'self' {office_origin()}")
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


SPINES = ["#007aff", "#ff9500", "#34c759", "#af52de", "#ff2d55", "#32ade6", "#5856d6", "#a2845e"]  # Apple system colours, one per folder


def when(s):
    """'2026-11-23 14:02' -> '23 Nov 2026, 14:02'"""
    return datetime.strptime(s, "%Y-%m-%d %H:%M").strftime("%d %b %Y, %H:%M").lstrip("0") if s else ""


app.jinja_env.globals.update(can_edit=can_edit, is_admin=is_admin,
                             spine=lambda fid: SPINES[(fid - 1) % len(SPINES)])
app.jinja_env.filters["when"] = when


def filesize(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


app.jinja_env.filters["filesize"] = filesize


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
    users = db().execute("SELECT * FROM users ORDER BY is_admin DESC, batch_id DESC, name").fetchall()
    groups = [(b, list(us)) for b, us in groupby(users, key=lambda u: u["batch_id"])]  # admins, then newest batch
    return render_template("who.html", groups=groups)


# ---------- folders & pages ----------

@app.route("/")
def home():
    folders = all_folders()
    recent = db().execute("""SELECT p.id, p.title, p.is_readme, p.updated_by, p.updated_at, f.name AS folder
                             FROM pages p JOIN folders f ON f.id=p.folder_id
                             WHERE p.is_readme=0 OR (SELECT COUNT(*) FROM revisions WHERE page_id=p.id) > 1
                             ORDER BY p.updated_at DESC LIMIT 8""").fetchall()  # skips untouched new overviews
    aars = db().execute("SELECT * FROM aar_entries ORDER BY id DESC LIMIT 4").fetchall()
    return render_template("home.html", folders=folders, recent=recent, aars=aars)


def all_folders():
    return db().execute("""SELECT f.*, (SELECT COUNT(*) FROM documents WHERE folder_id=f.id) AS n,
                           MAX((SELECT MAX(updated_at) FROM documents WHERE folder_id=f.id),
                               (SELECT MAX(updated_at) FROM pages WHERE folder_id=f.id)) AS last
                           FROM folders f ORDER BY f.name""").fetchall()


def explorer():
    """Every folder with its documents, slide decks, older pages and reflection count, for the left-hand tree."""
    folders = [dict(f, doc=[], slides=[], pages=[], reflections=0) for f in all_folders()]
    by_id = {f["id"]: f for f in folders}
    for d in db().execute("SELECT id, folder_id, kind, name FROM documents ORDER BY name COLLATE NOCASE"):
        by_id[d["folder_id"]][d["kind"]].append(d)
    for p in db().execute("SELECT id, folder_id, title FROM pages WHERE is_readme=0 ORDER BY title COLLATE NOCASE"):
        by_id[p["folder_id"]]["pages"].append(p)
    for r in db().execute("SELECT folder_id, COUNT(*) AS n FROM reflections GROUP BY folder_id"):
        by_id[r["folder_id"]]["reflections"] = r["n"]
    return folders


app.jinja_env.globals["explorer"] = explorer


@app.post("/folders")
def new_folder():
    require_admin()
    name = request.form["name"].strip()
    if name:
        try:
            fid = db().execute("INSERT INTO folders(name) VALUES (?)", (name,)).lastrowid
            write_page(db(), None, fid, "Overview", README_TEMPLATE, g.user["name"], is_readme=1)
            db().commit()
            return redirect(url_for("folder", fid=fid))
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
    readme = db().execute("SELECT * FROM pages WHERE folder_id=? AND is_readme=1", (fid,)).fetchone()
    pages = [dict(p, excerpt=to_text(p["html"]).strip()[:160]) for p in
             db().execute("SELECT * FROM pages WHERE folder_id=? AND is_readme=0 ORDER BY title", (fid,))]
    editors = [r["subject"] for r in db().execute(
        "SELECT subject FROM perms WHERE folder_id=? ORDER BY subject", (fid,))]
    documents = db().execute("SELECT * FROM documents WHERE folder_id=? ORDER BY name COLLATE NOCASE", (fid,)).fetchall()
    reflections = db().execute("SELECT * FROM reflections WHERE folder_id=? ORDER BY id", (fid,)).fetchall()
    resources = db().execute("SELECT * FROM resources WHERE folder_id=? ORDER BY kind, title COLLATE NOCASE", (fid,)).fetchall()
    return render_template("folder.html", resources=resources, folder=f, readme=readme, pages=pages, editors=editors,
                           folders=all_folders(), links=page_links(readme["html"]),
                           sections=overview_sections(readme["html"]), SECTIONS=SECTIONS, people_lines=people_lines,
                           people_html=people_html,
                           documents=documents, reflections=reflections, KINDS=KINDS)


# Done by: one person per line ("ME4A KOH JUNYANG NICHOLAS"), shown as a tidy list with initials and rank.
RANKS = {"REC", "PTE", "LCP", "CPL", "SGT", "SSG", "MSG", "SWO", "MWO", "SLTC", "OCT", "MID", "2LT", "LTA", "CPT",
         "MAJ", "LTC", "COL", "BG", "MG", "LG", "GEN", "DX", "MR", "MS", "MRS", "DR"}


def split_person(line):
    """("ME4A", "Koh Junyang Nicholas") from "ME4A KOH JUNYANG NICHOLAS" (ranks like ME4-2 too); rank is "" if there
    isn't one."""
    words = line.split()
    rank = ""
    if len(words) > 1 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,7}", words[0]) and (
            words[0].upper() in RANKS or re.search(r"\d", words[0])):
        rank, words = words[0].upper(), words[1:]
    name = " ".join(words)
    if name.isupper():  # names pasted in capitals read better in normal case
        name = " ".join(w.capitalize() for w in words)
    return rank, name


def people_lines(html):
    """The people in a Done by section as plain lines, whether saved as a people list or as older free text."""
    saved = re.findall(r'<li data-rank="([^"]*)">.*?<span class="who">(.*?)</span>', html, re.S)
    if saved:
        return [htmllib.unescape(f"{rank} {who}".strip()) for rank, who in saved]
    items = re.findall(r'<li[^>]*>(.*?)</li>', html, re.S) or re.split(r"</p>|<br\s*/?>|\n", html)
    lines = []
    for item in items:
        for part in htmllib.unescape(re.sub(r"<[^>]+>", " ", item)).split(","):
            if part.split():
                lines.append(" ".join(part.split()))
    return lines


def people_html(lines):
    """Each person with their rank in the bubble beside the name (initials if there is no rank)."""
    out = []
    for line in lines:
        rank, name = split_person(line.strip())
        if not name:
            continue
        words = [w for w in (re.sub(r"[^A-Za-z]", "", w) for w in name.split()) if w]
        badge = rank or ((words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper() if words else "?")
        out.append(f'<li data-rank="{htmllib.escape(rank)}"><span class="badge{" rank" if rank else ""}">{htmllib.escape(badge)}</span>'
                   f'<span class="who">{htmllib.escape(name)}</span></li>')
    return f'<ul class="people">{"".join(out)}</ul>' if out else ""


@app.post("/folder/<int:fid>/section/<key>")
def save_section(fid, key):
    """Saves one overview section in place (from the folder page); the other sections are left as they are."""
    if key not in SECTIONS:
        abort(404)
    require_edit(fid)
    readme = get_or_404("SELECT * FROM pages WHERE folder_id=? AND is_readme=1", fid)
    sections = overview_sections(readme["html"])
    html = request.form.get("html", "").strip()
    if key == "people" and "people" in request.form:  # Done by is edited as plain lines
        html = people_html([p for line in request.form["people"].splitlines() for p in line.split(",")])
    sections[key] = diagram_only(html) if key == "diagram" else html
    save_page(readme["id"], fid, "Overview", sections_html(sections))
    return {"html": sections[key]}


SECTION_VIEWS = {"doc": "Documentation", "slides": "Slides", "reflections": "Reflections", "pages": "Older pages"}


@app.route("/folder/<int:fid>/<section>")
def folder_section(fid, section):
    """Documentation, Slides, Reflections or Older pages of a folder, each on its own (opened from the left-hand tree)."""
    if section not in SECTION_VIEWS:
        abort(404)
    f = get_or_404("SELECT * FROM folders WHERE id=?", fid)
    documents = db().execute("SELECT * FROM documents WHERE folder_id=? ORDER BY name COLLATE NOCASE", (fid,)).fetchall()
    reflections = db().execute("SELECT * FROM reflections WHERE folder_id=? ORDER BY id", (fid,)).fetchall()
    pages = db().execute("SELECT * FROM pages WHERE folder_id=? AND is_readme=0 ORDER BY title", (fid,)).fetchall()
    return render_template("folder_section.html", folder=f, section=section, title=SECTION_VIEWS[section], kind=section,
                           documents=documents, reflections=reflections, pages=pages, KINDS=KINDS)


@app.route("/page/<int:pid>")
def page(pid):
    p = get_or_404("SELECT p.*, f.name AS folder FROM pages p JOIN folders f ON f.id=p.folder_id WHERE p.id=?", pid)
    if p["is_readme"]:
        return redirect(url_for("folder", fid=p["folder_id"]))
    return render_page(p, None)


def render_page(p, rev):
    siblings = db().execute("SELECT id, title FROM pages WHERE folder_id=? AND is_readme=0 ORDER BY title",
                            (p["folder_id"],)).fetchall()
    created = db().execute("SELECT saved_by, saved_at FROM revisions WHERE page_id=? ORDER BY id LIMIT 1",
                           (p["id"],)).fetchone()
    return render_template("page.html", page=p, rev=rev, siblings=siblings, created=created,
                           links=page_links(rev["html"] if rev else p["html"]))


def page_links(html):
    """Files and links in a page, for the side panel: [{href, label, kind}] with kind file/internal/web."""
    found = {}
    for href, label in re.findall(r'<a\s[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href = htmllib.unescape(href)
        if not href.startswith(("/", "http://", "https://", "mailto:")) or href.startswith("//"):
            continue
        kind = "file" if href.startswith("/uploads/") else "internal" if href.startswith("/") else "web"
        found.setdefault(href, {"href": href, "kind": kind,
                                "label": to_text(label).replace("📎", "").replace("↗", "").strip() or href})
    return list(found.values())


def save_page(pid, folder_id, title, html, is_readme=0):
    pid = write_page(db(), pid, folder_id, title, html, g.user["name"], is_readme)
    db().commit()
    return pid


@app.route("/folder/<int:fid>/new", methods=["GET", "POST"])
@app.route("/page/<int:pid>/edit", methods=["GET", "POST"])
def edit(fid=None, pid=None):
    p = get_or_404("SELECT * FROM pages WHERE id=?", pid) if pid else None
    fid = p["folder_id"] if p else get_or_404("SELECT id FROM folders WHERE id=?", fid)["id"]
    require_edit(fid)
    if request.method == "POST":
        title = "Overview" if p and p["is_readme"] else request.form["title"].strip() or "Untitled"
        pid = save_page(pid, fid, title, request.form.get("html", ""))
        return redirect(url_for("page", pid=pid))
    return render_template("edit.html", page=p, folder_id=fid)


@app.post("/page/<int:pid>/delete")
def delete_page(pid):
    require_admin()
    p = get_or_404("SELECT * FROM pages WHERE id=?", pid)
    if p["is_readme"]:
        abort(400, "A folder's overview can't be deleted. Edit it instead.")
    db().execute("DELETE FROM pages_fts WHERE rowid=?", (pid,))
    db().execute("DELETE FROM pages WHERE id=?", (pid,))  # revisions are kept
    db().commit()
    flash(f"Deleted {p['title']!r}.")
    return redirect(url_for("folder", fid=p["folder_id"]))


@app.post("/folder/<int:fid>/delete")
def delete_folder(fid):
    """Removes the folder, its overview, pages and edit rights. Page history and uploaded files are kept
    (an uploaded file can be linked from another folder too)."""
    require_admin()
    f = get_or_404("SELECT * FROM folders WHERE id=?", fid)
    d = db()
    d.execute("DELETE FROM pages_fts WHERE rowid IN (SELECT id FROM pages WHERE folder_id=?)", (fid,))
    d.execute("DELETE FROM pages WHERE folder_id=?", (fid,))
    d.execute("DELETE FROM perms WHERE folder_id=?", (fid,))
    d.execute("DELETE FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE folder_id=?)", (fid,))
    d.execute("DELETE FROM documents WHERE folder_id=?", (fid,))
    d.execute("DELETE FROM reflections WHERE folder_id=?", (fid,))
    d.execute("DELETE FROM resources WHERE folder_id=?", (fid,))
    d.execute("DELETE FROM folders WHERE id=?", (fid,))
    d.commit()
    flash(f"Deleted the folder {f['name']!r}.")
    return redirect(url_for("home"))


@app.route("/page/<int:pid>/history")
def history(pid):
    p = get_or_404("SELECT p.*, f.name AS folder FROM pages p JOIN folders f ON f.id=p.folder_id WHERE p.id=?", pid)
    revs = db().execute("SELECT id, title, saved_by, saved_at FROM revisions WHERE page_id=? ORDER BY id DESC",
                        (pid,)).fetchall()
    return render_template("history.html", page=p, revs=revs)


@app.route("/revision/<int:rid>")
def revision(rid):
    r = get_or_404("SELECT * FROM revisions WHERE id=?", rid)
    p = get_or_404("SELECT p.*, f.name AS folder FROM pages p JOIN folders f ON f.id=p.folder_id WHERE p.id=?",
                   r["page_id"])
    return render_page(p, r)


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
    section = request.form.get("section", "Other")
    return {"url": store(f.filename, f.save, folder_name(request.form.get("folder_id", type=int)), section), "name": f.filename,
            "image": Path(f.filename).suffix.lower() in IMAGES}


FILE_SECTIONS = {"Overview", "Documentation", "Slides", "Resources", "Reflections", "Pages", "Other"}


def safe_name(name, fallback="file"):
    """A file or folder name that is safe on Windows and Linux, keeping spaces and capitals."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name)).strip().strip(".")
    return name[:150] or fallback


def unique_path(folder, name):
    """folder/name, or folder/name (2), (3)... if that is taken."""
    path, stem, suffix, n = folder / name, Path(name).stem, Path(name).suffix, 2
    while path.exists():
        path, n = folder / f"{stem} ({n}){suffix}", n + 1
    return path


def url_for_file(path):
    return "/uploads/" + urllib.parse.quote(path.relative_to(UPLOADS).as_posix())


def store(filename, write, folder=None, section="Other"):
    """Save a file as <files>/<Folder>/<Section>/<name> (name kept, " (2)" added if taken); write(path) does the
    writing. folder is a folder's name (None: _General). Returns the file's URL."""
    where = UPLOADS / safe_name(folder or "_General") / safe_name(section if section in FILE_SECTIONS else "Other")
    where.mkdir(parents=True, exist_ok=True)
    path = unique_path(where, safe_name(Path(filename).name))
    write(path)
    return url_for_file(path)


def folder_name(fid, c=None):
    row = (c or db()).execute("SELECT name FROM folders WHERE id=?", (fid,)).fetchone()
    return row["name"] if row else None


@app.route("/uploads/<path:path>")
def uploads(path):
    inline = Path(path).suffix.lower() in VIEW_INLINE
    return send_from_directory(UPLOADS, path, as_attachment=not inline)


# ---------- Documentation and Slides (OnlyOffice) ----------
# OnlyOffice Document Server runs in its own container. The browser loads the editor from it (ONLYOFFICE_URL,
# default: this host on port 8081); the document server fetches files from KM and posts saved versions back
# (KM_INTERNAL_URL), signing everything with ONLYOFFICE_JWT_SECRET, which both containers share.
OO_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "")
OO_PUBLIC = os.environ.get("ONLYOFFICE_URL", "")
OO_INTERNAL = os.environ.get("ONLYOFFICE_INTERNAL_URL", "http://onlyoffice")
KM_INTERNAL = os.environ.get("KM_INTERNAL_URL", "http://km:8080")
OFFICE_TYPES = {"word": {"docx", "doc", "odt", "rtf", "txt"}, "cell": {"xlsx", "xls", "ods", "csv"},
                "slide": {"pptx", "ppt", "odp"}, "pdf": {"pdf"}}
EDITABLE = {"docx", "xlsx", "pptx"}  # other formats open read-only
KINDS = {"doc": ("Documentation", OFFICE_TYPES["word"] | OFFICE_TYPES["cell"] | OFFICE_TYPES["pdf"], "docx"),
         "slides": ("Slides", OFFICE_TYPES["slide"] | OFFICE_TYPES["pdf"], "pptx")}


def office_origin():
    if OO_PUBLIC:
        return OO_PUBLIC.rstrip("/")
    return f"{request.scheme}://{request.host.rsplit(':', 1)[0]}:8081" if request else ""


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


JWT_HEADER = json.dumps({"alg": "HS256", "typ": "JWT"}).encode()


def jwt_encode(payload):
    signing = f"{b64url(JWT_HEADER)}.{b64url(json.dumps(payload).encode())}"
    return f"{signing}.{b64url(hmac.new(OO_SECRET.encode(), signing.encode(), hashlib.sha256).digest())}"


def jwt_decode(token):
    """The payload of an HS256 token signed with ONLYOFFICE_JWT_SECRET; ValueError if it isn't."""
    try:
        head, body, sig = token.split(".")
    except ValueError:
        raise ValueError("malformed token")
    expected = b64url(hmac.new(OO_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    if not OO_SECRET or not hmac.compare_digest(sig, expected):
        raise ValueError("bad signature")
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def file_token(purpose, doc_id):
    return hmac.new(app.secret_key, f"{purpose}:{doc_id}".encode(), hashlib.sha256).hexdigest()


def ext(name):
    return Path(name).suffix.lower().lstrip(".")


def add_document(c, folder_id, kind, name, url, size, who):
    t = now()
    did = c.execute("INSERT INTO documents(folder_id, kind, name, url, size, updated_by, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (folder_id, kind, name, url, size, who, t)).lastrowid
    c.execute("INSERT INTO document_versions(document_id, version, url, size, saved_by, saved_at) VALUES (?,1,?,?,?,?)",
              (did, url, size, who, t))
    return did


def file_rel(url):
    """The file's path under the files folder, from its /uploads/... URL."""
    return urllib.parse.unquote(url.removeprefix("/uploads/"))


def upload_path(url):
    return UPLOADS / file_rel(url)


@app.post("/folder/<int:fid>/documents/<kind>")
def add_documents(fid, kind):
    """Upload files into Documentation or Slides, or (blank=1) start a new empty document."""
    if kind not in KINDS:
        abort(404)
    get_or_404("SELECT id FROM folders WHERE id=?", fid)
    require_edit(fid)
    _, allowed, blank_ext = KINDS[kind]
    added, refused = 0, []
    if request.form.get("blank"):
        name = f"Untitled {'document' if kind == 'doc' else 'presentation'}.{blank_ext}"
        src = BASE / "static" / "blank" / f"blank.{blank_ext}"
        url = store(name, lambda p: shutil.copyfile(src, p), folder_name(fid), KINDS[kind][0])
        add_document(db(), fid, kind, name, url, src.stat().st_size, g.user["name"])
        added = 1
    for f in request.files.getlist("file"):
        if not f.filename:
            continue
        if ext(f.filename) not in allowed:
            refused.append(f.filename)
            continue
        url = store(f.filename, f.save, folder_name(fid), KINDS[kind][0])
        add_document(db(), fid, kind, f.filename, url, upload_path(url).stat().st_size, g.user["name"])
        added += 1
    db().commit()
    if refused:
        flash(f"{KINDS[kind][0]} takes {', '.join(sorted(allowed))} files. Not added: {', '.join(refused)}.")
    if request.headers.get("X-Requested-With") == "fetch":
        return {"added": added, "refused": refused}
    return redirect(url_for("folder_section", fid=fid, section=kind))


def get_document(did):
    return get_or_404("SELECT d.*, f.name AS folder FROM documents d JOIN folders f ON f.id=d.folder_id WHERE d.id=?", did)


@app.route("/document/<int:did>")
def document(did):
    d = get_document(did)
    e = ext(d["name"])
    doc_type = next((t for t, exts in OFFICE_TYPES.items() if e in exts), "word")
    editing = can_edit(d["folder_id"]) and e in EDITABLE
    config = {
        "document": {"fileType": e, "key": f"km-{d['id']}-v{d['version']}", "title": d["name"],
                     "url": f"{KM_INTERNAL}/onlyoffice/file/{d['id']}?t={file_token('file', d['id'])}",
                     "permissions": {"edit": editing, "download": True}},
        "documentType": doc_type,
        "editorConfig": {"mode": "edit" if editing else "view", "lang": "en",
                         "callbackUrl": f"{KM_INTERNAL}/onlyoffice/callback/{d['id']}?t={file_token('save', d['id'])}",
                         "user": {"id": g.user["name"], "name": g.user["name"]},
                         "customization": {"forcesave": True, "goback": {"url": url_for("folder_section", fid=d["folder_id"], section=d["kind"])}}},
        "height": "100%", "width": "100%",
    }
    if OO_SECRET:
        config["token"] = jwt_encode(config)
    return render_template("document.html", d=d, config=config, office=office_origin(), configured=bool(OO_SECRET),
                           editing=editing)


@app.route("/document/<int:did>/download")
def document_download(did):
    d = get_document(did)
    return send_from_directory(UPLOADS, file_rel(d["url"]), as_attachment=True, download_name=d["name"])


@app.route("/document/<int:did>/versions")
def document_versions(did):
    d = get_document(did)
    versions = db().execute("SELECT * FROM document_versions WHERE document_id=? ORDER BY version DESC", (did,)).fetchall()
    return render_template("document_versions.html", d=d, versions=versions)


@app.route("/document/<int:did>/version/<int:vid>")
def document_version(did, vid):
    d = get_document(did)
    v = get_or_404("SELECT * FROM document_versions WHERE id=? AND document_id=?", vid, did)
    return send_from_directory(UPLOADS, file_rel(v["url"]), as_attachment=True,
                               download_name=f"{Path(d['name']).stem} (v{v['version']}){Path(d['name']).suffix}")


@app.post("/document/<int:did>/delete")
def document_delete(did):
    d = get_document(did)
    require_edit(d["folder_id"])
    db().execute("DELETE FROM document_versions WHERE document_id=?", (did,))
    db().execute("DELETE FROM documents WHERE id=?", (did,))  # the files stay on disk, like other uploads
    db().commit()
    flash(f"Removed {d['name']}.")
    return redirect(url_for("folder_section", fid=d["folder_id"], section=d["kind"]))


@app.route("/onlyoffice/file/<int:did>")
def onlyoffice_file(did):
    """The document server fetches the current file here (no KM sign-in; a token in the URL instead)."""
    if not hmac.compare_digest(request.args.get("t", ""), file_token("file", did)):
        abort(403)
    d = get_or_404("SELECT * FROM documents WHERE id=?", did)
    return send_from_directory(UPLOADS, file_rel(d["url"]), download_name=d["name"])


@app.post("/onlyoffice/callback/<int:did>")
def onlyoffice_callback(did):
    """The document server reports editing status here and, when a document is saved, where to fetch it."""
    if not hmac.compare_digest(request.args.get("t", ""), file_token("save", did)):
        abort(403)
    body = request.get_json(silent=True) or {}
    token = body.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        payload = jwt_decode(token)
    except ValueError:
        return {"error": 1}, 403
    payload = payload.get("payload", payload)  # the header form wraps the body in "payload"
    if payload.get("status") not in (2, 6):  # 2: closed with changes, 6: saved while still open
        return {"error": 0}
    d = get_or_404("SELECT * FROM documents WHERE id=?", did)
    src = urllib.parse.urlsplit(payload["url"])
    internal = urllib.parse.urlsplit(OO_INTERNAL)
    fetch_url = urllib.parse.urlunsplit((internal.scheme, internal.netloc, src.path, src.query, ""))
    with urllib.request.urlopen(fetch_url, timeout=120) as r:
        data = r.read()
    who = (payload.get("users") or [None])[0] or "OnlyOffice"
    current = upload_path(d["url"])
    keep = current.parent / "_versions"  # the version being replaced keeps a numbered copy here
    keep.mkdir(exist_ok=True)
    old = unique_path(keep, f"{current.stem} (v{d['version']}){current.suffix}")
    if current.exists():
        current.replace(old)
        db().execute("UPDATE document_versions SET url=? WHERE document_id=? AND version=?",
                     (url_for_file(old), did, d["version"]))
    current.write_bytes(data)
    t, version = now(), d["version"] + 1
    db().execute("UPDATE documents SET size=?, version=?, updated_by=?, updated_at=? WHERE id=?",
                 (len(data), version, who, t, did))
    db().execute("INSERT INTO document_versions(document_id, version, url, size, saved_by, saved_at) VALUES (?,?,?,?,?,?)",
                 (did, version, d["url"], len(data), who, t))
    db().commit()
    return {"error": 0}


# ---------- Resources: files and links beside the overview ----------

def add_resource(c, fid, kind, title, url, size, who):
    c.execute("INSERT INTO resources(folder_id, kind, title, url, size, added_by, added_at) VALUES (?,?,?,?,?,?,?)",
              (fid, kind, title, url, size, who, now()))


@app.post("/folder/<int:fid>/resources")
def add_resources(fid):
    """Upload files (any type, up to 2 GB) or add a link (title + URL)."""
    get_or_404("SELECT id FROM folders WHERE id=?", fid)
    require_edit(fid)
    for f in request.files.getlist("file"):
        if f.filename:
            url = store(f.filename, f.save, folder_name(fid), "Resources")
            add_resource(db(), fid, "file", f.filename, url, upload_path(url).stat().st_size, g.user["name"])
    link = request.form.get("url", "").strip()
    if link:
        if not re.match(r"(https?://|/)\S+$", link) or link.startswith("//"):
            flash("A link must start with http://, https:// or / (a page in KM).")
        else:
            add_resource(db(), fid, "link", request.form.get("title", "").strip() or link, link, None, g.user["name"])
    db().commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": 1}
    return redirect(url_for("folder", fid=fid) + "#resources")


@app.post("/resource/<int:rid>/delete")
def delete_resource(rid):
    r = get_or_404("SELECT * FROM resources WHERE id=?", rid)
    require_edit(r["folder_id"])
    db().execute("DELETE FROM resources WHERE id=?", (rid,))  # the file stays on disk
    db().commit()
    return redirect(url_for("folder", fid=r["folder_id"]) + "#resources")


# ---------- Reflections: anyone can add their own; authors and admins can change or remove them ----------

def get_reflection(rid):
    r = get_or_404("SELECT * FROM reflections WHERE id=?", rid)
    if not (is_admin() or r["author"] == g.user["name"]):
        abort(403, "Only the author or an admin can change this reflection.")
    return r


@app.post("/folder/<int:fid>/reflections")
def add_reflection(fid):
    get_or_404("SELECT id FROM folders WHERE id=?", fid)
    html = request.form.get("html", "").strip()
    if not to_text(html).strip() and "<img" not in html:
        abort(400, "A reflection needs some text.")
    t = now()
    db().execute("INSERT INTO reflections(folder_id, author, html, created_at, updated_at) VALUES (?,?,?,?,?)",
                 (fid, g.user["name"], html, t, t))
    db().commit()
    return {"html": html}


@app.post("/reflection/<int:rid>")
def save_reflection(rid):
    get_reflection(rid)
    html = request.form.get("html", "").strip()
    db().execute("UPDATE reflections SET html=?, updated_at=? WHERE id=?", (html, now(), rid))
    db().commit()
    return {"html": html}


@app.post("/reflection/<int:rid>/delete")
def delete_reflection(rid):
    r = get_reflection(rid)
    db().execute("DELETE FROM reflections WHERE id=?", (rid,))
    db().commit()
    flash("Reflection removed.")
    return redirect(url_for("folder_section", fid=r["folder_id"], section="reflections"))


# ---------- AAR (add-only; admins may edit, nobody may delete) ----------
# An entry is a title plus numbered points, each an observation, a reflection and a recommendation.
# The points are stored as JSON in `body`; entries written before this format stay plain text.

AAR_FIELDS = ("observation", "reflection", "recommendation")


def aar_points(body):
    """The entry's points, or None for an old plain-text entry."""
    try:
        points = json.loads(body)
    except ValueError:
        return None
    return points if isinstance(points, list) else None


def points_from_form():
    cols = [request.form.getlist(f) for f in AAR_FIELDS]
    points = [dict(zip(AAR_FIELDS, (v.strip() for v in row))) for row in zip(*cols)]
    return [p for p in points if any(p.values())]


app.jinja_env.globals.update(aar_points=aar_points, AAR_FIELDS=AAR_FIELDS)


@app.route("/aar")
def aar():
    entries = db().execute("SELECT * FROM aar_entries ORDER BY id DESC").fetchall()
    return render_template("aar.html", entries=entries)


@app.route("/aar/new", methods=["GET", "POST"])
def aar_new():
    points = []
    if request.method == "POST":
        title, points = request.form.get("title", "").strip(), points_from_form()
        if title and points:
            eid = db().execute("INSERT INTO aar_entries(title, body, author, created_at) VALUES (?,?,?,?)",
                               (title, json.dumps(points, ensure_ascii=False), g.user["name"], now())).lastrowid
            db().commit()
            return redirect(url_for("aar_entry", eid=eid))
        flash("An AAR needs a title and at least one point.")
    return render_template("aar_form.html", e=None, points=points)


@app.route("/aar/<int:eid>", methods=["GET", "POST"])
def aar_entry(eid):
    e = get_or_404("SELECT * FROM aar_entries WHERE id=?", eid)
    if request.method == "POST":
        require_admin()
        points = points_from_form()
        if not points:
            flash("An AAR needs at least one point.")
            return redirect(url_for("aar_edit", eid=eid))
        db().execute("UPDATE aar_entries SET title=?, body=?, edited_by=?, edited_at=? WHERE id=?",
                     (request.form.get("title", "").strip() or e["title"], json.dumps(points, ensure_ascii=False),
                      g.user["name"], now(), eid))
        db().commit()
        return redirect(url_for("aar_entry", eid=eid))
    hist = db().execute("SELECT * FROM aar_history WHERE entry_id=? ORDER BY id DESC", (eid,)).fetchall()
    return render_template("aar_entry.html", e=e, hist=hist)


@app.route("/aar/<int:eid>/edit")
def aar_edit(eid):
    require_admin()
    e = get_or_404("SELECT * FROM aar_entries WHERE id=?", eid)
    points = aar_points(e["body"]) or [{"observation": e["body"], "reflection": "", "recommendation": ""}]
    return render_template("aar_form.html", e=e, points=points)


@app.route("/links.json")
def links_json():
    """Everything you can link to, grouped for the editor's link dialog."""
    menu = {}
    for r in db().execute("""SELECT p.id, p.title, p.is_readme, p.folder_id, f.name AS folder FROM pages p
                             JOIN folders f ON f.id=p.folder_id ORDER BY f.name, p.is_readme DESC, p.title"""):
        url = url_for("folder", fid=r["folder_id"]) if r["is_readme"] else url_for("page", pid=r["id"])
        menu.setdefault(r["folder"], []).append({"title": f"{r['folder']} overview" if r["is_readme"] else r["title"],
                                                 "value": url})
    out = [{"title": name, "menu": items} for name, items in menu.items()]
    aars = [{"title": e["title"], "value": url_for("aar_entry", eid=e["id"])}
            for e in db().execute("SELECT id, title FROM aar_entries ORDER BY id DESC LIMIT 100")]
    return out + ([{"title": "AAR log", "menu": [{"title": "AAR log", "value": "/aar"}] + aars}] if aars else [])


# ---------- search ----------

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    pages, aars = [], []
    if q:
        fts = " ".join('"' + t.replace('"', '""') + '"' for t in q.split())
        pages = db().execute("""SELECT p.id, p.title, p.is_readme, f.name AS folder,
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
    batch = re.sub(r"[^a-z0-9-]", "", request.form["batch"].lower()).strip("-")  # e.g. alpha, team-2
    if not batch:
        flash("A group name must be letters or numbers, e.g. Alpha.")
        return redirect(url_for("admin"))
    d = db()
    d.execute("INSERT OR IGNORE INTO batches(id, created_at) VALUES (?,?)", (batch, now()))
    added, skipped = [], []
    for raw in re.split(r"[,\n]", request.form["names"]):
        n = clean_name(raw)
        if not n:
            continue
        username = f"{batch}-{n}"
        cur = d.execute("INSERT OR IGNORE INTO users(name, display_name, batch_id) VALUES (?,?,?)",
                        (username, raw.strip(), batch))
        (added if cur.rowcount else skipped).append(username)
    d.commit()
    flash(f"Group {batch.upper()}: added {', '.join(added) or 'nobody'}."
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


def backup_daily(dest):
    while True:  # one dated .db per day (same-day runs overwrite), uploads mirrored
        try:
            backup(dest)
        except Exception as e:
            print(f"BACKUP FAILED: {e}")
        time.sleep(24 * 60 * 60)


# ---------- start-up migrations (each does nothing once done) ----------

def move_old_files(c):
    """Files from before KM_FILES (inside the database volume) move to the files folder, same relative paths."""
    if UPLOADS.resolve() == OLD_UPLOADS.resolve() or not OLD_UPLOADS.exists():
        return
    for src in [p for p in OLD_UPLOADS.rglob("*") if p.is_file()]:
        dest = UPLOADS / src.relative_to(OLD_UPLOADS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.move(str(src), dest)


def file_references(c):
    """(url, folder id, section, name to use) for every file KM points at, most specific first."""
    refs = []
    for d in c.execute("SELECT d.url, d.folder_id, d.kind, d.name FROM documents d"):
        refs.append((d["url"], d["folder_id"], KINDS[d["kind"]][0], d["name"]))
    for v in c.execute("""SELECT v.url, d.folder_id, d.kind, d.name, v.version FROM document_versions v
                          JOIN documents d ON d.id=v.document_id"""):
        refs.append((v["url"], v["folder_id"], KINDS[v["kind"]][0] + "/_versions",
                     f"{Path(v['name']).stem} (v{v['version']}){Path(v['name']).suffix}"))
    for r in c.execute("SELECT url, folder_id, title FROM resources WHERE kind='file'"):
        refs.append((r["url"], r["folder_id"], "Resources", r["title"]))
    found = r'/uploads/[^"\'<>\s)]+'
    for p in c.execute("SELECT html, folder_id, is_readme FROM pages"):
        refs += [(u, p["folder_id"], "Overview" if p["is_readme"] else "Pages", None) for u in re.findall(found, p["html"])]
    for r in c.execute("SELECT html, folder_id FROM reflections"):
        refs += [(u, r["folder_id"], "Reflections", None) for u in re.findall(found, r["html"])]
    for r in c.execute("SELECT r.html, p.folder_id, p.is_readme FROM revisions r JOIN pages p ON p.id=r.page_id"):
        refs += [(u, r["folder_id"], "Overview" if r["is_readme"] else "Pages", None) for u in re.findall(found, r["html"])]
    return refs


def reorganise_files(c):
    """Files saved the old way (YYYY/MM/<random>-<name>) move to <Folder>/<Section>/<name> and every link to them
    is rewritten. Files nothing points at go to _Unused."""
    old = [p for p in UPLOADS.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*") if p.is_file()]
    if not old:
        return
    places = {}
    for url, fid, section, name in file_references(c):
        places.setdefault(file_rel(url), (fid, section, name))
    folders = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM folders")}
    for path in old:
        rel = path.relative_to(UPLOADS).as_posix()
        fid, section, name = places.get(rel, (None, None, None))
        original = re.sub(r"^[0-9a-f]{8}-", "", path.name)
        top = safe_name(folders.get(fid, "_Unused")) if fid else "_Unused"
        where = UPLOADS / top / Path(*[safe_name(s) for s in section.split("/")]) if section else UPLOADS / "_Unused"
        where.mkdir(parents=True, exist_ok=True)
        dest = unique_path(where, safe_name(name or original))
        path.replace(dest)
        old_url, new_url = "/uploads/" + rel, url_for_file(dest)
        for table, col in (("pages", "html"), ("revisions", "html"), ("reflections", "html"), ("documents", "url"),
                           ("document_versions", "url"), ("resources", "url")):
            c.execute(f"UPDATE {table} SET {col}=replace({col}, ?, ?) WHERE {col} LIKE ?", (old_url, new_url, f"%{old_url}%"))
    for d in sorted((p for p in UPLOADS.glob("[0-9][0-9][0-9][0-9]/**") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    for d in UPLOADS.glob("[0-9][0-9][0-9][0-9]"):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def resources_from_overviews(c):
    """Overviews used to keep files and links as text under "Links to resources"; they become Resources items."""
    for p in c.execute("SELECT id, folder_id, html, updated_by FROM pages WHERE is_readme=1").fetchall():
        sections = overview_sections(p["html"])
        if not sections["resources"]:
            continue
        for link in page_links(sections["resources"]):
            size = upload_path(link["href"]).stat().st_size if link["kind"] == "file" and upload_path(link["href"]).exists() else None
            add_resource(c, p["folder_id"], "file" if link["kind"] == "file" else "link", link["label"], link["href"],
                         size, p["updated_by"] or "KM")
        sections["resources"] = ""
        write_page(c, p["id"], p["folder_id"], "Overview", sections_html(sections), "KM")


def rename_old_usernames(c):
    """Usernames used to be group+name ("0521nic"); they become group-name ("0521-nic"), and so do the names on
    everything they wrote or were given. AAR entries keep the name they were written under: they can't be changed."""
    for u in c.execute("SELECT name, display_name, batch_id FROM users WHERE batch_id IS NOT NULL").fetchall():
        part = clean_name(u["display_name"] or "")
        old, new = u["name"], f"{u['batch_id']}-{part}"
        if not part or old != u["batch_id"] + part or c.execute("SELECT 1 FROM users WHERE name=?", (new,)).fetchone():
            continue
        c.execute("UPDATE users SET name=? WHERE name=?", (new, old))
        for table, col in (("perms", "subject"), ("pages", "updated_by"), ("revisions", "saved_by"), ("reflections", "author"),
                           ("documents", "updated_by"), ("document_versions", "saved_by"), ("resources", "added_by")):
            c.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))


with connect() as _c:
    rename_old_usernames(_c)
    move_old_files(_c)
    resources_from_overviews(_c)
    reorganise_files(_c)


if __name__ == "__main__":
    if sys.argv[1:2] == ["backup"]:
        backup(sys.argv[2] if len(sys.argv) > 2 else BASE / "backup")
    elif sys.argv[1:2] == ["import"]:
        import importer
        importer.main(sys.argv[2:])
    else:
        import logging
        import threading
        from waitress import serve
        if os.environ.get("KM_BACKUP"):
            threading.Thread(target=backup_daily, args=(os.environ["KM_BACKUP"],), daemon=True).start()
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        print(f"KM running on http://0.0.0.0:{PORT}  (other PCs: http://<this PC's IP>:{PORT})")
        serve(app, host="0.0.0.0", port=PORT, threads=8, max_request_body_size=2 * 1024 ** 3)
