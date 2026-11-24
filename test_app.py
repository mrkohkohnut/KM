"""Run: python test_app.py   (uses a throwaway data folder)"""
import io, os, re, shutil, sqlite3, tempfile

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

# admins can delete a folder with its overview and pages; trainees can't
post("/who", name="admin", pin="1234")
post("/folders", name="Old import")
old = sqlite3.connect(km.DB_PATH).execute("SELECT id FROM folders WHERE name='Old import'").fetchone()[0]
post(f"/folder/{old}/new", title="Old page", html="<p>zebra</p>")
post("/admin/perm", folder_id=str(old), subject="batch:0521")
post("/who", name="0521nic")
assert post(f"/folder/{old}/delete").status_code == 403
post("/who", name="admin", pin="1234")
assert b"Delete folder" in c.get(f"/folder/{old}").data
assert post(f"/folder/{old}/delete").status_code == 302
assert c.get(f"/folder/{old}").status_code == 404 and b"Old page" not in c.get("/search?q=zebra").data
assert not sqlite3.connect(km.DB_PATH).execute("SELECT 1 FROM pages WHERE folder_id=? UNION SELECT 1 FROM perms WHERE folder_id=?", (old, old)).fetchone()

# overview sections: each is saved on its own from the folder page, leaving the others as they were
post("/who", name="admin", pin="1234")
post("/folders", name="Sections")
sec = sqlite3.connect(km.DB_PATH).execute("SELECT id FROM folders WHERE name='Sections'").fetchone()[0]
page_html = c.get(f"/folder/{sec}").data
assert page_html.count(b'class="pen"') == 4 and b"Done by" in page_html and b"Links to resources" in page_html
r = post(f"/folder/{sec}/section/people", html="<ul><li>0521nic</li></ul>")
assert r.get_json() == {"html": "<ul><li>0521nic</li></ul>"}
post(f"/folder/{sec}/section/background", html="<p>Game studio</p>")
saved = km.overview_sections(sqlite3.connect(km.DB_PATH).execute(
    "SELECT html FROM pages WHERE folder_id=? AND is_readme=1", (sec,)).fetchone()[0])
assert saved["people"] == "<ul><li>0521nic</li></ul>" and saved["background"] == "<p>Game studio</p>" and saved["diagram"] == ""
# a diagram with several sheets is kept as one picture per sheet plus the .drawio file; anything else is dropped
sheets = ('<p>junk</p><div class="drawio-sheets" data-source="/uploads/n.drawio"><figure data-sheet="Full"><img data-drawio="1" src="/uploads/a.png" alt="Full"></figure>'
          '<figure data-sheet="SIP"><img data-drawio="1" src="/uploads/b.png" onerror="x()" alt="SIP"></figure><script>x()</script></div>')
assert post(f"/folder/{sec}/section/diagram", html=sheets).get_json()["html"] == (
    '<div class="drawio-sheets" data-source="/uploads/n.drawio"><figure data-sheet="Full"><img data-drawio="1" src="/uploads/a.png" alt="Full"></figure>'
    '<figure data-sheet="SIP"><img data-drawio="1" src="/uploads/b.png" alt="SIP"></figure></div>')
assert b'data-sheet="SIP"' in c.get(f"/folder/{sec}").data
assert b'class="pen remove"' in c.get(f"/folder/{sec}").data  # the remove button is there once a diagram exists
assert post(f"/folder/{sec}/section/diagram", html="").get_json() == {"html": ""}  # removing the diagram
assert b"Drop a .drawio file here" in c.get(f"/folder/{sec}").data
# the Network diagram section only ever holds draw.io diagrams: text sent to it is dropped
assert post(f"/folder/{sec}/section/diagram", html='<p>notes</p><p><img data-drawio="1" src="/uploads/d.png" alt="x"></p>').get_json() == \
    {"html": '<p><img data-drawio="1" src="/uploads/d.png" alt="x"></p>'}
assert post(f"/folder/{sec}/section/nope", html="x").status_code == 404
post("/who", name="0521nic")
assert post(f"/folder/{sec}/section/people", html="x").status_code == 403
assert b'class="pen"' not in c.get(f"/folder/{sec}").data and b"0521nic</li>" in c.get(f"/folder/{sec}").data
old = '<h2>Background</h2><p>Imported.</p><h2>Network diagram</h2><p>d</p><h2>Key contacts and links</h2><p>Nic</p><h2>Files</h2><p>f</p>'
assert km.overview_sections(old) == {"background": "<p>Imported.</p>", "diagram": "", "people": "<p>Nic</p>",
                                     "resources": "<h3>Files</h3><p>f</p>"}  # text in the diagram section is dropped

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

# Google Drive import: a fake download laid out like the real "Smelling Time" folder
import zipfile, importer
PNS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
cell = lambda t: f"<a:tc><a:txBody><a:p><a:r><a:t>{t}</a:t></a:r></a:p></a:txBody></a:tc>"
row = lambda *ts: "<a:tr>" + "".join(cell(t) for t in ts) + "</a:tr>"
drive = tempfile.mkdtemp()
top = os.path.join(drive, "Smelling Time")
files = {"NV Slides 3.0 Final Final.pptx": None, "network.drawio": "<mxfile/>", "network v2.drawio": "<mxfile/>",
         "presentation changes.txt": "flow", "RivalBlocks NV.pptx": "x", "NV Documentation (Ours).docx": "x",
         "NV Documentation Artefacts (Other Group)/Emails/ASD/content.txt": "Q3 close",
         "NV Documentation Artefacts (Other Group)/Audio/call.wav": "RIFF",
         "NV Documentation (Other Group)/NV Documentation (Other Group).docx": "x",
         "Archive/packetcapture-em1.pcap": "pcap", "Archive/Feedback.txt": "old"}
for name, data in files.items():
    os.makedirs(os.path.dirname(os.path.join(top, name)), exist_ok=True)
    if data is not None:
        with open(os.path.join(top, name), "w") as f:
            f.write(data)
with zipfile.ZipFile(os.path.join(top, "NV Slides 3.0 Final Final.pptx"), "w") as z:
    z.writestr("ppt/presentation.xml", f'<p:presentation {PNS}><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>')
    z.writestr("ppt/_rels/presentation.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                  '<Relationship Id="rId1" Target="slides/slide1.xml"/></Relationships>')
    z.writestr("ppt/slides/slide1.xml", f"""<p:sld {PNS}><p:cSld><p:spTree><p:graphicFrame><a:graphic><a:graphicData><a:tbl>
        {row("S/N", "Observation", "Reflection", "Recommendation")}{row("1", "Not enough workstations", "More trainees than PCs", "One each")}
        </a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>""")
shutil.make_archive(os.path.join(drive, "download"), "zip", drive, "Smelling Time")
shutil.rmtree(top)  # leave only the zip, as Drive gives it

importer.main([drive, "--dry-run"])
db = sqlite3.connect(km.DB_PATH)
assert not db.execute("SELECT 1 FROM folders WHERE name='RivalBlocks'").fetchone()  # dry run changed nothing
importer.main([drive])
overview = lambda f: db.execute("SELECT p.html FROM pages p JOIN folders f ON f.id=p.folder_id WHERE f.name=? AND p.is_readme", (f,)).fetchone()[0]
rival, tik = overview("RivalBlocks"), overview("TikTubeYouTok")
assert "RivalBlocks NV.pptx" in rival and "RivalBlocks NV" not in tik
assert "NV Documentation (Ours).docx" in tik and "(Ours)" not in rival
assert "network.drawio" in tik and "network.drawio" not in rival and "Diagram files" in tik and "network v2.drawio" in rival and "network v2.drawio" in tik
assert "packetcapture-em1.pcap" in tik and "Feedback" not in tik + rival  # only the pcap comes from Archive
assert "NV Documentation (Other Group).docx" in rival and "Emails" in rival
link = lambda html_, name: re.search(r'href="([^"]+)">📎 ' + re.escape(name), html_).group(1)
assert link(rival, "NV Slides 3.0 Final Final.pptx") == link(tik, "NV Slides 3.0 Final Final.pptx")  # stored once, in both
emails = db.execute("SELECT html FROM pages WHERE title='Emails'").fetchone()[0]
assert "<h2>ASD</h2>" in emails and "Q3 close" in emails
assert "<audio" in db.execute("SELECT html FROM pages WHERE title='Audio'").fetchone()[0]
assert json.loads(db.execute("SELECT body FROM aar_entries WHERE title='AAR from NV Slides 3.0 Final Final'").fetchone()[0]) == [
    {"observation": "Not enough workstations", "reflection": "More trainees than PCs", "recommendation": "One each"}]
counts = lambda: [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("folders", "pages", "aar_entries")]
before = counts(); importer.main([drive]); assert counts() == before  # running again adds nothing
assert c.get("/folder/%d" % db.execute("SELECT id FROM folders WHERE name='RivalBlocks'").fetchone()[0]).status_code == 200

# backup copies db + uploads
dest = tempfile.mkdtemp(); km.backup(dest)
assert any(f.endswith(".db") for f in os.listdir(dest)) and os.path.isdir(os.path.join(dest, "uploads"))
print("all checks passed")
