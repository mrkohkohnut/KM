# KM

A small knowledge base for training groups. It runs on one Windows PC, and the other PCs on the network use it through a browser.

## Install on the host PC (PC1) with Docker

1. Install **Docker Desktop**. In its settings, turn on **"Start Docker Desktop when you sign in"**.
2. Copy this folder to PC1, e.g. `C:\km`.
3. Create the `.env` file once. It holds the secret key KM and the document editor share. In PowerShell in that folder:
   ```
   "ONLYOFFICE_JWT_SECRET=" + [guid]::NewGuid().ToString("N") | Out-File -Encoding ascii .env
   ```
4. Start everything:
   ```
   docker compose up -d --build
   ```
   The first start downloads the document editor (OnlyOffice, about 1 GB) and takes a few minutes. It needs about 4 GB of free RAM on PC1.
5. In the router, give PC1 a fixed IP (a DHCP reservation).
6. On any PC, open `http://<PC1's IP>:8080`. The first visit asks you to create the admin and its PIN.

Docker restarts KM after a crash and whenever Docker Desktop starts. Docker Desktop starts when someone signs in to PC1, so PC1 must be signed in to after a reboot. Set PC1 to never sleep (Settings → Power → Sleep: Never).

| Task | Command |
|---|---|
| Update after changing the code | `docker compose up -d --build` |
| See logs and errors | `docker compose logs -f` |
| Stop | `docker compose down` (your data is kept) |

The PCs' firewalls are off, so no rule is needed. If you turn the firewall on, allow inbound TCP 8080 and 8081 (8081 is the document editor).

## How it's used

- **Admin → Groups:** enter the group `ME4A` and the names `nic, sam`. That creates the users `me4a-nic` and `me4a-sam` (usernames are group-name). Accounts made before groups existed (e.g. `0521nic`) were renamed to this format (`0521-nic`).
- **Users:** trainees pick their name with no password. Admins need their PIN, and admin mode turns off after 2 hours idle.
- **Admin → Who can edit:** give edit rights for a folder to a whole group or to one user. Everyone can view everything.
- **Folder overview:** each folder's overview has Background and Network diagram, with Done by and Resources beside it. Click a section's pen to edit just that section in place, then Save. **Resources** holds files of any type up to 2 GB (e.g. packet captures) and links: use **Add file**, **Add link**, or drop files on it. The Network diagram section is a draw.io diagram only: drop a `.drawio` file on it (or click its **+** to choose one from your computer), then click the diagram to edit it in draw.io.
- **Documentation and Slides:** each folder has both. Upload Word, Excel or PDF files to Documentation and PowerPoint or PDF files to Slides (click **Upload**, drop files on the section, or start a **New document** / **New presentation**). Click a file to open it in the browser: people with edit rights can edit it, everyone else can read it. Every save in the editor becomes a new version; the clock button lists them for download.
- **Reflections:** anyone can add their own reflection to a folder. The author or an admin can edit or remove it.
- **Older pages:** pages made before Documentation and Slides existed (such as the imported Emails or Audio pages) are listed at the bottom of their folder.
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

Imported `.drawio` files are listed under Links to resources (Diagram files). While a folder has no network diagram, its Network diagram section offers a **Use …** button for each of them.

## Where things are saved (on PC1)

**Files** are ordinary files in `C:\km\files`, so you can open them in Windows Explorer. Each KM folder has its own folder, with one subfolder per section, and files keep their real names:

```
files\
  RivalBlocks\
    Overview\        network diagrams and pictures used in the overview
    Documentation\   Word, Excel and PDF files
      _versions\     earlier versions, e.g. "Lab notes (v1).docx"
    Slides\          PowerPoint files (with their own _versions)
    Resources\       anything else you add, e.g. packet captures
    Reflections\     pictures or files added to reflections
    Pages\           files from older pages
  TikTubeYouTok\
    ...
  _Unused\           old files nothing in KM points to any more
```

If a name is already taken in a folder, the new file gets " (2)", " (3)" and so on. Don't rename or move files in there by hand: KM finds them by their path. To change a file, use KM (upload a new version, or edit it in the browser).

**The database** (`km.db`: users, rights, overviews, AAR, which file is where) stays in the Docker volume `km-data`, because SQLite can be damaged on folders Docker Desktop shares from Windows. `secret.key`, which signs the "who am I" cookie, is there too.

KM backs itself up when it starts and then every 24 hours, into `.\backup\` next to `docker-compose.yml`. Each backup is plain files: a dated `km-YYYYMMDD.db` and a copy of the files folder under `uploads\`. To keep backups on another drive, change `./backup` in `docker-compose.yml` to e.g. `E:/km-backup`.

To restore, stop KM and copy a backup `.db` back in as `km.db`:
`docker compose down`, then `docker compose run --rm -v C:/km/backup:/b km cp /b/km-YYYYMMDD.db /app/data/km.db`, then `docker compose up -d`. Files are restored by copying the backup's `uploads\` contents back into `C:\km\files`.

## Check

`python test_app.py` exercises the rules: rights, uploads, history, AAR protection and backup.
