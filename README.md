# KM

A small knowledge base for training batches. It runs on one Windows PC, and the other PCs on the network use it through a browser.

## Install on the host PC (PC1)

1. Install Python 3 from python.org and tick **"Add python.exe to PATH"**.
2. Copy this folder to the host, e.g. `C:\km`.
3. Open PowerShell **as Administrator** in that folder and run:
   ```
   powershell -ExecutionPolicy Bypass -File setup-windows.ps1 -BackupDir "E:\km-backup"
   ```
   This installs Flask and Waitress, starts KM at boot, schedules a 02:00 backup and turns off sleep.
4. In the router, give PC1 a fixed IP (a DHCP reservation).
5. On any PC, open `http://<PC1's IP>:8080`. The first visit asks you to create the admin and its PIN.

To run it by hand and see errors, use `python app.py`. When it runs in the background, errors go to `data\km.log`.

The script adds no firewall rule because the PCs' firewalls are off. If you turn the firewall on later, allow inbound TCP 8080.

## How it's used

- **Admin → Batches:** enter batch `0521` and the names `nic, sam`. That creates the users `0521nic` and `0521sam`.
- **Users:** trainees pick their name with no password. Admins need their PIN, and admin mode turns off after 2 hours idle.
- **Admin → Who can edit:** give edit rights for a folder to a whole batch or to one user. Everyone can view everything.
- **Pages:** drag files into the editor and they upload to PC1. The **Diagram** button opens draw.io, and double-clicking a diagram edits it. Every save is kept in **History** and can be restored.
- **AAR:** anyone can add an entry. Only admins can edit, and every earlier version is kept. Nobody can delete an entry, because the database itself refuses.

## Where things are saved (on PC1)

```
data/km.db        pages, history, users, rights, AAR (SQLite)
data/uploads/     dropped files and draw.io diagrams (PNG with the diagram inside)
data/secret.key   signs the "who am I" cookie; keep it private
```

To back up by hand, run `python app.py backup E:\km-backup`.

## Check

`python test_app.py` exercises the rules: rights, uploads, history, AAR protection and backup.
