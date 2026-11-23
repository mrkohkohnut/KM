"""Run: python test_app.py   (uses a throwaway data folder)"""
import io, os, shutil, sqlite3, tempfile

os.environ["KM_DATA"] = tempfile.mkdtemp()
import app as km

c = km.app.test_client()
post = lambda url, **data: c.post(url, data=data)

# first run: setup admin, then pick user
assert c.get("/").headers["Location"].endswith("/setup")
post("/setup", name="Admin", pin="1234")
assert post("/who", name="admin", pin="0000").status_code == 200  # wrong PIN stays on page
assert post("/who", name="admin", pin="1234").status_code == 302

# batch 0521 -> usernames batch + name
post("/admin/batch", batch="0521", names="Nic, sam\nAlex")
post("/admin/batch", batch="0521", names="nic")  # duplicate is skipped
names = [r[0] for r in sqlite3.connect(km.DB_PATH).execute("SELECT name FROM users WHERE batch_id='0521' ORDER BY name")]
assert names == ["0521alex", "0521nic", "0521sam"], names

post("/folders", name="Module 1"); post("/folders", name="Module 2")
# each folder gets an overview page that opens at the top of the folder and can't be deleted
assert b"Network diagram" in c.get("/folder/1").data
assert c.get("/page/1").headers["Location"].endswith("/folder/1")
assert post("/page/1/delete").status_code == 400
post("/page/1/edit", title="ignored", html='<p>Lab network</p><a href="/uploads/x/a.pdf">📎 a.pdf</a> <a href="https://example.com">vendor</a> <a href="javascript:alert(1)">bad</a>')
assert sqlite3.connect(km.DB_PATH).execute("SELECT title FROM pages WHERE id=1").fetchone()[0] == "Overview"
links = km.page_links(sqlite3.connect(km.DB_PATH).execute("SELECT html FROM pages WHERE id=1").fetchone()[0])
assert [(l["kind"], l["label"]) for l in links] == [("file", "a.pdf"), ("web", "vendor")], links
post("/admin/perm", folder_id="1", subject="batch:0521")  # whole batch edits Module 1
post("/admin/perm", folder_id="2", subject="0521sam")      # only sam edits Module 2

assert b"0521nic" in c.get("/who").data  # name list renders with admins + batches

# trainee: no PIN needed
assert post("/who", name="0521nic").status_code == 302
assert c.get("/admin").status_code == 403
assert post("/folder/1/new", title="Router setup", html="<p>Plug in the cable</p>").status_code == 302
assert post("/folder/2/new", title="Nope", html="x").status_code == 403  # nic can't edit Module 2
assert post("/folders", name="Mine").status_code == 403

# upload lands in data/uploads on the host; blocked where no rights
r = c.post("/upload", data={"folder_id": "1", "file": (io.BytesIO(b"PDFDATA"), "manual.pdf")})
url = r.get_json()["url"]
assert (km.UPLOADS / url.removeprefix("/uploads/")).read_bytes() == b"PDFDATA"
assert c.get(url).data == b"PDFDATA"
assert c.post("/upload", data={"folder_id": "2", "file": (io.BytesIO(b"x"), "a.txt")}).status_code == 403

# edit + history + restore
post("/page/3/edit", title="Router setup", html="<p>Plug in the blue cable</p>")
assert b"blue cable" in c.get("/page/3").data
assert b"blue" in c.get("/search?q=blue").data and b"Router setup" in c.get("/search?q=cable").data
post("/revision/4/restore")  # rev 1-2 = overviews, 3 = edit above, 4 = Router setup first save
assert b"blue" not in c.get("/page/3").data
assert any(m["title"] == "Module 1" for m in c.get("/links.json").get_json())

# AAR: title + points (observation / reflection / recommendation); anyone adds, only admins correct,
# nobody deletes (enforced by the database)
post("/who", name="0521nic")
assert post("/aar/new", title="Empty", observation=[""], reflection=[""], recommendation=[""]).status_code == 200  # rejected
post("/aar/new", title="Exercise 1", observation=["Comms slow", "Kit late"], reflection=["One radio", ""],
     recommendation=["Issue two radios", "Book kit earlier"])
import json
assert [p["observation"] for p in json.loads(sqlite3.connect(km.DB_PATH).execute("SELECT body FROM aar_entries").fetchone()[0])] == ["Comms slow", "Kit late"]
assert b"Issue two radios" in c.get("/aar/1").data and b"Book kit earlier" in c.get("/aar").data
assert post("/aar/1", title="hacked", observation=["x"]).status_code == 403
assert c.get("/aar/1/edit").status_code == 403
post("/who", name="admin", pin="1234")
assert b"Comms slow" in c.get("/aar/1/edit").data
post("/aar/1", title="Exercise 1", observation=["Comms very slow"], reflection=["One radio"], recommendation=["Issue two radios"])
db = sqlite3.connect(km.DB_PATH)
assert "Kit late" in db.execute("SELECT body FROM aar_history").fetchone()[0]  # original kept
assert b"Earlier wording" in c.get("/aar/1").data
for sql in ["DELETE FROM aar_entries", "DELETE FROM aar_history", "UPDATE aar_history SET body='x'",
            "UPDATE aar_entries SET author='someone'"]:
    try:
        db.execute(sql); raise AssertionError(sql + " should have been blocked")
    except sqlite3.IntegrityError:
        pass
db.rollback()

# Google Drive import: a fake Drive download with a Word doc, a slide deck holding an AAR table,
# a draw.io file, nested folders and an Archive folder that must be left out
import zipfile, importer
def office(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
PNS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
cell = lambda t: f"<a:tc><a:txBody><a:p><a:r><a:t>{t}</a:t></a:r></a:p></a:txBody></a:tc>"
row = lambda *ts: "<a:tr>" + "".join(cell(t) for t in ts) + "</a:tr>"
drive = tempfile.mkdtemp()
top = os.path.join(drive, "Smelling Time")
for d in ["Artefacts/Emails", "Artefacts/Audio", "Archive"]:
    os.makedirs(os.path.join(top, d))
office(os.path.join(top, "Lab notes.docx"), {"word/document.xml": f"""<w:document {W}><w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Lab setup</w:t></w:r></w:p>
  <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Router</w:t></w:r><w:r><w:t> at 192.168.2.254</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>OSPF</w:t></w:r></w:p>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Host</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>10.10.20.100</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""})
office(os.path.join(top, "NV Slides 3.0 Final Final.pptx"), {
    "ppt/presentation.xml": f'<p:presentation {PNS}><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
    "ppt/_rels/presentation.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                       '<Relationship Id="rId1" Target="slides/slide1.xml"/></Relationships>',
    "ppt/slides/slide1.xml": f"""<p:sld {PNS}><p:cSld><p:spTree>
      <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>AAR</a:t></a:r></a:p></p:txBody></p:sp>
      <p:graphicFrame><a:graphic><a:graphicData><a:tbl>{row("S/N", "Observation", "Reflection", "Recommendation")}
        {row("1", "Not enough workstations", "More trainees than PCs", "One workstation each")}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>
    </p:spTree></p:cSld></p:sld>"""})
for name, data in {"network.drawio": "<mxfile/>", "notes.txt": "check the DC", "Artefacts/Emails/content.txt": "Q3 close",
                   "Artefacts/Audio/call.wav": "RIFF", "Archive/capture.pcap": "x"}.items():
    with open(os.path.join(top, name), "w") as f:
        f.write(data)
dl = os.path.join(drive, "download.zip")
shutil.make_archive(dl[:-4], "zip", drive, "Smelling Time")

importer.main([drive, "--dry-run"])  # a folder holding the zip works like the zip itself
db = sqlite3.connect(km.DB_PATH)
assert not db.execute("SELECT 1 FROM folders WHERE name='Smelling Time'").fetchone()  # dry run changed nothing
importer.main([dl])
folders = {r[0] for r in db.execute("SELECT name FROM folders")}
assert {"Smelling Time", "Artefacts"} <= folders and "Archive" not in folders, folders
doc = db.execute("SELECT html FROM pages WHERE title='Lab notes'").fetchone()[0]
assert "<h2>Lab setup</h2>" in doc and "<strong>Router</strong>" in doc and "<li>OSPF</li>" in doc and "<td>10.10.20.100</td>" in doc, doc
overview = db.execute("SELECT p.html FROM pages p JOIN folders f ON f.id=p.folder_id WHERE f.name='Smelling Time' AND p.is_readme").fetchone()[0]
assert "network.drawio" in overview and "check the DC" in overview and "Lab notes" in overview, overview
sections = {r[0]: r[1] for r in db.execute("SELECT title, html FROM pages WHERE folder_id=(SELECT id FROM folders WHERE name='Artefacts') AND is_readme=0")}
assert set(sections) == {"Emails", "Audio"} and "Q3 close" in sections["Emails"] and "<audio" in sections["Audio"], sections
aar = db.execute("SELECT body FROM aar_entries WHERE title='AAR from NV Slides 3.0 Final Final'").fetchone()[0]
assert json.loads(aar) == [{"observation": "Not enough workstations", "reflection": "More trainees than PCs",
                            "recommendation": "One workstation each"}]
counts = lambda: [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("folders", "pages", "aar_entries")]
before = counts(); importer.main([dl]); assert counts() == before  # running again adds nothing
assert c.get("/folder/%d" % db.execute("SELECT id FROM folders WHERE name='Artefacts'").fetchone()[0]).status_code == 200

# backup copies db + uploads
dest = tempfile.mkdtemp(); km.backup(dest)
assert any(f.endswith(".db") for f in os.listdir(dest)) and os.path.isdir(os.path.join(dest, "uploads"))
print("all checks passed")
