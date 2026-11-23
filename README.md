# KM

A small knowledge base for training batches. It runs on one Windows PC, and the other PCs on the network use it through a browser.

## Install on the host PC (PC1) with Docker

1. Install **Docker Desktop**. In its settings, turn on **"Start Docker Desktop when you sign in"**.
2. Copy this folder to PC1, e.g. `C:\km`, then in a terminal in that folder run:
   ```
   docker compose up -d --build
   ```
3. In the router, give PC1 a fixed IP (a DHCP reservation).
4. On any PC, open `http://<PC1's IP>:8080`. The first visit asks you to create the admin and its PIN.

Docker restarts KM after a crash and whenever Docker Desktop starts. Docker Desktop starts when someone signs in to PC1, so PC1 must be signed in to after a reboot. Set PC1 to never sleep (Settings → Power → Sleep: Never).

| Task | Command |
|---|---|
| Update after changing the code | `docker compose up -d --build` |
| See logs and errors | `docker compose logs -f` |
| Stop | `docker compose down` (your data is kept) |

The PCs' firewalls are off, so no rule is needed. If you turn the firewall on, allow inbound TCP 8080.

## How it's used

- **Admin → Batches:** enter batch `0521` and the names `nic, sam`. That creates the users `0521nic` and `0521sam`.
- **Users:** trainees pick their name with no password. Admins need their PIN, and admin mode turns off after 2 hours idle.
- **Admin → Who can edit:** give edit rights for a folder to a whole batch or to one user. Everyone can view everything.
- **Folder overview:** each folder's overview has four sections (Background, Network diagram, Done by, Links to resources). Click a section's pen to edit just that section in place, then Save.
- **Pages:** drag files into the editor and they upload to PC1. The **Diagram** button opens draw.io, and double-clicking a diagram edits it. Every save is kept in **History** and can be restored.
- **AAR:** anyone can add an entry. Only admins can edit, and every earlier version is kept. Nobody can delete an entry, because the database itself refuses.

## Importing the Smelling Time Google Drive folder

The importer splits the folder into two KM folders, **RivalBlocks** and **TikTubeYouTok**. The `ROUTES` list at the top of `importer.py` decides where each file goes. Files that cover both scenarios (the combined slide decks, `network v2.drawio`, `presentation changes.txt`, `do not touch.zip`) are stored once and linked from both folders. Only the packet capture comes over from `Archive`.

1. In Google Drive, right-click **Smelling Time** and choose **Download**. Drive gives you one or more `.zip` files.
2. Put the zip(s) in the `import` folder next to `docker-compose.yml` (e.g. `C:\km\import`). Don't unzip them.
3. Check where everything will go (this changes nothing):
   ```
   docker compose exec km python app.py import /import --dry-run
   ```
4. Run it for real:
   ```
   docker compose exec km python app.py import /import
   ```

Everything is attached as files: nested Drive folders (Emails, Audio, SMB and so on) become pages listing their files, and the rest is listed on each folder's overview, with `.drawio` files under Network diagram. Images, audio and short text files show on the page. The only thing extracted is the AAR table in `NV Slides 3.0 Final Final`, which becomes an AAR entry. AAR entries can never be deleted, so check that the dry run shows `AAR entry: ...` first. Running the import again skips anything already imported.

To make a `.drawio` file editable, click the pen next to **Network diagram** on the folder page, double-click the file and save in draw.io.

## Where things are saved (on PC1)

The database and uploaded files live in the Docker volume `km-data`, which is kept when the container is rebuilt:

```
km.db        pages, history, users, rights, AAR (SQLite)
uploads/     dropped files and draw.io diagrams (PNG with the diagram inside)
secret.key   signs the "who am I" cookie; keep it private
```

KM backs itself up when it starts and then every 24 hours, into `.\backup\` next to `docker-compose.yml`. Each backup is plain files: a dated `km-YYYYMMDD.db` and a copy of `uploads\`. To keep backups on another drive, change `./backup` in `docker-compose.yml` to e.g. `E:/km-backup`.

To restore, stop KM and copy a backup `.db` back in as `km.db`:
`docker compose down`, then `docker compose run --rm -v C:/km/backup:/b km cp /b/km-YYYYMMDD.db /app/data/km.db`, then `docker compose up -d`. Uploaded files are restored the same way: copy `/b/uploads/.` into `/app/data/uploads/`.

## Check

`python test_app.py` exercises the rules: rights, uploads, history, AAR protection and backup.
