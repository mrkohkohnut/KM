"""Import a Google Drive folder download into KM.

    python app.py import "Smelling Time-20261123.zip" [more zips...]   (or an extracted folder)
    python app.py import ... --dry-run                                 (show what would happen)

Drive's "Download" on a folder gives one or more zips; Google Docs arrive as .docx and
Google Slides as .pptx. The top Drive folder's own files become one KM folder, and each of its
subfolders becomes another. Inside a KM folder:
  - every .docx / .pptx becomes a page with its text, headings, tables and images (original attached)
  - each nested subfolder becomes a page listing its files
  - other files are attached; images, audio and short text files are shown inline
  - .drawio files are linked under "Network diagram" (double-click one in the editor to make it editable)
An AAR table (Observation / Reflection / Recommendation) in the deck named by --aar-from becomes an AAR entry.
Running it again skips folders, pages and AARs that already exist.
"""
import argparse
import html
import json
import posixpath
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import app as km

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DOCS = {".docx", ".pptx"}
AUDIO = {".wav", ".mp3", ".ogg", ".m4a"}
TEXT = {".txt", ".md", ".log", ".csv", ".json"}
WHO = "import"
esc = html.escape


# ---------- Office files -> HTML (standard library only) ----------

def rels(z, part):
    """{relationship id: target} for a part such as 'word/document.xml'."""
    folder, name = part.rsplit("/", 1)
    path = f"{folder}/_rels/{name}.rels"
    if path not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(path)):
        t = r.get("Target", "")
        out[r.get("Id")] = t if r.get("TargetMode") == "External" else posixpath.normpath(posixpath.join(folder, t))
    return out


def image(z, rel, blip, save):
    target = rel.get(blip.get(R + "embed"), "")
    if target in z.namelist() and Path(target).suffix.lower() in km.IMAGES:
        return f'<img src="{save(Path(target).name, z.read(target))}" alt="">'
    return ""


def docx_to_html(z, save):
    rel = rels(z, "word/document.xml")
    body = ET.fromstring(z.read("word/document.xml")).find(W + "body")

    def run(r):
        parts = []
        for n in r:
            if n.tag == W + "t":
                parts.append(esc(n.text or ""))
            elif n.tag == W + "tab":
                parts.append(" ")
            elif n.tag in (W + "br", W + "cr"):
                parts.append("<br>")
            else:
                parts.extend(image(z, rel, b, save) for b in n.iter(A + "blip"))
        text, rpr = "".join(parts), r.find(W + "rPr")
        if text.strip() and "<img" not in text and rpr is not None:
            for tag, el in (("strong", "b"), ("em", "i")):
                flag = rpr.find(W + el)
                if flag is not None and flag.get(W + "val") not in ("0", "false"):
                    text = f"<{tag}>{text}</{tag}>"
        return text

    def inline(p):
        out = []
        for child in p:
            if child.tag == W + "r":
                out.append(run(child))
            elif child.tag == W + "hyperlink":
                inner = "".join(run(r) for r in child.iter(W + "r"))
                href = rel.get(child.get(R + "id"), "")
                out.append(f'<a href="{esc(href)}">{inner}</a>' if href.startswith(("http", "mailto:")) else inner)
            elif child.tag != W + "pPr":
                out.extend(run(r) for r in child.iter(W + "r"))
        return "".join(out)

    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for el in body:
        if el.tag == W + "p":
            ppr = el.find(W + "pPr")
            style = ppr.find(W + "pStyle") if ppr is not None else None
            style = style.get(W + "val", "") if style is not None else ""
            content = inline(el)
            if not re.sub(r"<br>|\s", "", content):
                continue
            if ppr is not None and ppr.find(W + "numPr") is not None:
                items.append(content)
                continue
            flush()
            level = re.match(r"[Hh]eading ?(\d)", style)
            if style == "Title" or level:
                n = 2 if style == "Title" else min(int(level.group(1)) + 1, 4)  # the page title is the h1
                out.append(f"<h{n}>{content}</h{n}>")
            else:
                out.append(f"<p>{content}</p>")
        elif el.tag == W + "tbl":
            flush()
            rows = ["<tr>" + "".join("<td>" + "<br>".join(c for c in map(inline, tc.iter(W + "p")) if c.strip()) + "</td>"
                                     for tc in tr.findall(W + "tc")) + "</tr>" for tr in el.findall(W + "tr")]
            out.append("<table>" + "".join(rows) + "</table>")
    flush()
    return "\n".join(out)


def pptx_to_html(z, save):
    """Returns (html, tables) where tables are lists of rows of cell text, for AAR detection."""
    prel = rels(z, "ppt/presentation.xml")
    slides = [prel[s.get(R + "id")] for s in ET.fromstring(z.read("ppt/presentation.xml")).iter(P + "sldId")]
    text = lambda ap: "".join(esc(t.text or "") for t in ap.iter(A + "t"))
    out, tables = [], []
    for n, part in enumerate(slides, 1):
        rel, title, body = rels(z, part), None, []
        for el in ET.fromstring(z.read(part)).iter():
            if el.tag == P + "sp" and el.find(P + "txBody") is not None:
                paras = [t for t in map(text, el.find(P + "txBody").findall(A + "p")) if t.strip()]
                ph = el.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                if title is None and ph is not None and ph.get("type") in ("title", "ctrTitle"):
                    title = " ".join(paras)
                else:
                    body.extend(f"<p>{t}</p>" for t in paras)
            elif el.tag == P + "pic":
                body.extend(image(z, rel, b, save) for b in el.iter(A + "blip"))
            elif el.tag == A + "tbl":
                rows = [[html.unescape("\n".join(t for t in map(text, tc.iter(A + "p")) if t.strip()))
                         for tc in tr.findall(A + "tc")] for tr in el.findall(A + "tr")]
                tables.append(rows)
                body.append("<table>" + "".join("<tr>" + "".join(f"<td>{esc(c).replace(chr(10), '<br>')}</td>" for c in r)
                                                + "</tr>" for r in rows) + "</table>")
        body = [b for b in body if b]
        if title is None:  # Google Slides exports often have no title box: use the first line of text
            first = next((b for b in body if b.startswith("<p>")), None)
            title = first[3:-4] if first else "Slide"
            if first:
                body.remove(first)
        out.append(f"<h2>{n}. {title}</h2>" + "".join(body))
    return "\n".join(out), tables


def aar_points(tables):
    """Rows of any table whose header has Observation, Reflection and Recommendation columns."""
    points = []
    for rows in tables:
        head = [c.lower() for c in rows[0]] if rows else []
        cols = {f: next((i for i, h in enumerate(head) if f in h), None) for f in km.AAR_FIELDS}
        if None in cols.values():
            continue
        for r in rows[1:]:
            point = {f: (r[i].strip() if i < len(r) else "") for f, i in cols.items()}
            if any(point.values()):
                points.append(point)
    return points


# ---------- Drive folder -> KM ----------

def size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Importer:
    def __init__(self, c, dry_run, aar_from):
        self.c, self.dry, self.aar_from, self.log = c, dry_run, aar_from.lower(), []

    def save(self, name, data):
        return "#dry-run" if self.dry else km.store(name, lambda p: p.write_bytes(data))

    def copy(self, path):
        return "#dry-run" if self.dry else km.store(path.name, lambda p: shutil.copyfile(path, p))

    def file_block(self, path):
        url, ext = self.copy(path), path.suffix.lower()
        block = f'<p><a href="{url}">📎 {esc(path.name)}</a> <small>{size(path.stat().st_size)}</small></p>'
        if ext in km.IMAGES:
            block += f'<p><img src="{url}" alt="{esc(path.stem)}"></p>'
        elif ext in AUDIO:
            block += f'<p><audio controls preload="none" src="{url}"></audio></p>'
        elif ext in TEXT and path.stat().st_size <= 64_000:
            block += f"<pre>{esc(path.read_text('utf-8', errors='replace'))}</pre>"
        return block

    def page(self, fid, title, html_):
        if self.c.execute("SELECT 1 FROM pages WHERE folder_id=? AND title=? AND is_readme=0", (fid, title)).fetchone():
            self.log.append(f"    skip page (exists): {title}")
            return None
        self.log.append(f"    page: {title}")
        return km.write_page(self.c, None, fid, title, html_, WHO)

    def document(self, fid, path):
        with zipfile.ZipFile(path) as z:
            if path.suffix.lower() == ".docx":
                body, tables = docx_to_html(z, self.save), []
            else:
                body, tables = pptx_to_html(z, self.save)
        pid = self.page(fid, path.stem, f'<p>Imported from <a href="{self.copy(path)}">📎 {esc(path.name)}</a>.</p>\n{body}')
        points = aar_points(tables)
        if points and self.aar_from and self.aar_from in path.stem.lower():
            title = f"AAR from {path.stem}"
            if self.c.execute("SELECT 1 FROM aar_entries WHERE title=?", (title,)).fetchone():
                self.log.append(f"    skip AAR (exists): {title}")
            else:
                self.c.execute("INSERT INTO aar_entries(title, body, author, created_at) VALUES (?,?,?,?)",
                               (title, json.dumps(points, ensure_ascii=False), WHO, km.now()))
                self.log.append(f"    AAR entry: {title} ({len(points)} point{'s' * (len(points) != 1)})")
        return pid

    def folder(self, name, d, recurse):
        row = self.c.execute("SELECT id FROM folders WHERE name=?", (name,)).fetchone()
        if row:
            fid, new = row["id"], False
            self.log.append(f"folder (exists, overview left as is): {name}")
        else:
            fid, new = self.c.execute("INSERT INTO folders(name) VALUES (?)", (name,)).lastrowid, True
            self.log.append(f"folder: {name}")
        files = sorted(f for f in d.iterdir() if f.is_file())
        docs = [(f.stem, self.document(fid, f)) for f in files if f.suffix.lower() in DOCS]
        diagrams = [f for f in files if f.suffix.lower() == ".drawio"]
        others = [f for f in files if f.suffix.lower() not in DOCS and f not in diagrams]
        sections = []
        for sub in sorted(x for x in d.iterdir() if x.is_dir()) if recurse else []:
            blocks = []
            for f in sorted(x for x in sub.rglob("*") if x.is_file()):
                if f.suffix.lower() in DOCS:
                    docs.append((f.stem, self.document(fid, f)))
                else:
                    where = f.parent.relative_to(sub).as_posix()
                    blocks.append(("" if where == "." else where, self.file_block(f)))
            body, heading = [], None
            for where, block in blocks:
                if where != heading:
                    heading = where
                    if where:
                        body.append(f"<h2>{esc(where)}</h2>")
                body.append(block)
            if body:
                sections.append((sub.name, self.page(fid, sub.name, "\n".join(body))))
        if not new:
            return
        link = lambda title, pid: f'<li><a href="/page/{pid}">{esc(title)}</a></li>' if pid else ""
        overview = [f"<h2>Background</h2><p>Imported from the Google Drive folder “{esc(d.name)}” on "
                    f"{datetime.now():%d %b %Y}. Edit this overview to describe what the folder covers.</p>",
                    "<h2>Network diagram</h2>"]
        if diagrams:
            overview.append("<p>Choose Edit overview and double-click a diagram file below to open it in draw.io; "
                            "saving puts the editable diagram on this page.</p>")
            overview += [self.file_block(f) for f in diagrams]
        else:
            overview.append("<p>Choose Edit overview, then Diagram in the toolbar to draw it here.</p>")
        if docs:
            overview.append("<h2>Documents</h2><ul>" + "".join(link(t, p) for t, p in docs) + "</ul>")
        if sections:
            overview.append("<h2>Sections</h2><ul>" + "".join(link(t, p) for t, p in sections) + "</ul>")
        if others:
            overview.append("<h2>Files</h2>" + "".join(self.file_block(f) for f in others))
        km.write_page(self.c, None, fid, "Overview", "\n".join(overview), WHO, is_readme=1)


def unpack(sources, tmp):
    """Zips (given directly or inside a folder) are extracted; Drive may split one folder over several.
    A folder with no zips in it is used as the extracted Drive folder."""
    paths = [Path(s) for s in sources]
    zips = [z for p in paths for z in (sorted(p.glob("*.zip")) if p.is_dir() else [p])]  # a folder of zips counts too
    if len(paths) == 1 and paths[0].is_dir() and not zips:
        root = paths[0]
    else:
        for p in zips:
            with zipfile.ZipFile(p) as z:
                z.extractall(tmp)  # zipfile strips absolute paths and ".." from member names
        root = tmp
    entries = list(root.iterdir())
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else root


def main(argv):
    ap = argparse.ArgumentParser(prog="python app.py import", description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="+", help="Drive download zip(s), or one extracted folder")
    ap.add_argument("--dry-run", action="store_true", help="show what would be imported, change nothing")
    ap.add_argument("--skip", action="append", default=["Archive"], metavar="FOLDER",
                    help="Drive subfolder to leave out (default: Archive)")
    ap.add_argument("--aar-from", default="NV Slides 3.0 Final Final",
                    help="deck whose AAR table becomes an AAR entry (AAR entries can never be deleted)")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = unpack(args.sources, Path(tmp))
        c = km.connect()
        imp = Importer(c, args.dry_run, args.aar_from)
        try:
            imp.folder(root.name, root, recurse=False)
            for d in sorted(x for x in root.iterdir() if x.is_dir()):
                if d.name in args.skip:
                    imp.log.append(f"skip folder: {d.name}")
                else:
                    imp.folder(d.name, d, recurse=True)
            if args.dry_run:
                c.rollback()
            else:
                c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
    print("\n".join(imp.log))
    print("Dry run: nothing was changed." if args.dry_run else "Import finished.")
