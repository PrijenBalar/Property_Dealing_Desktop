#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kargil Property - Desktop launcher.

Boots a bundled portable MariaDB + PHP built-in server and shows the web app
inside a native desktop window (WebView2). Everything is self-contained; no
XAMPP / MySQL / PHP install is required on the target PC.

Folder layout expected next to this launcher (or the built .exe):

    KargilProperty/
      KargilProperty.exe         <- this launcher (frozen)
      runtime/
        php/                     <- portable PHP (php.exe, ext/, dlls)
        mariadb/                 <- portable MariaDB (bin/, share/, lib/)
        www/                     <- the PHP web app (docroot)
      data/                      <- MariaDB data directory (pre-seeded)
      logs/                      <- runtime logs (created automatically)

Run with --selftest to boot the stack headless, hit the homepage, and exit
(used for automated verification during the build).
"""

import os
import sys
import time
import socket
import signal
import subprocess
import threading
import urllib.request
from pathlib import Path

APP_NAME = "Kargil Property"
DB_NAME = "kargil_property1"
DB_USER = "root"
DB_PASS = "root"
PREFERRED_DB_PORT = 3307
PREFERRED_HTTP_PORT = 8650

CREATE_NO_WINDOW = 0x08000000  # hide child console windows on Windows


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def base_dir() -> Path:
    """Folder that contains this launcher (works frozen and from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE = base_dir()
RUNTIME = BASE / "runtime"
PHP_DIR = RUNTIME / "php"
MARIADB_DIR = RUNTIME / "mariadb"
WWW_DIR = RUNTIME / "www"
DATA_DIR = BASE / "data"
LOG_DIR = BASE / "logs"
SESS_DIR = BASE / "data_tmp" / "sessions"

PHP_EXE = PHP_DIR / "php.exe"
MARIADBD_EXE = MARIADB_DIR / "bin" / "mariadbd.exe"
MARIADB_ADMIN = MARIADB_DIR / "bin" / "mariadb-admin.exe"


def _ensure_std_streams() -> None:
    """When launched windowed from Explorer, sys.stdout/stderr are None.
    Any print() (ours or a library's) would then raise 'lost sys.stdout' and
    silently kill the boot thread. Redirect to a file so nothing can crash."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    target = None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        target = open(LOG_DIR / "stdout.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        try:
            target = open(os.devnull, "w")
        except Exception:
            return
    if sys.stdout is None:
        sys.stdout = target
    if sys.stderr is None:
        sys.stderr = target


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        if sys.stdout is not None:
            print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "launcher.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def free_port(preferred: int) -> int:
    """Return `preferred` if free, otherwise an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def write_php_ini() -> Path:
    """Generate php.ini with an absolute extension_dir for THIS machine."""
    ext_dir = PHP_DIR / "ext"
    SESS_DIR.mkdir(parents=True, exist_ok=True)
    ini = f"""; Auto-generated at launch by {APP_NAME} launcher
extension_dir = "{ext_dir}"
extension=mysqli
extension=gd
extension=mbstring
extension=fileinfo
extension=exif
extension=openssl
extension=curl
extension=pdo_mysql

display_errors = Off
log_errors = On
error_log = "{LOG_DIR / 'php_error.log'}"
error_reporting = E_ALL & ~E_DEPRECATED & ~E_NOTICE
date.timezone = "Asia/Kolkata"

upload_max_filesize = 256M
post_max_size = 300M
max_file_uploads = 100
memory_limit = 512M
max_execution_time = 300
max_input_time = 300
session.save_path = "{SESS_DIR}"
"""
    ini_path = PHP_DIR / "php.ini"
    ini_path.write_text(ini, encoding="utf-8")
    return ini_path


# --------------------------------------------------------------------------- #
# MariaDB
# --------------------------------------------------------------------------- #
class Stack:
    def __init__(self):
        self.db_port = PREFERRED_DB_PORT
        self.http_port = PREFERRED_HTTP_PORT
        self.db_proc = None
        self.php_proc = None
        self.url = ""

    # -- MariaDB ----------------------------------------------------------- #
    def start_mariadb(self):
        self.db_port = free_port(PREFERRED_DB_PORT)
        log(f"Starting MariaDB on 127.0.0.1:{self.db_port} ...")
        args = [
            str(MARIADBD_EXE),
            "--no-defaults",
            f"--basedir={MARIADB_DIR}",
            f"--datadir={DATA_DIR}",
            f"--port={self.db_port}",
            "--bind-address=127.0.0.1",
            "--skip-name-resolve",
            "--innodb-log-file-size=16M",
            "--innodb-buffer-pool-size=64M",
            f"--log-error={LOG_DIR / 'mariadb_error.log'}",
        ]
        self.db_proc = subprocess.Popen(
            args, creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_db_ready()

    def _db_ping(self) -> bool:
        try:
            r = subprocess.run(
                [str(MARIADB_ADMIN), "--no-defaults", "-u", DB_USER,
                 f"-p{DB_PASS}", "--host=127.0.0.1", f"--port={self.db_port}",
                 "--connect-timeout=2", "ping"],
                creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _wait_db_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.db_proc.poll() is not None:
                raise RuntimeError("MariaDB exited during startup - see logs/mariadb_error.log")
            if self._db_ping():
                log("MariaDB is ready.")
                return
            time.sleep(1)
        raise RuntimeError("MariaDB did not become ready in time.")

    def stop_mariadb(self):
        if not self.db_proc:
            return
        log("Shutting down MariaDB (clean) ...")
        try:
            subprocess.run(
                [str(MARIADB_ADMIN), "--no-defaults", "-u", DB_USER,
                 f"-p{DB_PASS}", "--host=127.0.0.1", f"--port={self.db_port}",
                 "shutdown"],
                creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
        except Exception as e:
            log(f"mariadb-admin shutdown failed: {e}")
        try:
            self.db_proc.wait(timeout=30)
        except Exception:
            log("Forcing MariaDB termination.")
            try:
                self.db_proc.terminate()
            except Exception:
                pass
        self.db_proc = None

    # -- PHP --------------------------------------------------------------- #
    def start_php(self):
        self.http_port = free_port(PREFERRED_HTTP_PORT)
        ini = write_php_ini()
        log(f"Starting PHP server on 127.0.0.1:{self.http_port} ...")
        env = dict(os.environ)
        env.update({
            "KP_DB_HOST": "127.0.0.1",
            "KP_DB_PORT": str(self.db_port),
            "KP_DB_USER": DB_USER,
            "KP_DB_PASS": DB_PASS,
            "KP_DB_NAME": DB_NAME,
        })
        args = [
            str(PHP_EXE), "-c", str(ini),
            "-S", f"127.0.0.1:{self.http_port}",
            "-t", str(WWW_DIR),
        ]
        self.php_proc = subprocess.Popen(
            args, cwd=str(WWW_DIR), env=env, creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.url = f"http://127.0.0.1:{self.http_port}/index.php"
        self._wait_http_ready()

    def _wait_http_ready(self, timeout=30):
        deadline = time.time() + timeout
        probe = f"http://127.0.0.1:{self.http_port}/index.php"
        while time.time() < deadline:
            if self.php_proc.poll() is not None:
                raise RuntimeError("PHP server exited during startup.")
            try:
                with urllib.request.urlopen(probe, timeout=3) as r:
                    r.read(64)
                    log("PHP server is ready.")
                    return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("PHP server did not become ready in time.")

    def stop_php(self):
        if not self.php_proc:
            return
        log("Stopping PHP server ...")
        try:
            self.php_proc.terminate()
            self.php_proc.wait(timeout=10)
        except Exception:
            try:
                self.php_proc.kill()
            except Exception:
                pass
        self.php_proc = None

    # -- lifecycle --------------------------------------------------------- #
    def boot(self):
        self.start_mariadb()
        self.start_php()

    def shutdown(self):
        self.stop_php()
        self.stop_mariadb()


# --------------------------------------------------------------------------- #
# Pre-flight checks
# --------------------------------------------------------------------------- #
def preflight() -> str:
    missing = [str(p) for p in (PHP_EXE, MARIADBD_EXE, MARIADB_ADMIN, WWW_DIR, DATA_DIR)
               if not p.exists()]
    if missing:
        return ("This app folder looks incomplete. Missing:\n  "
                + "\n  ".join(missing)
                + "\n\nPlease keep KargilProperty.exe together with the "
                  "'runtime' and 'data' folders.")
    if not check_writable(BASE):
        return ("This app cannot write to its own folder, so the database "
                "cannot run.\n\nPlease MOVE the whole 'KargilProperty' folder "
                "to a location you own (for example your Desktop, Documents, "
                "or any drive like D:\\) and avoid 'C:\\Program Files'.")
    return ""


# --------------------------------------------------------------------------- #
# Self-test (headless) - used during build verification
# --------------------------------------------------------------------------- #
def selftest() -> int:
    err = preflight()
    if err:
        log("PREFLIGHT FAILED:\n" + err)
        return 2
    stack = Stack()
    try:
        stack.boot()
        tests = ["/index.php", "/admin/login.php", "/contact.php"]
        ok = True
        for path in tests:
            url = f"http://127.0.0.1:{stack.http_port}{path}"
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    body = r.read().decode("utf-8", "replace")
                marker = ("Kargil Property" in body) or ("Admin" in body)
                log(f"GET {path} -> {r.status} len={len(body)} marker={marker}")
                ok = ok and (r.status == 200) and marker
            except Exception as e:
                log(f"GET {path} FAILED: {e}")
                ok = False
        log("SELFTEST RESULT: " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    except Exception as e:
        log(f"SELFTEST ERROR: {e}")
        return 3
    finally:
        stack.shutdown()


# --------------------------------------------------------------------------- #
# GUI mode
# --------------------------------------------------------------------------- #
LOADING_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Kargil Property</title>
<style>
 html,body{height:100%;margin:0;font-family:'Segoe UI',Arial,sans-serif;
   background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0;}
 .box{height:100%;display:flex;flex-direction:column;align-items:center;
   justify-content:center;gap:18px;}
 .ring{width:54px;height:54px;border:5px solid rgba(255,255,255,.15);
   border-top-color:#0ea5e9;border-radius:50%;animation:spin 1s linear infinite;}
 @keyframes spin{to{transform:rotate(360deg)}}
 h1{font-size:22px;font-weight:600;margin:0;}
 p{margin:0;color:#94a3b8;font-size:14px;}
</style></head><body><div class="box">
 <div class="ring"></div>
 <h1>Kargil Property</h1>
 <p id="msg">Starting the application&hellip; please wait.</p>
</div></body></html>"""

ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>body{{font-family:'Segoe UI',Arial,sans-serif;background:#1e293b;
 color:#e2e8f0;padding:40px;line-height:1.6}}h1{{color:#f87171}}
 pre{{white-space:pre-wrap;background:#0f172a;padding:16px;border-radius:8px;
 color:#fca5a5}}</style></head><body>
 <h1>Could not start Kargil Property</h1><pre>{msg}</pre>
 <p>Details are in the <b>logs</b> folder next to the app.</p></body></html>"""


def run_gui():
    import webview

    err = preflight()
    stack = Stack()
    window = {"ref": None}

    def on_closed():
        log("Window closed by user.")
        stack.shutdown()

    def boot_thread():
        w = window["ref"]
        if err:
            w.load_html(ERROR_HTML.format(msg=err))
            return
        try:
            stack.boot()
            log(f"Loading app: {stack.url}")
            w.load_url(stack.url)
        except Exception as e:
            log(f"BOOT ERROR: {e}")
            stack.shutdown()
            w.load_html(ERROR_HTML.format(msg=str(e)))

    window["ref"] = webview.create_window(
        APP_NAME, html=LOADING_HTML, width=1280, height=820,
        min_size=(900, 600),
    )
    window["ref"].events.closed += on_closed
    # webview.start blocks until the window closes; boot runs in a worker.
    try:
        webview.start(boot_thread, gui="edgechromium")
    finally:
        # Belt-and-suspenders: guarantee a clean shutdown even if the
        # window 'closed' event did not fire (idempotent - safe to repeat).
        stack.shutdown()


def main():
    _ensure_std_streams()
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        run_gui()
    except Exception as e:
        log(f"FATAL: {e}")
        # last-ditch: open in browser so the user still gets the app
        try:
            import webbrowser
            err = preflight()
            if not err:
                stack = Stack()
                stack.boot()
                webbrowser.open(stack.url)
                log("Opened in default browser (fallback). Close this window to quit.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
                finally:
                    stack.shutdown()
        except Exception as e2:
            log(f"FALLBACK FAILED: {e2}")
            sys.exit(1)


if __name__ == "__main__":
    main()
