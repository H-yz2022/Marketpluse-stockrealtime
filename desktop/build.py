"""Builds the desktop app's two parts (Windows) and checks them.

    python desktop/build.py core        dist/ChinaHKMarketsCore/      the program (needs desktop/requirements-core.txt)
    python desktop/build.py smoke       starts the built program: server answers, a page loads, a worker job runs
    python desktop/build.py zip         dist/ChinaHKMarketsCore.zip
    python desktop/build.py launcher    dist/ChinaHKMarkets.exe       the window (needs desktop/requirements.txt)
    python desktop/build.py version     dist/version.json             what installed copies compare themselves with

The launcher is what people download. It fetches the program (the zip) once, keeps both up to date from
version.json, and runs the program on the user's PC - no website involved. GitHub runs all of these on each push
that changes the program (.github/workflows/desktop.yml).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
CORE_INPUTS = ["stockrt", "ui", "app_pages", "streamlit_app.py", ".streamlit", "results", "research/lab_pools",
               "sample_data/universe.parquet", "desktop/core.py", "desktop/requirements-core.txt", "desktop/build.py"]
# Bundled but never loaded by this app: tests, C headers, optional chart libraries.
PRUNE = ["pyarrow/include", "pyarrow/tests", "pandas/tests", "numpy/tests", "numpy/_core/tests", "numpy/f2py/tests",
         "altair/vegalite/v6/schema/__pycache__", "streamlit/hello"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def core_id() -> str:
    """Changes only when something that goes into the program changes, so installed copies don't download 100+ MB
    again for a README edit: a hash of the program's input files (line endings ignored, so every checkout agrees)."""
    h = hashlib.sha256()
    for rel in CORE_INPUTS:
        path = ROOT / rel
        files = [path] if path.is_file() else sorted(f for f in path.rglob("*") if f.is_file())
        for f in files:
            if "__pycache__" in f.parts:
                continue
            h.update(f.relative_to(ROOT).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def launcher_id() -> str:
    """The commit the launcher was built from (APP_BUILD overrides it, e.g. for a local test build)."""
    return os.environ.get("APP_BUILD") or git("rev-parse", "HEAD")[:12]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pyinstaller(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(DIST),
                    "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"), *args], cwd=ROOT, check=True)


def core() -> None:
    sep = os.pathsep
    data = [("streamlit_app.py", "."), ("app_pages", "app_pages"), (".streamlit", ".streamlit"),
            ("results", "results"), ("research/lab_pools", "research/lab_pools"),
            ("sample_data/universe.parquet", "sample_data"), ("stockrt/trading_calendar.json", "stockrt")]
    args = ["--onedir", "--windowed", "--name", "ChinaHKMarketsCore", "--icon", str(ROOT / "desktop" / "icon.ico"),
            "--paths", str(ROOT),
            "--collect-all", "streamlit", "--recursive-copy-metadata", "streamlit",
            "--collect-all", "altair", "--collect-data", "tzdata", "--collect-submodules", "uvicorn",
            "--collect-submodules", "stockrt", "--collect-submodules", "ui",
            "--hidden-import", "sqlite3", "--hidden-import", "zoneinfo"]
    for mod in ("tkinter", "matplotlib", "IPython", "pytest", "PyInstaller", "notebook", "scipy"):
        args += ["--exclude-module", mod]
    for src, dest in data:
        args += ["--add-data", f"{ROOT / src}{sep}{dest}"]
    pyinstaller(*args, str(ROOT / "desktop" / "core.py"))
    internal = DIST / "ChinaHKMarketsCore" / "_internal"
    import shutil

    for rel in PRUNE:
        shutil.rmtree(internal / rel, ignore_errors=True)
    (DIST / "ChinaHKMarketsCore" / "core.json").write_text(json.dumps({"build": core_id()}), encoding="utf-8")
    size = sum(f.stat().st_size for f in (DIST / "ChinaHKMarketsCore").rglob("*") if f.is_file())
    print(f"core {core_id()}: {size / 1e6:.0f} MB in {DIST / 'ChinaHKMarketsCore'}")


def smoke() -> None:
    """The built program really runs: every module imports, the server answers and serves the page, and a
    background job runs in worker mode."""
    exe = DIST / "ChinaHKMarketsCore" / "ChinaHKMarketsCore.exe"
    work = ROOT / "build" / "smoke"
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, STOCKRT_DATA_DIR=str(work / "data"), STOCKRT_RESULTS_DIR=str(work / "data" / "results"),
               STOCKRT_DESKTOP="1", STOCKRT_DATA_MODE="live")
    rc = subprocess.run([str(exe), "--selftest"], env=env, timeout=300).returncode
    log = work / "data" / "logs" / "selftest.log"
    print("selftest exit", rc, "|", log.read_text(encoding="utf-8")[-600:].strip() if log.exists() else "no log")
    assert rc == 0, "selftest failed"

    port = 8650
    srv = subprocess.Popen([str(exe), "--server", str(port)], env=env)
    try:
        t0, ok = time.time(), False
        while time.time() - t0 < 240 and srv.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=3) as r:
                    ok = r.read().strip() == b"ok"
                if ok:
                    break
            except OSError:
                time.sleep(1)
        slog = work / "data" / "logs" / "server.log"
        assert ok, "server did not answer: " + (slog.read_text(encoding="utf-8")[-1500:] if slog.exists() else "")
        print(f"server answered after {time.time() - t0:.0f} s")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            html = r.read()
        assert b"<div id=\"root\">" in html or b"streamlit" in html.lower(), "front page not served"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/host-config", timeout=10) as r:
            assert r.status == 200
        progress = work / "job.json"
        progress.unlink(missing_ok=True)
        rc = subprocess.run([str(exe), "--worker", "selftest", "{}", str(progress)], env=env, timeout=120).returncode
        state = json.loads(progress.read_text(encoding="utf-8"))
        assert rc == 0 and state["message"].startswith("Done"), f"worker failed: {rc} {state}"
        assert (work / "data" / "results" / "lab" / "meta.json").exists(), "bundled results were not copied"
        print("worker job ok; bundled results copied")
    finally:
        srv.kill()
    print("smoke test passed")


def make_zip() -> None:
    src, out = DIST / "ChinaHKMarketsCore", DIST / "ChinaHKMarketsCore.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_LZMA) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, Path("ChinaHKMarketsCore") / f.relative_to(src))
    print(f"zip: {out.stat().st_size / 1e6:.0f} MB")


def launcher() -> None:
    (ROOT / "desktop" / "build_info.py").write_text(f'BUILD = "{launcher_id()}"\n', encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "desktop" / "make_icon.py")], check=True)
    pyinstaller("--onefile", "--windowed", "--name", "ChinaHKMarkets", "--icon", str(ROOT / "desktop" / "icon.ico"),
                "--paths", str(ROOT / "desktop"), "--hidden-import", "build_info", str(ROOT / "desktop" / "app.py"))
    print(f"launcher {launcher_id()}: {(DIST / 'ChinaHKMarkets.exe').stat().st_size / 1e6:.1f} MB")


def version() -> None:
    exe, z = DIST / "ChinaHKMarkets.exe", DIST / "ChinaHKMarketsCore.zip"
    info = {"build": launcher_id(), "sha256": sha256(exe), "size": exe.stat().st_size,
            "core": {"build": core_id(), "sha256": sha256(z), "size": z.stat().st_size,
                     "file": "ChinaHKMarketsCore.zip"},
            "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    (DIST / "version.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(json.dumps(info, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["core", "smoke", "zip", "launcher", "version", "core-id"])
    step = ap.parse_args().step
    {"core": core, "smoke": smoke, "zip": make_zip, "launcher": launcher, "version": version,
     "core-id": lambda: print(core_id())}[step]()
