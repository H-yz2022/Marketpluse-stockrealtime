"""Published results: safe download, switching the results folder, rolling window, waiting after the close."""

import io
import json
import tarfile
from datetime import date, datetime

import pytest

from stockrt import archive, published
from stockrt.calendar import BJ


def tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_safe_extract_skips_paths_outside(tmp_path):
    data = tarball({"repo-results/manifest.json": b"{}", "repo-results/cn/2026-10-09.json": b"{}",
                    "../evil.txt": b"x", "/abs.txt": b"x"})
    root = published._safe_extract(data, tmp_path / "x")
    assert root == tmp_path / "x" / "repo-results"
    assert (root / "cn" / "2026-10-09.json").exists()
    assert not (tmp_path / "evil.txt").exists() and not list(tmp_path.rglob("abs.txt"))


class Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_sync_switches_to_the_published_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path / "local")
    monkeypatch.setattr(published, "SYNC_DIR", tmp_path / "published")
    monkeypatch.setattr(published, "repo", lambda: "me/repo")
    monkeypatch.setattr(published, "enabled", lambda: True)
    monkeypatch.setattr(published, "_state", {"checked": 0.0, "stamp": None, "dir": None, "error": None})
    tar = tarball({"repo-results/manifest.json": json.dumps({"updated": "2026-10-12T15:10:00+0800"}).encode(),
                   "repo-results/cn/2026-10-12.json": b"{}"})
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url.endswith("manifest.json"):
            return Resp(payload={"updated": "2026-10-12T15:10:00+0800"})
        return Resp(content=tar)

    monkeypatch.setattr(published.requests, "get", fake_get)
    st = published.sync()
    assert st["source"] == "published" and archive.RESULTS_DIR.name == "repo-results"
    assert (archive.RESULTS_DIR / "cn" / "2026-10-12.json").exists()
    assert published.sync() == st and len(calls) == 2          # within a minute: no new request
    published.sync(force=True)                                   # same stamp: manifest only, no download
    assert len(calls) == 3


def test_nothing_published_keeps_local(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setattr(archive, "RESULTS_DIR", local)
    monkeypatch.setattr(published, "repo", lambda: "me/repo")
    monkeypatch.setattr(published, "enabled", lambda: True)
    monkeypatch.setattr(published, "_state", {"checked": 0.0, "stamp": None, "dir": None, "error": None})
    monkeypatch.setattr(published.requests, "get", lambda url, **kw: Resp(status=404))
    assert published.sync()["source"] == "local" and archive.RESULTS_DIR == local


def test_rolling_window_keeps_four_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    days = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8), date(2026, 10, 9),
            date(2026, 10, 12)]
    for d in days:
        base = tmp_path / "cn" / d.isoformat()
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".json").write_text(json.dumps({"checkpoints": ["14:00"]}), encoding="utf-8")
        base.with_suffix(".md").write_text("x", encoding="utf-8")
    removed = archive.prune_sessions("CN")
    assert archive.sessions("CN") == sorted(days, reverse=True)[:4]
    assert len(removed) == 4                                     # .json + .md of the two oldest
    man = json.loads(archive.write_manifest(datetime(2026, 10, 12, 15, 10, tzinfo=BJ)).read_text(encoding="utf-8"))
    assert man["sessions"]["CN"][0] == "2026-10-12" and man["updated"].startswith("2026-10-12T15:10")


@pytest.mark.parametrize("hhmm,expected", [((14, 59), False), ((15, 20), True), ((16, 30), False)])
def test_site_waits_for_the_published_close(monkeypatch, hhmm, expected):
    monkeypatch.setattr(published, "enabled", lambda: True)
    monkeypatch.setattr("stockrt.calendar.now_bj", lambda: datetime(2026, 10, 12, *hhmm, tzinfo=BJ))
    assert published.await_close("CN", date(2026, 10, 12)) is expected
    assert published.await_close("CN", date(2026, 10, 9)) is False   # an earlier session is never "due"
