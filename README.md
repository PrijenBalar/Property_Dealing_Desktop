# Property Dealing — Desktop

Windows desktop packaging for the **Kargil Property** web app (a PHP + MySQL
real-estate listing site). This repo holds the **source and build scripts**
that turn the web app into a self-contained Windows desktop program and a
single-file installer — no XAMPP / PHP / MySQL required on the target PC.

> The web application itself lives in the companion repo
> [`Property_Dealing`](https://github.com/PrijenBalar/Property_Dealing).

## What's here

| File | Purpose |
|------|---------|
| `launcher.py` | Boots bundled **portable PHP** + **portable MariaDB**, then shows the site in a native **WebView2** window (browser fallback). `--selftest` boots headless and verifies pages. |
| `KargilProperty.spec` | PyInstaller spec that freezes `launcher.py` into `KargilProperty.exe` (onefile, windowed). |
| `setup_make_payload.py` | Builds `payload.zip` from the local `KargilProperty` app folder (excludes transient `logs/` and `data_tmp/`). |
| `setup_installer.py` | Builds the single-file **self-extracting installer**: a Tkinter GUI that extracts the app + PHP/MariaDB runtime + database to a writable per-user folder (with a **"Change…" button** and auto-default to a drive that has free space), **registers the app with Windows** (shows in *Settings → Apps* with a working Uninstall), and creates Desktop/Start-Menu shortcuts. Headless modes: `--selftest <dir>`, `--install <dir>`. |

## What the bundled app does

- **Three roles** (stored in `tbl_admin.role`):
  - **Admin** — approves/deletes properties and views all listings & payments (cannot add/edit).
  - **Dealer** — adds properties with **multiple photos and videos** (with live previews); sees only their **own** listings and who purchased them.
  - **Buyer** — self-registers, browses approved listings, and buys them.
- **Buyer purchase flow** — demo card checkout, orders, and a "My Purchases" page.
- One unified login/registration; all data lives in the bundled MariaDB database.

## Download

The ready-to-run **`KargilProperty-Setup.exe`** is published under
[**Releases**](../../releases) (it's ~100 MB, so it's a release asset rather
than a committed file).

## Not in this repo (by design, too large / machine-local)

- The bundled **PHP** and **MariaDB** runtimes and the **web app** files
  (`runtime/`).
- The **MariaDB database / live data** (`data/`).
- Build outputs (`dist/`, `pybuild/`, `work/`, `payload.zip`, `*.exe`).

These are assembled locally in the `KargilProperty` app folder.

## Building

```sh
# 1. Freeze the launcher into KargilProperty.exe (from this folder)
pyinstaller KargilProperty.spec --distpath dist --workpath pybuild

# 2. Copy the fresh dist/KargilProperty.exe into the app folder, then build
#    the installer payload from that folder
python setup_make_payload.py            # -> payload.zip

# 3. Build the single-file installer
pyinstaller --onefile --windowed --name KargilProperty-Setup \
    --icon app.ico --add-data "payload.zip;." setup_installer.py
# -> dist/KargilProperty-Setup.exe
```

> Paths inside `KargilProperty.spec` are absolute to the original build
> machine; adjust them for your environment.
