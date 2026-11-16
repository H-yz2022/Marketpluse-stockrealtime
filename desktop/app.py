"""China & HK Markets - the Windows desktop app.

The website in its own window: an app icon, a title, the taskbar and Start menu like any program, and no browser
tabs or address bar. It shows https://china-hk-markets.streamlit.app/ through Windows' built-in Edge engine
(WebView2), so it always has the latest version of the site and needs nothing else installed.

- The site loads without the hosting service's own buttons (Fork, GitHub, the Streamlit badge): the app opens the
  site's inner page in embed mode, and tells the site it is the desktop app (`app=desktop`) so website-only parts
  such as the "download the app" link are left out. If the site is asleep, Streamlit's wake-up page shows first,
  then the app switches to the clean view as soon as the site answers.
- It updates itself: at each start it compares its build with the newest one on GitHub (the "desktop" release) and,
  if a newer one exists, downloads it, checks its SHA-256, swaps it in and restarts. Any failure keeps the current
  version running.
- The window opens at half the screen's width, centred, on a "connecting" page; it remembers the language chosen on
  the site; download buttons (CSV) save to Downloads; outside links open in the browser; without a connection it says
  so and offers to try again. The Edge cache is capped at 32 MB.

    python desktop/app.py                     # run from source (never updates itself)
    bash desktop/build.sh                     # build dist/ChinaHKMarkets.exe (GitHub builds it on each push too)

Set CHINA_HK_URL to point it at another address (for example a local copy: http://localhost:8501/).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import webview

try:
    from build_info import BUILD  # written by the GitHub build; absent in a local build
except ImportError:
    BUILD = "dev"

BASE = os.environ.get("CHINA_HK_URL", "https://china-hk-markets.streamlit.app/").rstrip("/") + "/"
HOSTED = BASE.endswith(".streamlit.app/")
INNER = BASE + "~/+/" if HOSTED else BASE          # the app itself, without the hosting service's frame
RELEASE = "https://github.com/H-yz2022/Marketpluse-stockrealtime/releases/download/desktop/"
TITLE = "China & HK Markets · 尾盘选股"
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", f"--disk-cache-size={32 * 1024 * 1024}")

STYLE = """<style>
body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#fcfcfb;color:#0b0b0b;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.card{max-width:520px;text-align:center;padding:32px}
h1{font-size:22px;margin:0 0 12px} p{color:#555;line-height:1.6;margin:0 0 20px}
button{font-size:15px;padding:10px 22px;border:0;border-radius:6px;background:#2a78d6;color:#fff;cursor:pointer}
#msg{margin-top:14px;color:#888;font-size:13px} small{color:#999}
@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#fff}p{color:#bbb}}
</style>"""


def page(title: str, body: str, extra: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">{STYLE}</head><body><div class="card">'
            f"<h1>{title}</h1><p>{body}</p>{extra}<small>{BUILD[:7]}</small></div></body></html>")


LOADING = page("正在连接… · Connecting…", "如果网站正在休眠，打开需要约一分钟。<br>If the site has been asleep, it "
               "takes about a minute to wake.")
UPDATING = page("正在更新… · Updating…", "已有新版本，正在下载并重新启动。<br>A newer version is available: "
                "downloading it and restarting.")
OFFLINE = page("无法连接 · Can't reach the site", f"请检查网络连接后重试。<br>Check the internet connection and try "
               f"again.<br><small>{BASE}</small>", """<button onclick="document.getElementById('msg').textContent=
'正在连接… Connecting…';pywebview.api.retry().then(ok=>{if(!ok)document.getElementById('msg').textContent=
'仍然无法连接 · Still offline'})">重试 · Try again</button><div id="msg"></div>""")


def fetch(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ChinaHKMarkets-desktop"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def reachable(url: str) -> bool:
    try:
        fetch(url)
        return True
    except urllib.error.HTTPError:
        return True             # the server answered (Streamlit's cookie redirect loops without a cookie jar)
    except OSError:
        return False


def awake() -> bool:
    """The site's own health check answers "ok" only while it runs (not while asleep or waking)."""
    try:
        return fetch(INNER + "_stcore/health", timeout=10).strip() == b"ok"
    except OSError:
        return False


# ------------------------------------------------------------------ settings (the language chosen on the site)

def storage_dir() -> Path:
    """Where the window keeps its cookies and the app its settings."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    d = base / "ChinaHKMarkets"
    d.mkdir(parents=True, exist_ok=True)
    return d


SETTINGS = storage_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    try:
        SETTINGS.write_text(json.dumps(s), encoding="utf-8")
    except OSError:
        pass


def app_url(lang: str) -> str:
    return INNER + "?" + urllib.parse.urlencode({"embed": "true", "app": "desktop", "lang": lang})


def follow_language(window: webview.Window, settings: dict) -> None:
    """Keep the language the site shows (its `lang` address parameter) for the next start."""
    while True:
        time.sleep(5)
        try:
            url = window.get_current_url() or ""
        except Exception:  # noqa: BLE001 - the window is closing
            return
        lang = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("lang", [None])[0]
        if lang in ("zh", "en") and lang != settings.get("lang"):
            settings["lang"] = lang
            save_settings(settings)


# ------------------------------------------------------------------ self-update

def update(window: webview.Window) -> bool:
    """If GitHub has a newer build: download it, check it, swap it in and restart. True when restarting."""
    exe = Path(sys.executable)
    for old in exe.parent.glob(exe.stem + ".old*.exe"):           # left by the last update
        try:
            old.unlink()
        except OSError:
            pass
    if not getattr(sys, "frozen", False) or BUILD == "dev":
        return False
    try:
        info = json.loads(fetch(RELEASE + "version.json", timeout=10))
        if not info.get("build") or info["build"] == BUILD:
            return False
        window.load_html(UPDATING)
        data = fetch(RELEASE + "ChinaHKMarkets.exe", timeout=120)
        if hashlib.sha256(data).hexdigest() != info.get("sha256"):
            return False                                          # incomplete or altered download: keep this one
        new = exe.with_name(exe.stem + ".new.exe")
        new.write_bytes(data)
        old = exe.with_name(f"{exe.stem}.old{int(time.time())}.exe")
        exe.rename(old)                                           # Windows lets a running program be renamed
        new.rename(exe)
        subprocess.Popen([str(exe)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
        window.destroy()
        return True
    except Exception:  # noqa: BLE001 - never let an update stop the app from opening
        return False


# ------------------------------------------------------------------ the window

class Api:
    """Called from the offline page. The window is kept in an underscore attribute: pywebview exposes the api
    object's public attributes to the page by walking them, and walking a Window from its own thread froze the app."""

    def __init__(self, url: str) -> None:
        self._window: webview.Window | None = None
        self._url = url

    def retry(self) -> bool:
        if reachable(BASE) and self._window is not None:
            self._window.load_url(self._url if awake() else BASE)
            return True
        return False


def connect(window: webview.Window, url: str, settings: dict) -> None:
    """Runs in the background once the window is up: update if needed, then open the site (waking it first)."""
    if update(window):
        return
    if not reachable(BASE):
        window.load_html(OFFLINE)
        return
    if not awake():
        window.load_url(BASE)                                     # Streamlit's own wake-up page
        for _ in range(120):                                      # up to 10 minutes
            time.sleep(5)
            if awake():
                break
    window.load_url(url)
    follow_language(window, settings)


def half_screen() -> dict:
    """Half the screen's width and most of its height, centred (in the screen's own units)."""
    try:
        s = webview.screens[0]
        w, h = max(s.width // 2, 640), max(int(s.height * 0.85), 500)
        return {"width": w, "height": h, "x": (s.width - w) // 2, "y": max((s.height - h) // 2, 0)}
    except Exception:  # noqa: BLE001 - any trouble reading the screen: a sensible fixed size
        return {"width": 960, "height": 760}


def main() -> int:
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    settings = load_settings()
    url = app_url(settings.get("lang", "zh"))
    api = Api(url)
    window = webview.create_window(TITLE, html=LOADING, js_api=api, min_size=(640, 500), text_select=True,
                                   **half_screen())
    api._window = window
    webview.start(connect, (window, url, settings), private_mode=False, storage_path=str(storage_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
