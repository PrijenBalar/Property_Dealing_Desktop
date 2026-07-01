# -*- coding: utf-8 -*-
"""
Kargil Property - self-extracting Setup.

A single Setup.exe (PyInstaller onefile) carrying the whole app
(KargilProperty.exe + portable PHP/MariaDB runtime + the existing database)
inside it as payload.zip. When run it:

  1. extracts everything to a writable per-user folder. Default is chosen
     automatically: %LOCALAPPDATA%\\KargilProperty if the system drive has
     plenty of room, otherwise the fixed drive with the most free space
     (MariaDB cannot write inside Program Files). The user can override with
     the "Change..." button.
  2. REGISTERS the app with Windows (per-user, no admin) so it appears in
     Settings > Apps > Installed apps with a working Uninstall button,
  3. creates Desktop + Start-Menu shortcuts (app + uninstall),
  4. offers to launch the app.

Headless modes (used by the build / for scripted installs):
  Setup.exe --selftest <dir>   extract + verify the embedded payload
  Setup.exe --install  <dir>   extract + register, no GUI (prints result)
"""
import os
import sys
import string
import shutil
import zipfile
import subprocess
import threading
import traceback
import tempfile

APP_NAME        = "Kargil Property"
INSTALL_DIRNAME = "KargilProperty"
MAIN_EXE        = "KargilProperty.exe"
PAYLOAD         = "payload.zip"
CREATE_NO_WINDOW = 0x08000000
NEEDED_BYTES    = 350 * 1024 * 1024              # ~350 MB required to install
PREFER_LOCAL    = 2 * 1024 * 1024 * 1024         # prefer %LOCALAPPDATA% if >= 2 GB free
REG_KEY         = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\KargilProperty"


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def free_bytes(path: str) -> int:
    probe = os.path.abspath(path)
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except Exception:
        return -1


def fixed_drives():
    for letter in string.ascii_uppercase:
        root = "%s:\\" % letter
        if os.path.exists(root):
            yield root


def default_install_dir() -> str:
    """Pick a sensible, writable default that actually has room."""
    la = os.environ.get("LOCALAPPDATA")
    if la and free_bytes(la) >= PREFER_LOCAL:
        return os.path.join(la, INSTALL_DIRNAME)
    # otherwise the emptiest fixed drive
    best, best_free = None, -1
    for root in fixed_drives():
        fb = free_bytes(root)
        if fb > best_free:
            best, best_free = root, fb
    if best:
        return os.path.join(best, INSTALL_DIRNAME)
    if la:
        return os.path.join(la, INSTALL_DIRNAME)
    return os.path.join(os.path.expanduser("~"), INSTALL_DIRNAME)


def payload_uncompressed_kb() -> int:
    with zipfile.ZipFile(resource_path(PAYLOAD)) as z:
        return max(1, sum(m.file_size for m in z.infolist()) // 1024)


def extract_payload(dest: str, progress=None) -> str:
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(resource_path(PAYLOAD)) as z:
        members = z.infolist()
        total = max(1, len(members))
        for i, m in enumerate(members):
            z.extract(m, dest)
            if progress and (i % 25 == 0 or i == total - 1):
                progress(i / total * 100.0, "Installing files...  %d/%d" % (i + 1, total))
    return os.path.join(dest, MAIN_EXE)


# PowerShell that creates shortcuts AND registers the app with Windows so it
# shows in Settings > Apps. The Uninstall command is passed as an
# -EncodedCommand (base64) to avoid any quoting problems, and runs from
# powershell.exe (outside the install dir) so it can delete the whole folder.
_PS_REGISTER = r"""
$ErrorActionPreference = 'SilentlyContinue'
$W        = New-Object -ComObject WScript.Shell
$desktop  = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$target   = '__EXE__'
$wd       = '__DIR__'
$appName  = 'Kargil Property.lnk'

foreach ($loc in @($desktop, $programs)) {
    if (-not $loc) { continue }
    $lnk = Join-Path $loc $appName
    $s = $W.CreateShortcut($lnk)
    $s.TargetPath = $target; $s.WorkingDirectory = $wd; $s.IconLocation = $target
    $s.Description = 'Kargil Property'; $s.Save()
}

$dLnk = (Join-Path $desktop  $appName)
$pLnk = (Join-Path $programs $appName)
$uLnk = (Join-Path $programs 'Uninstall Kargil Property.lnk')

$uninstScript = @"
Start-Sleep -Milliseconds 400
Remove-Item -LiteralPath '$wd' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\KargilProperty' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath '$dLnk','$pLnk','$uLnk' -Force -ErrorAction SilentlyContinue
"@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($uninstScript))
$uninstallCmd = 'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $enc

$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\KargilProperty'
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name 'DisplayName'     -Value 'Kargil Property'
Set-ItemProperty -Path $key -Name 'DisplayVersion'  -Value '1.0.0'
Set-ItemProperty -Path $key -Name 'Publisher'       -Value 'Kargil Property'
Set-ItemProperty -Path $key -Name 'DisplayIcon'     -Value $target
Set-ItemProperty -Path $key -Name 'InstallLocation' -Value $wd
Set-ItemProperty -Path $key -Name 'UninstallString' -Value $uninstallCmd
Set-ItemProperty -Path $key -Name 'NoModify' -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name 'NoRepair' -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name 'EstimatedSize' -Value __KB__ -Type DWord

if ($programs) {
    $u = $W.CreateShortcut($uLnk)
    $u.TargetPath   = 'powershell.exe'
    $u.Arguments    = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $enc
    $u.IconLocation = $target
    $u.Description  = 'Uninstall Kargil Property'
    $u.Save()
}
Write-Output 'REGISTERED'
"""


def register_and_shortcuts(install_dir: str, estimated_kb: int):
    exe = os.path.join(install_dir, MAIN_EXE)
    ps = (_PS_REGISTER.replace("__EXE__", exe)
                      .replace("__DIR__", install_dir)
                      .replace("__KB__", str(int(estimated_kb))))
    tmp = os.path.join(tempfile.gettempdir(), "_kp_register.ps1")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(ps)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", tmp],
        creationflags=CREATE_NO_WINDOW, timeout=90,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        os.remove(tmp)
    except OSError:
        pass
    return "REGISTERED" in (r.stdout or "")


# --------------------------------------------------------------------------- #
# Headless modes
# --------------------------------------------------------------------------- #
def _verify(dest: str) -> bool:
    exe = os.path.join(dest, MAIN_EXE)
    checks = [
        ("main exe", os.path.isfile(exe)),
        ("runtime/www", os.path.isdir(os.path.join(dest, "runtime", "www"))),
        ("php.exe", os.path.isfile(os.path.join(dest, "runtime", "php", "php.exe"))),
        ("mariadbd", os.path.isfile(os.path.join(dest, "runtime", "mariadb", "bin", "mariadbd.exe"))),
        ("database", os.path.isdir(os.path.join(dest, "data", "kargil_property1"))),
    ]
    for label, cond in checks:
        print("  %-11s: %s" % (label, cond))
    return all(c for _, c in checks)


def selftest(dest: str) -> int:
    try:
        extract_payload(dest)
        ok = _verify(dest)
        print("SELFTEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    except Exception:
        traceback.print_exc()
        return 2


def install_headless(dest: str) -> int:
    try:
        print("Installing to:", dest)
        extract_payload(dest)
        reg = register_and_shortcuts(dest, payload_uncompressed_kb())
        ok = _verify(dest)
        print("registered :", reg)
        print("INSTALL:", "PASS" if (ok and reg) else "FAIL")
        return 0 if (ok and reg) else 1
    except Exception:
        traceback.print_exc()
        return 2


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    root = tk.Tk()
    root.title(APP_NAME + " - Setup")
    root.geometry("600x360")
    root.resizable(False, False)

    state = {"dir": default_install_dir(), "done": False, "exe": None}

    head = tk.Frame(root, bg="#0d6efd", height=78)
    head.pack(fill="x")
    tk.Label(head, text="Kargil Property", bg="#0d6efd", fg="white",
             font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=24, pady=(16, 0))
    tk.Label(head, text="Setup - installs the app and its database on this PC",
             bg="#0d6efd", fg="#dbe9ff", font=("Segoe UI", 9)).pack(anchor="w", padx=24)

    body = tk.Frame(root, padx=24, pady=18)
    body.pack(fill="both", expand=True)
    tk.Label(body, text="Install Kargil Property (with all existing data) to:",
             font=("Segoe UI", 9)).pack(anchor="w")

    path_row = tk.Frame(body)
    path_row.pack(anchor="w", fill="x", pady=(2, 4))
    path_var = tk.StringVar(value=state["dir"])
    tk.Label(path_row, textvariable=path_var, font=("Segoe UI", 9, "bold"),
             fg="#0d6efd", anchor="w").pack(side="left")

    free_lbl = tk.Label(body, text="", font=("Segoe UI", 8), fg="#888")
    free_lbl.pack(anchor="w", pady=(0, 10))

    def refresh_free():
        fb = free_bytes(state["dir"])
        if fb < 0:
            free_lbl.config(text="", fg="#888")
        elif fb < NEEDED_BYTES:
            free_lbl.config(text="Warning: only %.0f MB free here (about 300 MB needed)." % (fb / 1048576.0), fg="#c00")
        else:
            free_lbl.config(text="%.1f GB free on this drive." % (fb / 1073741824.0), fg="#888")

    def choose():
        picked = filedialog.askdirectory(title="Choose a folder to install Kargil Property into")
        if picked:
            state["dir"] = os.path.join(os.path.normpath(picked), INSTALL_DIRNAME)
            path_var.set(state["dir"])
            refresh_free()

    change_btn = tk.Button(path_row, text="Change...", command=choose)
    change_btn.pack(side="left", padx=(10, 0))

    bar = ttk.Progressbar(body, length=552, mode="determinate", maximum=100.0)
    bar.pack(anchor="w", pady=(6, 0))
    status = tk.Label(body, text="Ready to install.", font=("Segoe UI", 9), fg="#555")
    status.pack(anchor="w", pady=(8, 0))

    btns = tk.Frame(body)
    btns.pack(side="bottom", anchor="e", pady=(16, 0), fill="x")

    def ui(fn):
        root.after(0, fn)

    def set_progress(pct, msg):
        ui(lambda: (bar.config(value=pct), status.config(text=msg)))

    def worker():
        try:
            set_progress(2, "Preparing...")
            exe = extract_payload(state["dir"], progress=set_progress)
            set_progress(100, "Registering with Windows & creating shortcuts...")
            register_and_shortcuts(state["dir"], payload_uncompressed_kb())
            state["exe"] = exe
            state["done"] = True
            ui(finished_ok)
        except PermissionError:
            ui(lambda: fail("A file is in use. If Kargil Property is running, close it and run Setup again."))
        except OSError as e:
            if getattr(e, "errno", None) == 28:
                ui(lambda: fail("Not enough disk space on the selected drive.\n\nClick 'Change...' and pick a drive with more free space."))
            else:
                ui(lambda: fail("Installation failed:\n\n" + traceback.format_exc()))
        except Exception:
            ui(lambda: fail("Installation failed:\n\n" + traceback.format_exc()))

    def start():
        fb = free_bytes(state["dir"])
        if 0 <= fb < NEEDED_BYTES:
            if not messagebox.askyesno(APP_NAME + " - Setup",
                    "This drive has only %.0f MB free (about 300 MB is needed).\n\nInstall here anyway?" % (fb / 1048576.0)):
                return
        install_btn.config(state="disabled")
        cancel_btn.config(state="disabled")
        change_btn.config(state="disabled")
        status.config(text="Installing...")
        threading.Thread(target=worker, daemon=True).start()

    def finished_ok():
        status.config(text="Installation complete. (Listed in Settings > Apps for uninstall.)")
        install_btn.config(text="Launch Now", state="normal", command=launch)
        cancel_btn.config(text="Finish", state="normal", command=root.destroy)

    def fail(msg):
        bar.config(value=0)
        status.config(text="Installation failed.")
        messagebox.showerror(APP_NAME + " - Setup", msg)
        install_btn.config(state="normal")
        cancel_btn.config(state="normal")
        change_btn.config(state="normal")

    def launch():
        try:
            os.startfile(state["exe"])
        except Exception:
            pass
        root.destroy()

    cancel_btn = tk.Button(btns, text="Cancel", width=12, command=root.destroy)
    cancel_btn.pack(side="right", padx=(8, 0))
    install_btn = tk.Button(btns, text="Install", width=14, command=start,
                            bg="#0d6efd", fg="white", activebackground="#0b5ed7",
                            font=("Segoe UI", 9, "bold"))
    install_btn.pack(side="right")

    refresh_free()
    root.mainloop()


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        dest = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(tempfile.gettempdir(), "kp_setup_selftest")
        sys.exit(selftest(dest))
    if len(sys.argv) >= 3 and sys.argv[1] == "--install":
        sys.exit(install_headless(sys.argv[2]))
    run_gui()


if __name__ == "__main__":
    main()
