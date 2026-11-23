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
- **Pages:** drag files into the editor and they upload to PC1. The **Diagram** button opens draw.io, and double-clicking a diagram edits it. Every save is kept in **History** and can be restored.
- **AAR:** anyone can add an entry. Only admins can edit, and every earlier version is kept. Nobody can delete an entry, because the database itself refuses.

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
