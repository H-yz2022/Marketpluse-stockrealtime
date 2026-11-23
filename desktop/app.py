"""China & HK Markets - the Windows desktop app's launcher (ChinaHKMarkets.exe, the file people download).

The app does not use the website. This launcher opens the window and runs the program itself on this PC:

- The program (desktop/core.py with the whole site inside, about 85 MB to download) is fetched once from the
  repository's "desktop" release into %LOCALAPPDATA%\\ChinaHKMarkets\\core and started as a local server that only
  this PC can reach. Market data comes straight from Tencent, Sina and East Money; results are computed here.
- Both parts keep themselves up to date from the release's version.json, quietly: a newer launcher or program is
  downloaded in the background while the app runs and is used from the next start. Nothing restarts or asks
  while the app is open (until 22 Nov 2026 the window closed and reopened, and a reload question popped up).
  Every download is checked against its SHA-256, and any failure keeps the current version.
- Started by the evening schedule (`--scheduled`), it first shows a small window saying what it is, with a choice:
  open the app, run in the background (minimised), or not tonight. With no answer it runs in the background.
- One window per PC (starting it again brings the open window forward), half the screen's width, centred. It
  remembers the language, text size and light / dark theme chosen in the app; Ctrl + / Ctrl - zoom everything;
  download buttons (CSV) save to Downloads; outside links open in the browser. Closing the window stops the program.

    python desktop/app.py [--scheduled]       # run from source (needs CHINA_HK_CORE)
    python desktop/build.py launcher          # build dist/ChinaHKMarkets.exe

For testing: CHINA_HK_CORE = path of a ChinaHKMarketsCore.exe to use as it is (no download, no update);
CHINA_HK_RELEASE = another release address (a folder works as file:///C:/...).
"""

from __future__ import annotations

import hashlib
import json
import lzma  # noqa: F401 - the program's zip is LZMA-compressed: make sure the packaged launcher can open it
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import webview

try:
    from build_info import BUILD  # written by the build; absent when run from source
except ImportError:
    BUILD = "dev"

RELEASE = os.environ.get("CHINA_HK_RELEASE",
                         "https://github.com/H-yz2022/Marketpluse-stockrealtime/releases/download/desktop/")
TITLE = "China & HK Markets · 尾盘选股"
HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ChinaHKMarkets"
CORE_DIR, DATA_DIR, SETTINGS = HOME / "core", HOME / "data", HOME / "settings.json"
CORE_EXE = Path("ChinaHKMarketsCore") / "ChinaHKMarketsCore.exe"
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", f"--disk-cache-size={32 * 1024 * 1024}")

STYLE = """<style>
body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#fcfcfb;color:#0b0b0b;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.card{max-width:540px;text-align:center;padding:32px}
h1{font-size:22px;margin:0 0 12px} p{color:#555;line-height:1.6;margin:0 0 20px}
button{font-size:15px;padding:10px 22px;border:0;border-radius:6px;background:#2a78d6;color:#fff;cursor:pointer}
#msg{margin-top:14px;color:#888;font-size:13px} small{color:#999}
.bar{height:8px;background:#e6e5df;border-radius:4px;overflow:hidden;margin:0 0 10px}
#fill{height:100%;width:0;background:#2a78d6;transition:width .3s}
pre{text-align:left;font-size:11px;color:#888;white-space:pre-wrap;max-height:180px;overflow:auto}
@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#fff}p{color:#bbb}.bar{background:#333}}
</style>"""
RETRY = """<button onclick="document.getElementById('msg').textContent='正在重试… Retrying…';pywebview.api.retry()">
重试 · Try again</button><div id="msg"></div>"""


def page(title: str, body: str, extra: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">{STYLE}</head><body><div class="card">'
            f"<h1>{title}</h1><p>{body}</p>{extra}<small>{BUILD[:7]}</small></div></body></html>")


STARTING = page("正在启动… · Starting…", "首次启动可能需要一分钟。<br>The first start can take a minute.")
SCHEDULED_WAIT = 60      # seconds before the evening start runs in the background on its own
CHOICE = page(
    "China &amp; HK Markets 自动启动 · Started for tonight",
    "A 股即将开盘（北京时间 09:30）。程序在后台按时计算各个选股，打开时结果已准备好。<br>"
    "The China market opens soon (09:30 Beijing). The app computes each screen on time in the background, so the "
    "results are ready when you look.",
    """<div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
<button onclick="pick('open')">打开窗口 · Open the app</button>
<button onclick="pick('background')" style="background:#555">在后台运行 · Run in the background</button>
<button onclick="pick('skip')" style="background:#999">今晚不用 · Not tonight</button></div>
<div id="msg"></div><p style="font-size:12px;margin-top:14px">在“任务计划程序”的 China HK Markets - trading hours
中可更改或关闭自动启动。<br>Change or turn off the evening start in Task Scheduler (China HK Markets - trading
hours).</p>
<script>var left=%d,done=false;function pick(m){if(done)return;done=true;
document.getElementById('msg').textContent='…';pywebview.api.choose(m)}
function tick(){if(done)return;document.getElementById('msg').textContent=left+' 秒后自动在后台运行 · Runs in the '+
'background in '+left+' s';if(left--<=0){pick('background')}else{setTimeout(tick,1000)}}
window.addEventListener('pywebviewready',tick)</script>""" % SCHEDULED_WAIT)
DOWNLOADING = page("正在下载程序 · Downloading the program",
                   "只需下载一次，之后直接启动。<br>This happens once; after that the app starts straight away.",
                   '<div class="bar"><div id="fill"></div></div><div id="msg">0%</div>'
                   "<script>function setProgress(p,t){document.getElementById('fill').style.width=p+'%';"
                   "document.getElementById('msg').textContent=t}</script>")
OFFLINE = page("无法下载程序 · Can't download the program",
               "首次启动需要联网下载程序（约 85 MB）。请检查网络后重试。<br>The first start needs the internet to "
               "download the program (about 85 MB). Check the connection and try again.", RETRY)


def failed(log_tail: str) -> str:
    safe = log_tail.replace("&", "&amp;").replace("<", "&lt;")
    return page("启动失败 · The program didn't start",
                f"日志在 · The log is in<br><small>{DATA_DIR / 'logs' / 'server.log'}</small>",
                f"<pre>{safe}</pre>{RETRY}")


# ------------------------------------------------------------------ small helpers

def fetch(name: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(RELEASE + name, headers={"User-Agent": "ChinaHKMarkets-desktop"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def remote_info() -> dict | None:
    """The release's version.json, or None when it can't be read (offline, GitHub unreachable)."""
    try:
        return json.loads(fetch("version.json", timeout=10))
    except (OSError, ValueError):
        return None


def log(text: str) -> None:
    """The launcher's own log (%LOCALAPPDATA%\\ChinaHKMarkets\\launcher.log): a windowed program has no console."""
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        path = HOME / "launcher.log"
        if path.exists() and path.stat().st_size > 500_000:
            path.unlink()
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{BUILD[:7]}] {text}\n")
    except OSError:
        pass


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_json(path: Path, obj: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def free_port(preferred: int = 8765) -> int:
    """The same port every time when it is free (the window's saved state belongs to the address), else any."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def already_running() -> bool:
    """One app per PC: a second start brings the open window forward instead."""
    if os.name != "nt":
        return False
    import ctypes

    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ChinaHKMarketsDesktop")
    if ctypes.windll.kernel32.GetLastError() != 183:              # ERROR_ALREADY_EXISTS
        return False
    hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)                   # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    return True


# ------------------------------------------------------------------ the launcher's own update

def remove_old_launchers() -> None:
    exe = Path(sys.executable)
    for old in exe.parent.glob(exe.stem + ".old*.exe"):           # left by the last update
        try:
            old.unlink()
        except OSError:
            pass


def update_launcher(info: dict | None, exe: Path | None = None) -> bool:
    """If the release has a newer launcher: download it, check it and put it in place of this file, quietly. This
    copy keeps running (a running program can be renamed); the new one starts next time. True when swapped."""
    if exe is None:
        if not getattr(sys, "frozen", False) or BUILD == "dev":
            return False
        exe = Path(sys.executable)
    try:
        if not info or not info.get("build") or info["build"] == BUILD:
            return False
        data = fetch("ChinaHKMarkets.exe", timeout=120)
        if hashlib.sha256(data).hexdigest() != info.get("sha256"):
            return False                                          # incomplete or altered download: keep this one
        new = exe.with_name(exe.stem + ".new.exe")
        new.write_bytes(data)
        exe.rename(exe.with_name(f"{exe.stem}.old{int(time.time())}.exe"))
        new.rename(exe)
        log(f"launcher {info['build'][:7]} ready for the next start")
        return True
    except Exception:  # noqa: BLE001 - never let an update disturb the app
        return False


# ------------------------------------------------------------------ the program (core)

def installed_core() -> tuple[str, Path] | None:
    """(build, exe) of the program to run: CHINA_HK_CORE when set, else the installed one."""
    override = os.environ.get("CHINA_HK_CORE")
    if override:
        return ("override", Path(override)) if Path(override).exists() else None
    build = load_json(CORE_DIR / "current.json").get("build")
    exe = CORE_DIR / build / CORE_EXE if build else None
    return (build, exe) if exe is not None and exe.exists() else None


def download_core(core: dict, progress=None) -> bool:
    """Download the program's zip, check its SHA-256, unpack it next to the others and make it the current one."""
    build = core["build"]
    target, part = CORE_DIR / build, CORE_DIR / f"{build}.part"
    tmp = HOME / "update" / f"core-{build}.zip"
    try:
        if not (target / CORE_EXE).exists():
            tmp.parent.mkdir(parents=True, exist_ok=True)
            h, done, total = hashlib.sha256(), 0, core.get("size") or 1
            req = urllib.request.Request(RELEASE + core.get("file", "ChinaHKMarketsCore.zip"),
                                         headers={"User-Agent": "ChinaHKMarkets-desktop"})
            with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                while block := r.read(1 << 18):
                    f.write(block)
                    h.update(block)
                    done += len(block)
                    if progress:
                        progress(min(done / total, 1.0) * 0.9, f"{done / 1e6:.0f} / {total / 1e6:.0f} MB")
            if h.hexdigest() != core.get("sha256"):
                return False                                      # incomplete or altered download
            if progress:
                progress(0.92, "正在安装… · Installing…")
            shutil.rmtree(part, ignore_errors=True)
            with zipfile.ZipFile(tmp) as z:
                z.extractall(part)
            shutil.rmtree(target, ignore_errors=True)
            part.rename(target)
        save_json(CORE_DIR / "current.json", {"build": build})
        return (target / CORE_EXE).exists()
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def remove_old_cores(keep: str) -> None:
    if not CORE_DIR.exists():
        return
    for d in CORE_DIR.iterdir():
        if d.is_dir() and d.name != keep:
            shutil.rmtree(d, ignore_errors=True)


class Server:
    """The program's local server process (only this PC can reach it)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.port = 0

    def start(self, exe: Path) -> None:
        self.stop()
        self.port = free_port()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, STOCKRT_DATA_DIR=str(DATA_DIR), STOCKRT_RESULTS_DIR=str(DATA_DIR / "results"),
                   STOCKRT_DESKTOP="1", STOCKRT_DATA_MODE="live", PYTHONIOENCODING="utf-8",
                   PYINSTALLER_RESET_ENVIRONMENT="1")              # the program is its own packaged application
        self.proc = subprocess.Popen([str(exe), "--server", str(self.port)], env=env, cwd=str(exe.parent),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/_stcore/health", timeout=3) as r:
                return r.read().strip() == b"ok"
        except OSError:
            return False

    def wait(self, seconds: float = 240) -> bool:
        t0 = time.time()
        while time.time() - t0 < seconds:
            if self.proc is None or self.proc.poll() is not None:
                return False
            if self.ready():
                return True
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()                                  # its workers stop with it (core.exit_with_parent)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def url(self, settings: dict) -> str:
        """The page address with the remembered language, text size and theme (Streamlit's embed_options sets the
        light or dark theme; without it the window follows Windows)."""
        q = {"embed": "true", "app": "desktop", "lang": settings.get("lang", "zh"), "fs": settings.get("fs", "m"),
             "theme": settings.get("theme", "system")}
        option = {"light": "light_theme", "dark": "dark_theme"}.get(q["theme"])
        if option:
            q["embed_options"] = option
        return f"http://127.0.0.1:{self.port}/?" + urllib.parse.urlencode(q)


# ------------------------------------------------------------------ the window

class App:
    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self.server = Server()
        self.settings = load_json(SETTINGS)
        self.core_build: str | None = None
        self.lock = threading.Lock()
        self.closed = threading.Event()         # the window has been closed: every background loop stops
        self.scheduled = False                  # started by the evening schedule: show the choice first
        self.updating = False

    def log_tail(self) -> str:
        try:
            return (DATA_DIR / "logs" / "server.log").read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            return ""

    def run(self) -> None:
        """In the background once the window is up: update, make sure the program is there, start it, show it."""
        if not self.lock.acquire(blocking=False):
            return
        try:
            w = self.window
            info = remote_info()
            core = installed_core()
            if core is None:
                if not info or not info.get("core"):
                    w.load_html(OFFLINE)
                    return
                w.load_html(DOWNLOADING)
                shown = [0.0]

                def progress(f: float, text: str) -> None:
                    if time.time() - shown[0] < 0.3 and f < 0.9:   # the page may still be loading: never fail on it
                        return
                    shown[0] = time.time()
                    try:
                        w.evaluate_js(f"window.setProgress && setProgress({f * 100:.0f}, "
                                      f"{json.dumps(f'{f * 100:.0f}% · {text}')})")
                    except Exception:  # noqa: BLE001 - only the display
                        pass

                log(f"downloading the program {info['core'].get('build')}")
                if not download_core(info["core"], progress):
                    log("download failed")
                    w.load_html(OFFLINE)
                    return
                core = installed_core()
                w.load_html(STARTING)
            self.core_build, exe = core
            self.server.start(exe)
            if not self.server.wait():
                log(f"the program {self.core_build} didn't start")
                w.load_html(failed(self.log_tail()))
                return
            log(f"program {self.core_build} running on port {self.server.port}")
            w.load_url(self.server.url(self.settings))
            if self.core_build != "override":
                remove_old_cores(keep=self.core_build)
            threading.Thread(target=self.update_quietly, args=(info,), daemon=True).start()
        except Exception as e:  # noqa: BLE001 - show it rather than leave a blank window
            import traceback

            log("error: " + traceback.format_exc()[-1500:])
            if not self.closed.is_set():
                self.window.load_html(page("出错了 · Something went wrong", str(e)[:300], RETRY))
        finally:
            self.lock.release()

    def update_quietly(self, info: dict | None) -> None:
        """Newer versions are downloaded while this one runs and used from the next start: no restart, no question,
        nothing for the person to do. (The running program keeps its folder until then.)"""
        if self.updating:
            return
        self.updating = True
        update_launcher(info)
        core = (info or {}).get("core")
        if core and self.core_build != "override" and core.get("build") != self.core_build and download_core(core):
            log(f"program {core.get('build')} ready for the next start")

    def choose(self, mode: str) -> None:
        """The evening start's choice: open the window, run minimised in the background, or close for tonight."""
        log(f"evening start: {mode}")
        w = self.window
        if mode == "skip":
            w.destroy()
            return
        if mode == "open":
            size = half_screen()
            w.resize(size["width"], size["height"])
            if "x" in size:
                w.move(size["x"], size["y"])
        else:
            w.minimize()
        w.load_html(STARTING)
        threading.Thread(target=self.run, daemon=True).start()

    def follow_language(self) -> None:
        """Keep the language, text size and theme chosen in the app (address parameters) for the next start."""
        allowed = {"lang": ("zh", "en"), "fs": ("m", "l", "xl"), "theme": ("system", "light", "dark")}
        while not self.closed.wait(3):
            try:
                url = self.window.get_current_url() or ""
            except Exception:  # noqa: BLE001 - the window is closing
                return
            parts = urllib.parse.urlparse(url)
            q = urllib.parse.parse_qs(parts.query)
            changed = False
            for key, ok in allowed.items():
                value = q.get(key, [None])[0]
                if value in ok and value != self.settings.get(key):
                    self.settings[key], changed = value, True
            if changed:
                save_json(SETTINGS, self.settings)
            reload = theme_reload(url)
            if reload:
                try:
                    self.window.load_url(reload)
                except Exception:  # noqa: BLE001 - the window is closing
                    return

    def started(self) -> None:
        threading.Thread(target=self.follow_language, daemon=True).start()
        if not self.scheduled:
            self.run()


def theme_reload(url: str) -> str | None:
    """The address to reload when the theme chosen in the app (`theme`) differs from the one the page was loaded
    with (Streamlit's `embed_options`), else None."""
    parts = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parts.query)
    if parts.scheme != "http" or "theme" not in q:
        return None
    want = {"light": "light_theme", "dark": "dark_theme"}.get(q["theme"][0])
    if want == q.get("embed_options", [None])[0]:
        return None
    if want:
        q["embed_options"] = [want]
    else:
        q.pop("embed_options", None)
    return parts._replace(query=urllib.parse.urlencode(q, doseq=True)).geturl()


class Api:
    """Called from the launcher's own pages. The app is kept in an underscore attribute: pywebview exposes the api
    object's public attributes to the page by walking them, and walking a Window from its own thread froze the app."""

    def __init__(self, app: App) -> None:
        self._app = app

    def retry(self) -> None:
        threading.Thread(target=self._app.run, daemon=True).start()

    def choose(self, mode: str) -> None:
        if mode in ("open", "background", "skip"):
            threading.Thread(target=self._app.choose, args=(mode,), daemon=True).start()


def half_screen() -> dict:
    """Half the screen's width and most of its height, centred (in the screen's own units)."""
    try:
        s = webview.screens[0]
        w, h = max(s.width // 2, 640), max(int(s.height * 0.85), 500)
        return {"width": w, "height": h, "x": (s.width - w) // 2, "y": max((s.height - h) // 2, 0)}
    except Exception:  # noqa: BLE001 - any trouble reading the screen: a sensible fixed size
        return {"width": 960, "height": 760}


def main() -> int:
    if already_running():
        return 0
    HOME.mkdir(parents=True, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    remove_old_launchers()
    app = App()
    app.scheduled = "--scheduled" in sys.argv[1:]
    if app.scheduled:                       # a small window that says what it is, without taking the keyboard
        size = {"width": 620, "height": 440}
        try:
            s = webview.screens[0]
            size.update(x=(s.width - 620) // 2, y=max((s.height - 440) // 2, 0))
        except Exception:  # noqa: BLE001 - the system places it
            pass
        app.window = webview.create_window(TITLE, html=CHOICE, js_api=Api(app), min_size=(480, 360),
                                           text_select=True, zoomable=True, focus=False, **size)
    else:
        app.window = webview.create_window(TITLE, html=STARTING, js_api=Api(app), min_size=(640, 500),
                                           text_select=True, zoomable=True, **half_screen())
    app.window.events.closed += app.closed.set
    log("started (evening schedule)" if app.scheduled else "started")
    try:
        webview.start(app.started, private_mode=False, storage_path=str(HOME))
    finally:
        app.closed.set()
        app.server.stop()
        log("closed")
    os._exit(0)     # nothing may keep the process alive once the window is gone (an earlier version lingered)


if __name__ == "__main__":
    sys.exit(main())
