"""Run: python test_app.py   (uses a throwaway data folder)"""
import io, os, sqlite3, tempfile

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
post("/admin/perm", folder_id="1", subject="batch:0521")  # whole batch edits Module 1
post("/admin/perm", folder_id="2", subject="0521sam")      # only sam edits Module 2

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
post("/page/1/edit", title="Router setup", html="<p>Plug in the blue cable</p>")
assert b"blue cable" in c.get("/page/1").data
assert b"blue" in c.get("/search?q=blue").data and b"Router setup" in c.get("/search?q=cable").data
post("/revision/1/restore")
assert b"blue" not in c.get("/page/1").data

# AAR: anyone adds, trainees can't edit, nobody deletes (enforced by the database)
post("/aar", title="Exercise 1", body="Went well")
assert post("/aar/1", title="hacked", body="x").status_code == 403
post("/who", name="admin", pin="1234")
post("/aar/1", title="Exercise 1", body="Went well, comms slow")
db = sqlite3.connect(km.DB_PATH)
assert db.execute("SELECT body FROM aar_history").fetchone()[0] == "Went well"  # original kept
for sql in ["DELETE FROM aar_entries", "DELETE FROM aar_history", "UPDATE aar_history SET body='x'",
            "UPDATE aar_entries SET author='someone'"]:
    try:
        db.execute(sql); raise AssertionError(sql + " should have been blocked")
    except sqlite3.IntegrityError:
        pass

# backup copies db + uploads
dest = tempfile.mkdtemp(); km.backup(dest)
assert any(f.endswith(".db") for f in os.listdir(dest)) and os.path.isdir(os.path.join(dest, "uploads"))
print("all checks passed")
