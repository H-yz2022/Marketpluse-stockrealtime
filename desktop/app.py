"""China & HK Markets - the Windows desktop app.

The website in its own window: an app icon, a title, the taskbar and Start menu like any program, and no browser
tabs or address bar. It shows https://china-hk-markets.streamlit.app/ through Windows' built-in Edge engine
(WebView2), so it always has the latest version of the site and needs nothing else installed. Download buttons
(CSV) save to the Downloads folder, outside links open in the normal browser, and without a connection it says so
and offers to try again.

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

OFFLINE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#fcfcfb;color:#0b0b0b;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.card{max-width:520px;text-align:center;padding:32px}
h1{font-size:22px;margin:0 0 12px} p{color:#555;line-height:1.6;margin:0 0 20px}
button{font-size:15px;padding:10px 22px;border:0;border-radius:6px;background:#2a78d6;color:#fff;cursor:pointer}
#msg{margin-top:14px;color:#888;font-size:13px}
@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#fff}p{color:#bbb}}
</style></head><body><div class="card">
<h1>无法连接 · Can't reach the site</h1>
<p>请检查网络连接后重试。<br>Check the internet connection and try again.<br>
<small>__URL__</small></p>
<button onclick="document.getElementById('msg').textContent='正在连接… Connecting…';
pywebview.api.retry().then(ok=>{if(!ok)document.getElementById('msg').textContent='仍然无法连接 · Still offline'})">
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
    """Called from the offline page."""

    window: webview.Window | None = None

    def retry(self) -> bool:
        if reachable(URL) and self.window is not None:
            self.window.load_url(URL)
            return True
        return False


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
    online = reachable(URL)
    api.window = webview.create_window(
        TITLE, url=URL if online else None, html=None if online else OFFLINE.replace("__URL__", URL),
        js_api=api, width=1400, height=900, min_size=(900, 600), text_select=True)
    webview.start(private_mode=False, storage_path=str(storage_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
