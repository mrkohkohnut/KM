"""Import the "Smelling Time" Google Drive folder into KM, split into a RivalBlocks and a TikTubeYouTok folder.

    python app.py import /import              (a folder holding the Drive download zips, or the zips themselves)
    python app.py import /import --dry-run    (show where everything would go, change nothing)

Every file is attached as it is; ROUTES below decides which KM folder(s) it goes to. Word, Excel and PDF
files go to Documentation, decks to Slides, other files (and .drawio files) to Resources. Files in a nested
Drive folder (e.g. Artefacts/Emails/...) go on a page named after that folder (Emails), linked from Resources.
Files are stored as <files>/<KM folder>/<section>/<name>. The only thing extracted
is the AAR table (Observation / Reflection / Recommendation) in the main slide deck, which becomes an
AAR entry. Running it again skips folders, pages and AARs that already exist.
"""
import argparse
import fnmatch
import html
import json
import posixpath
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import app as km

RIVAL, TIKTUBE = "RivalBlocks", "TikTubeYouTok"
# (pattern for the path inside the Drive folder, KM folders). First match wins; [] leaves the file out.
# Sorted by reading the documents: RivalBlocks is 10.10.x / FreePBX / Gitea, TikTubeYouTok is RTMP / HLS / 192.168.10-200.x.
ROUTES = [
    ("archive/*.pcap", [TIKTUBE]),
    ("archive/*", []),
    ("nv documentation (other group)/*", [RIVAL]),
    ("nv documentation artefacts (other group)/*", [RIVAL]),
    ("nv documentation (ours)*", [TIKTUBE]),
    ("nv documentation (manual)*", [RIVAL]),
    ("network.drawio", [TIKTUBE]),
    ("*rivalblocks*", [RIVAL]),
    ("*", [RIVAL, TIKTUBE]),  # combined slide decks, network v2.drawio, notes, the zip: cover both or neither
]
AUDIO = {".wav", ".mp3", ".ogg", ".m4a"}
TEXT = {".txt", ".md", ".log", ".csv", ".json"}
WHO = "import"
esc = html.escape
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def route(rel_path):
    p = rel_path.lower()
    return next(folders for pattern, folders in ROUTES if fnmatch.fnmatch(p, pattern))


def pptx_tables(path):
    """Every table in a .pptx as rows of cell text, in slide order."""
    with zipfile.ZipFile(path) as z:
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        rels = {r.get("Id"): posixpath.normpath(posixpath.join("ppt", r.get("Target")))
                for r in ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))}
        tables = []
        for sld in pres.iter(P + "sldId"):
            for tbl in ET.fromstring(z.read(rels[sld.get(R + "id")])).iter(A + "tbl"):
                tables.append([["\n".join(t for t in ("".join(x.text or "" for x in p.iter(A + "t"))
                                                     for p in tc.iter(A + "p")) if t.strip())
                                for tc in tr.findall(A + "tc")] for tr in tbl.findall(A + "tr")])
        return tables


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


def size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Importer:
    def __init__(self, c, dry_run, main_deck):
        self.c, self.dry, self.main_deck, self.log, self.urls = c, dry_run, main_deck.lower(), [], {}

    def url(self, path, folder, section):
        """Store each file once, as <folder>/<section>/<name>, even when it is linked from both KM folders."""
        if path not in self.urls:
            self.urls[path] = "#dry-run" if self.dry else km.store(path.name, lambda p: shutil.copyfile(path, p), folder, section)
        return self.urls[path]

    def file_block(self, path, folder, section):
        url, ext = self.url(path, folder, section), path.suffix.lower()
        block = f'<p><a href="{url}">📎 {esc(path.name)}</a> <small>{size(path.stat().st_size)}</small></p>'
        if ext in km.IMAGES:
            block += f'<p><img src="{url}" alt="{esc(path.stem)}"></p>'
        elif ext in AUDIO:
            block += f'<p><audio controls preload="none" src="{url}"></audio></p>'
        elif ext in TEXT and path.stat().st_size <= 64_000:
            block += f"<pre>{esc(path.read_text('utf-8', errors='replace'))}</pre>"
        return block

    def aar(self, path):
        try:
            points = aar_points(pptx_tables(path))
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            self.log.append(f"could not read {path.name} as PowerPoint, so no AAR was imported")
            return
        title = f"AAR from {path.stem}"
        if not points:
            self.log.append(f"no AAR table found in {path.name}")
        elif self.c.execute("SELECT 1 FROM aar_entries WHERE title=?", (title,)).fetchone():
            self.log.append(f"skip AAR (exists): {title}")
        else:
            self.c.execute("INSERT INTO aar_entries(title, body, author, created_at) VALUES (?,?,?,?)",
                           (title, json.dumps(points, ensure_ascii=False), WHO, km.now()))
            self.log.append(f"AAR entry: {title} ({len(points)} point{'s' * (len(points) != 1)})")

    def folder(self, name, files):
        """files: [(path inside the Drive folder, local path)] routed to this KM folder."""
        row = self.c.execute("SELECT id FROM folders WHERE name=?", (name,)).fetchone()
        fid = row["id"] if row else self.c.execute("INSERT INTO folders(name) VALUES (?)", (name,)).lastrowid
        self.log.append(f"folder: {name}" + (" (exists, overview left as is)" if row else ""))
        diagrams, loose, pages = [], [], {}
        for rel, path in files:
            parts = rel.split("/")
            e = km.ext(path.name)
            kind = "slides" if e in km.OFFICE_TYPES["slide"] else "doc" if e in km.KINDS["doc"][1] else None
            if path.suffix.lower() == ".drawio":
                diagrams.append(path)
            elif kind and len(parts) <= 2:  # documents go to Documentation, decks to Slides
                if self.c.execute("SELECT 1 FROM documents WHERE folder_id=? AND name=?", (fid, path.name)).fetchone():
                    self.log.append(f"    skip {km.KINDS[kind][0]} file (exists): {path.name}")
                else:
                    km.add_document(self.c, fid, kind, path.name, self.url(path, name, km.KINDS[kind][0]), path.stat().st_size, WHO)
                    self.log.append(f"    {km.KINDS[kind][0]}: {path.name}")
            elif len(parts) <= 2:  # in the top Drive folder or directly in one of its subfolders
                loose.append(path)
            else:  # deeper: on a page named after the subfolder, with a heading per folder below it
                pages.setdefault(parts[1], []).append(("/".join(parts[2:-1]), path))
        made = []
        for title, entries in sorted(pages.items()):
            if self.c.execute("SELECT 1 FROM pages WHERE folder_id=? AND title=? AND is_readme=0", (fid, title)).fetchone():
                self.log.append(f"    skip page (exists): {title}")
                continue
            body, heading = [], None
            for where, path in sorted(entries, key=lambda e: (e[0], e[1].name)):
                if where != heading:
                    heading = where
                    if where:
                        body.append(f"<h2>{esc(where)}</h2>")
                body.append(self.file_block(path, name, "Pages"))
            made.append((title, km.write_page(self.c, None, fid, title, "\n".join(body), WHO)))
            self.log.append(f"    page: {title} ({len(entries)} file{'s' * (len(entries) != 1)})")
        # Resources: loose files and .drawio files (the Network diagram offers these), plus links to the artefact pages
        for path in loose + diagrams:
            if self.c.execute("SELECT 1 FROM resources WHERE folder_id=? AND title=?", (fid, path.name)).fetchone():
                self.log.append(f"    skip resource (exists): {path.name}")
                continue
            km.add_resource(self.c, fid, "file", path.name, self.url(path, name, "Resources"), path.stat().st_size, WHO)
            self.log.append(f"    resource: {path.name}")
        for title, pid in made:
            km.add_resource(self.c, fid, "link", title, f"/page/{pid}", None, WHO)
        if not row:
            overview = km.sections_html({"background": f"<p>Imported from Google Drive on {datetime.now():%d %b %Y}. "
                                                       "Click the pen to describe the scenario.</p>"})
            km.write_page(self.c, None, fid, "Overview", overview, WHO, is_readme=1)


def unpack(sources, tmp):
    """Zips (given directly or inside a folder) are extracted; Drive may split one folder over several.
    A folder with no zips in it is used as the extracted Drive folder."""
    paths = [Path(s) for s in sources]
    zips = [z for p in paths for z in (sorted(p.glob("*.zip")) if p.is_dir() else [p])]
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
    ap.add_argument("sources", nargs="+", help="Drive download zip(s), a folder holding them, or the extracted folder")
    ap.add_argument("--dry-run", action="store_true", help="show where everything would go, change nothing")
    ap.add_argument("--main-deck", default="NV Slides 3.0 Final Final",
                    help="slide deck whose AAR table becomes an AAR entry (AAR entries can never be deleted)")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = unpack(args.sources, Path(tmp))
        routed, left_out = {RIVAL: [], TIKTUBE: []}, []
        for path in sorted(f for f in root.rglob("*") if f.is_file()):
            rel = path.relative_to(root).as_posix()
            folders = route(rel)
            if not folders:
                left_out.append(rel)
            for name in folders:
                routed[name].append((rel, path))
        c = km.connect()
        imp = Importer(c, args.dry_run, args.main_deck)
        try:
            for name, files in routed.items():
                imp.folder(name, files)
            deck = next((p for _, p in routed[TIKTUBE] + routed[RIVAL]
                         if p.suffix.lower() == ".pptx" and p.stem.lower() == imp.main_deck), None)
            if deck:
                imp.aar(deck)
            else:
                imp.log.append(f"main deck {args.main_deck}.pptx not found, so no AAR was imported")
            c.rollback() if args.dry_run else c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
    if left_out:
        imp.log.append(f"left out ({len(left_out)}): " + ", ".join(left_out))
    print("\n".join(imp.log))
    print("Dry run: nothing was changed." if args.dry_run else "Import finished.")
