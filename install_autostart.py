#!/usr/bin/env python3
"""Make the Drip House shop system start by itself when you sign in to your computer.

    python install_autostart.py            install (run it once)
    python install_autostart.py remove     switch the automatic start off

Mac:      use  python3 install_autostart.py
Windows:  use  python install_autostart.py   (or py install_autostart.py)

Run it from the folder that contains app.py and index.html. It copies them to
~/DripHouse (your data is copied too), then starts the server in the background
every time you sign in. Run it again after you replace app.py or index.html to update.
"""
import os, plistlib, shutil, socket, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

LABEL = "com.driphouse.shop"
HOME = Path.home()
TARGET = HOME / "DripHouse"          # a plain folder in your home: background programs may read it (Desktop/Documents are blocked on a Mac)
SRC = Path(__file__).resolve().parent
PORT = os.environ.get("PORT", "8000")
PROTECTED = [HOME / d for d in ("Desktop", "Documents", "Downloads")]


def say(msg=""): print(msg, flush=True)


def die(msg):
    print("\n" + msg + "\n", file=sys.stderr); sys.exit(1)


def run(cmd, quiet=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode and not quiet: say("  (" + " ".join(cmd[:3]) + " said: " + (r.stderr or r.stdout).strip()[:200] + ")")
    return r.returncode


def in_protected(p):
    p = Path(p).resolve()
    return any(d == p or d in p.parents for d in PROTECTED)


def port_busy():
    s = socket.socket(); s.settimeout(1)
    try: return s.connect_ex(("127.0.0.1", int(PORT))) == 0
    finally: s.close()


def wait_ready(seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        try: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2); return True
        except Exception: time.sleep(0.7)
    return False


def deploy():
    """Copy the program (and your existing data) to ~/DripHouse. Never overwrites an existing database."""
    TARGET.mkdir(exist_ok=True)
    if SRC.resolve() == TARGET.resolve(): return
    for f in ("app.py", "index.html"):
        if not (SRC / f).exists(): die(f"{f} is not next to install_autostart.py. Put them all in the same folder and try again.")
        shutil.copy2(SRC / f, TARGET / f)
    for f in ("README.md", "schema.sql", "start.bat"):
        if (SRC / f).exists(): shutil.copy2(SRC / f, TARGET / f)
    old, new = SRC / "workshop.db", TARGET / "workshop.db"
    if old.exists() and not new.exists():
        a, b = sqlite3.connect(old), sqlite3.connect(new)
        a.backup(b); b.close(); a.close()  # safe copy of your customers, sales and products
        say(f"Copied your data to {new}")


# ---------------------------------------------------------------- macOS
def pick_python_mac():
    for p in (sys.executable, shutil.which("python3"), "/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if not p or not Path(p).exists() or in_protected(p): continue
        r = subprocess.run([p, "-c", "import sys;print(sys.version_info>=(3,8))"], capture_output=True, text=True)
        if r.stdout.strip() == "True": return p
    die("Could not find a suitable Python 3 (3.8 or newer) on this Mac.")


def build_plist(py, app_dir):
    app_dir = Path(app_dir); env = {"NO_BROWSER": "1", "PYTHONUNBUFFERED": "1"}
    if "PORT" in os.environ: env["PORT"] = PORT
    return plistlib.dumps({
        "Label": LABEL, "ProgramArguments": [str(py), str(app_dir / "app.py")], "WorkingDirectory": str(app_dir),
        "EnvironmentVariables": env, "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
        "StandardOutPath": str(app_dir / "server.log"), "StandardErrorPath": str(app_dir / "server.log")})


def mac_plist_path(): return HOME / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def mac_stop():
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], quiet=True)
    if mac_plist_path().exists(): run(["launchctl", "unload", str(mac_plist_path())], quiet=True)


def mac_start():
    plist = mac_plist_path(); plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(build_plist(pick_python_mac(), TARGET))
    if run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)]) != 0:  # older macOS
        if run(["launchctl", "load", "-w", str(plist)]) != 0: die("macOS would not start the background service. Please tell me the message above.")


# ---------------------------------------------------------------- Windows
def build_bat(py):
    py = str(py)
    if py.lower().endswith("pythonw.exe"): py = py[:-len("pythonw.exe")] + "python.exe"
    return ("@echo off\r\nchcp 65001 >nul\r\ncd /d \"%~dp0\"\r\nset NO_BROWSER=1\r\nset PYTHONUNBUFFERED=1\r\n"
            + (f"set PORT={PORT}\r\n" if "PORT" in os.environ else "") + f"\"{py}\" \"%~dp0app.py\" >> server.log 2>&1\r\n")


def build_vbs(bat):  # runs the .bat with no window
    return 'CreateObject("WScript.Shell").Run Chr(34) & "' + str(bat).replace('"', '""') + '" & Chr(34), 0, False\r\n'


def win_startup_file(): return Path(os.environ.get("APPDATA", HOME)) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "DripHouse.vbs"


def win_stop():
    ps = "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*DripHouse*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    run(["powershell", "-NoProfile", "-Command", ps], quiet=True)


def win_start():
    bat = TARGET / "run-server.bat"; bat.write_text(build_bat(sys.executable), encoding="utf-8")
    vbs = win_startup_file(); vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(build_vbs(bat), encoding="utf-16")  # UTF-16 so folder names with any letters work
    subprocess.Popen(["wscript", str(vbs)])


# ---------------------------------------------------------------- main
def main():
    mac, win = sys.platform == "darwin", os.name == "nt"
    if not (mac or win): die("This installer supports macOS and Windows. On Linux, just run: python3 app.py")
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("remove", "uninstall", "off"):
        (mac_stop if mac else win_stop)()
        for f in ((mac_plist_path(),) if mac else (win_startup_file(),)):
            if f.exists(): f.unlink()
        say(f"Automatic start is switched off. Your data is still safe in {TARGET}.")
        return
    say("Setting up automatic start for the Drip House shop system...")
    (mac_stop if mac else win_stop)()   # stop our own older copy, if any
    time.sleep(1)
    if port_busy():
        die(f"Something is already running on port {PORT}. This is probably the server you started in VS Code.\n"
            "Click in its terminal, press Ctrl+C to stop it, then run this installer again.")
    deploy()
    fresh = not (TARGET / "workshop.db").exists()
    (mac_start if mac else win_start)()
    say("Starting the server...")
    if not wait_ready():
        log = TARGET / "server.log"; tail = "\n".join(log.read_text(errors="ignore").splitlines()[-8:]) if log.exists() else "(no log yet)"
        die(f"The server did not answer. Last lines of {log}:\n{tail}")
    if fresh:
        for line in (TARGET / "server.log").read_text(errors="ignore").splitlines():
            if "RECOVERY KEY" in line or "WARNING" in line: say(line.strip("* "))
        say("^ Write the recovery key down. It is shown only once.")
    say(f"\nDone. The shop system now starts by itself every time you sign in.\nOpen:  http://localhost:{PORT}   (bookmark it)\n"
        f"Program and data folder: {TARGET}\nTo update later: replace app.py / index.html here, then run this installer again.")


if __name__ == "__main__":
    main()
