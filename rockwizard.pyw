"""RockWizard desktop app: shows the interface in its own window. The server (also used by the
phone web app) runs as its own background process (rockwizard_server.pyw): this window starts it
when it isn't running, and Settings has Stop/Start. Closing the window also stops a server it
started itself, unless "Start with Windows" is on (then the phone keeps working)."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

import webview  # noqa: E402

from chc import startup  # noqa: E402

PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
window = None
started_here = False

STOPPED = """<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;
background:#0c0d1a;color:#e8e8f4;font-family:'Segoe UI',system-ui,sans-serif">
<div style="text-align:center;max-width:420px;padding:24px">
<div style="font-size:22px;font-weight:700;margin-bottom:8px">RockWizard server is stopped</div>
<div style="color:#9a9cc0;margin-bottom:22px">Nothing is running in the background, and your phone can't connect.
Start it again to use RockWizard.</div>
<button id="go" onclick="this.textContent='Starting…';this.disabled=true;pywebview.api.start_server()"
style="border:0;border-radius:10px;padding:12px 26px;font-size:15px;font-weight:600;color:#fff;cursor:pointer;
background:linear-gradient(135deg,#8b5cf6,#ec4899)">Start server</button></div></body></html>"""


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def launch_server() -> bool:
    """Start rockwizard_server.pyw as its own process and wait until it answers."""
    exe = Path(sys.executable).with_name("pythonw.exe")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe if exe.exists() else sys.executable), str(ROOT / "rockwizard_server.pyw")],
                     cwd=ROOT, creationflags=flags, close_fds=True)
    for _ in range(300):  # first start can take a while (loading the chart models' libraries)
        if port_open():
            return True
        time.sleep(0.1)
    return False


def stop_server():
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "api/server/stop", method="POST", data=b""), timeout=3)
    except OSError:
        pass


class Api:
    """Called from the page (window.pywebview.api) for things only the PC can do."""

    def pick_folder(self):
        r = window.create_file_dialog(webview.FOLDER_DIALOG)
        return r[0] if r else None

    def pick_file(self):
        r = window.create_file_dialog(webview.OPEN_DIALOG, file_types=("Programs (*.exe)", "All files (*.*)"))
        return r[0] if r else None

    def server_stopped(self):
        """The page asked the server to stop: wait for it to go, then show the stopped screen."""
        global started_here
        for _ in range(50):
            if not port_open():
                break
            time.sleep(0.1)
        started_here = False
        window.load_html(STOPPED)

    def start_server(self):
        global started_here
        if port_open() or launch_server():
            started_here = started_here or not startup.enabled()
            window.load_url(URL)
            return True
        window.load_html(STOPPED.replace("is stopped", "didn't start").replace(
            "Start it again", "See logs/rockwizard-server.log, or try again"))
        return False


def on_closed():
    if started_here and not startup.enabled():
        stop_server()


def own_taskbar_icon():
    """Windows files a window under the program that runs it, which here is Python, and shows
    Python's icon on the taskbar. With an identity of its own the taskbar shows the window's icon."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RockWizard.Desktop")


def main():
    global window, started_here
    own_taskbar_icon()
    if not port_open():
        started_here = launch_server()
    up = port_open()
    window = webview.create_window("RockWizard", URL if up else None, html=None if up else STOPPED, js_api=Api(),
                                   width=1200, height=820, min_size=(380, 600), background_color="#0c0d1a")
    window.events.closed += on_closed
    webview.start(icon=str(ROOT / "rockwizard.ico"))


if __name__ == "__main__":
    main()
