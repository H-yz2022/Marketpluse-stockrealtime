"""The desktop app's launcher and build helpers (offline; the window library is replaced by a stand-in)."""

import hashlib
import importlib
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parent.parent / "desktop"


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """desktop/app.py with its folders under tmp_path and a local folder as the release."""
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(settings={}, Window=object))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "home"))
    release = tmp_path / "release"
    release.mkdir()
    monkeypatch.setenv("CHINA_HK_RELEASE", release.as_uri() + "/")
    monkeypatch.delenv("CHINA_HK_CORE", raising=False)
    monkeypatch.syspath_prepend(str(DESKTOP))
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    yield app, release
    sys.modules.pop("app", None)


def _release(release: Path, build: str, payload: bytes = b"program") -> dict:
    z = release / "ChinaHKMarketsCore.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_LZMA) as f:
        f.writestr("ChinaHKMarketsCore/ChinaHKMarketsCore.exe", payload)
        f.writestr("ChinaHKMarketsCore/_internal/data.txt", "x")
    core = {"build": build, "sha256": hashlib.sha256(z.read_bytes()).hexdigest(), "size": z.stat().st_size,
            "file": z.name}
    (release / "version.json").write_text(json.dumps({"build": "launcher1", "sha256": "", "core": core}))
    return core


def test_the_program_is_downloaded_checked_and_installed(launcher):
    app, release = launcher
    assert app.installed_core() is None
    core = _release(release, "aaa111")
    assert app.remote_info()["core"]["build"] == "aaa111"
    seen = []
    assert app.download_core(core, lambda f, text: seen.append(f)) is True
    build, exe = app.installed_core()
    assert build == "aaa111" and exe.read_bytes() == b"program" and seen[-1] > 0.9
    assert not list((app.HOME / "update").glob("*"))             # the downloaded zip isn't kept

    newer = _release(release, "bbb222", b"program v2")           # an update: installed next to the running one
    assert app.download_core(newer) is True
    assert app.installed_core()[0] == "bbb222" and (app.CORE_DIR / "aaa111").exists()
    app.remove_old_cores(keep="bbb222")
    assert sorted(d.name for d in app.CORE_DIR.iterdir() if d.is_dir()) == ["bbb222"]


def test_a_bad_download_keeps_the_installed_program(launcher):
    app, release = launcher
    assert app.download_core(_release(release, "aaa111")) is True
    bad = dict(_release(release, "ccc333", b"altered"), sha256="0" * 64)
    assert app.download_core(bad) is False                        # wrong SHA-256: not installed, not made current
    assert app.installed_core()[0] == "aaa111" and not (app.CORE_DIR / "ccc333").exists()
    (release / "ChinaHKMarketsCore.zip").unlink()                 # release unreachable
    assert app.download_core(dict(bad, build="ddd444")) is False and app.installed_core()[0] == "aaa111"


def test_offline_without_a_release(launcher, monkeypatch):
    app, release = launcher
    assert app.remote_info() is None                              # no version.json: the app says so and offers retry
    monkeypatch.setenv("CHINA_HK_CORE", str(release / "nothing.exe"))
    assert app.installed_core() is None


def test_the_packaged_program_starts_its_own_worker(monkeypatch):
    from stockrt import jobs

    assert jobs.worker_command()[1:] == ["-m", "stockrt.worker", "--serve"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\\app\\ChinaHKMarketsCore.exe")
    assert jobs.worker_command() == [sys.executable, "--worker", "--serve"]


def test_program_id_ignores_line_endings_and_follows_content(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(DESKTOP))
    sys.modules.pop("build", None)
    build = importlib.import_module("build")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 1\ny = 2\n")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "CORE_INPUTS", ["pkg"])
    first = build.core_id()
    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")   # a Windows checkout of the same file
    assert build.core_id() == first
    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 1\ny = 3\n")
    assert build.core_id() != first
    sys.modules.pop("build", None)


def test_bundled_results_are_copied_once_and_never_overwrite(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(DESKTOP))
    sys.modules.pop("core", None)
    core = importlib.import_module("core")
    bundled, mine = tmp_path / "app" / "results", tmp_path / "data" / "results"
    (bundled / "lab").mkdir(parents=True)
    (bundled / "lab" / "meta.json").write_text("bundled")
    (bundled / "cn").mkdir()
    (bundled / "cn" / "2026-10-12.json").write_text("bundled")
    (mine / "cn").mkdir(parents=True)
    (mine / "cn" / "2026-10-12.json").write_text("mine")
    monkeypatch.setattr(core, "APP_DIR", tmp_path / "app")
    monkeypatch.setenv("STOCKRT_RESULTS_DIR", str(mine))
    core.seed_results()
    assert (mine / "lab" / "meta.json").read_text() == "bundled" and (mine / "cn" / "2026-10-12.json").read_text() == "mine"
    sys.modules.pop("core", None)


def test_the_window_reloads_only_when_the_theme_choice_changes(launcher):
    app, _ = launcher
    base = "http://127.0.0.1:8765/?embed=true&app=desktop&lang=zh&fs=l"
    assert "embed_options=dark_theme" in app.theme_reload(base + "&theme=dark")
    assert app.theme_reload(base + "&theme=dark&embed_options=dark_theme") is None          # already applied
    back = app.theme_reload(base + "&theme=system&embed_options=dark_theme")                 # back to following Windows
    assert back is not None and "embed_options" not in back and "lang=zh" in back and "fs=l" in back
    assert app.theme_reload(base + "&theme=system") is None
    assert app.theme_reload("data:text/html,<p>starting</p>") is None                         # the launcher's own pages
    assert app.Server().url({"lang": "en", "fs": "xl", "theme": "light"}).endswith(
        "lang=en&fs=xl&theme=light&embed_options=light_theme")
    assert "lang=" not in app.Server().url({})              # first start: the page follows Windows



def test_a_newer_launcher_is_put_in_place_quietly_for_the_next_start(launcher, tmp_path):
    app, release = launcher
    new = b"new launcher"
    (release / "ChinaHKMarkets.exe").write_bytes(new)
    exe = tmp_path / "ChinaHKMarkets.exe"
    exe.write_bytes(b"old launcher")
    info = {"build": "launcher2", "sha256": hashlib.sha256(new).hexdigest()}
    assert app.update_launcher(info, exe) is True
    assert exe.read_bytes() == new                                  # used from the next start
    assert [p.read_bytes() for p in tmp_path.glob("ChinaHKMarkets.old*.exe")] == [b"old launcher"]
    assert app.update_launcher({"build": app.BUILD, "sha256": ""}, exe) is False      # same version: nothing


def test_an_altered_launcher_download_is_ignored(launcher, tmp_path):
    app, release = launcher
    (release / "ChinaHKMarkets.exe").write_bytes(b"tampered")
    exe = tmp_path / "ChinaHKMarkets.exe"
    exe.write_bytes(b"old launcher")
    assert app.update_launcher({"build": "launcher2", "sha256": "0" * 64}, exe) is False
    assert exe.read_bytes() == b"old launcher" and not list(tmp_path.glob("*.old*.exe"))


def test_the_evening_start_explains_itself_and_offers_a_choice(launcher):
    app, _ = launcher
    for choice in ("pick('open')", "pick('background')", "pick('skip')", "Task Scheduler"):
        assert choice in app.CHOICE
    assert f"var left={app.SCHEDULED_WAIT}" in app.CHOICE and "reload" not in app.CHOICE.lower()
