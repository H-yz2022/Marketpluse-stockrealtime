"""China & HK Markets - the Windows desktop app.

The website in its own window: an app icon, a title, the taskbar and Start menu like any program, and no browser
tabs or address bar. It shows https://china-hk-markets.streamlit.app/ through Windows' built-in Edge engine
(WebView2), so it always has the latest version of the site and needs nothing else installed. Download buttons
(CSV) save to the Downloads folder, outside links open in the normal browser, and without a connection it says so
and offers to try again.

The window opens at half the screen's width, centred, and shows a "connecting" page at once; the connection is
checked in the background, then the site loads.

    python desktop/app.py                     # run from source
    bash desktop/build.sh                     # build dist/ChinaHKMarkets.exe (GitHub builds it on each push too)

Set CHINA_HK_URL to point it at another address (for example a local copy: http://localhost:8501).
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import webview

URL = os.environ.get("CHINA_HK_URL", "https://china-hk-markets.streamlit.app/")
TITLE = "China & HK Markets · 尾盘选股"

STYLE = """<style>
body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#fcfcfb;color:#0b0b0b;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.card{max-width:520px;text-align:center;padding:32px}
h1{font-size:22px;margin:0 0 12px} p{color:#555;line-height:1.6;margin:0 0 20px}
button{font-size:15px;padding:10px 22px;border:0;border-radius:6px;background:#2a78d6;color:#fff;cursor:pointer}
#msg{margin-top:14px;color:#888;font-size:13px}
@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#fff}p{color:#bbb}}
</style>"""

LOADING = f"""<!doctype html><html><head><meta charset="utf-8">{STYLE}</head><body><div class="card">
<h1>正在连接… · Connecting…</h1>
<p>如果网站正在休眠，打开需要约一分钟。<br>If the site has been asleep, it takes about a minute to wake.</p>
</div></body></html>"""

OFFLINE = f"""<!doctype html><html><head><meta charset="utf-8">{STYLE}</head><body><div class="card">
<h1>无法连接 · Can't reach the site</h1>
<p>请检查网络连接后重试。<br>Check the internet connection and try again.<br>
<small>__URL__</small></p>
<button onclick="document.getElementById('msg').textContent='正在连接… Connecting…';
pywebview.api.retry().then(ok=>{{if(!ok)document.getElementById('msg').textContent='仍然无法连接 · Still offline'}})">
重试 · Try again</button><div id="msg"></div></div></body></html>"""


def reachable(url: str, timeout: float = 15) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChinaHKMarkets-desktop"})
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True             # the server answered (Streamlit's cookie redirect loops without a cookie jar)
    except OSError:
        return False


class Api:
    """Called from the offline page. The window is kept in an underscore attribute: pywebview exposes the api
    object's public attributes to the page by walking them, and walking a Window from its own thread froze the app."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None

    def retry(self) -> bool:
        if reachable(URL) and self._window is not None:
            self._window.load_url(URL)
            return True
        return False


def connect(window: webview.Window) -> None:
    """Runs in the background once the window is up: load the site, or say it can't be reached."""
    if reachable(URL):
        window.load_url(URL)
    else:
        window.load_html(OFFLINE.replace("__URL__", URL))


def half_screen() -> dict:
    """Half the screen's width and most of its height, centred (in the screen's own units)."""
    try:
        s = webview.screens[0]
        w, h = max(s.width // 2, 640), max(int(s.height * 0.85), 500)
        return {"width": w, "height": h, "x": (s.width - w) // 2, "y": max((s.height - h) // 2, 0)}
    except Exception:  # noqa: BLE001 - any trouble reading the screen: a sensible fixed size
        return {"width": 960, "height": 760}


def storage_dir() -> Path:
    """Where the window keeps its cookies (so the site remembers settings between runs)."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    d = base / "ChinaHKMarkets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    api = Api()
    window = webview.create_window(TITLE, html=LOADING, js_api=api, min_size=(640, 500), text_select=True,
                                   **half_screen())
    api._window = window
    webview.start(connect, window, private_mode=False, storage_path=str(storage_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
