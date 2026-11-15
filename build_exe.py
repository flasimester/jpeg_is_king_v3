"""
Build a standalone Windows .exe with FFmpeg bundled inside.

    python build_exe.py                      # finds / downloads ffmpeg.exe automatically
    python build_exe.py --ffmpeg C:\\path\\ffmpeg.exe

Output: dist\\RawToJpeg.exe
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def locate_ffmpeg(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        sys.exit(f"ffmpeg not found at {p}")
    for c in (HERE / "ffmpeg.exe", HERE / "ffmpeg" / "ffmpeg.exe"):
        if c.is_file():
            return c
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)

    print("ffmpeg.exe not found locally - downloading (~80 MB)...")
    dest_dir = HERE / "ffmpeg"
    dest_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "ffmpeg.zip"
        urllib.request.urlretrieve(FFMPEG_ZIP_URL, zpath)
        with zipfile.ZipFile(zpath) as z:
            member = next(n for n in z.namelist() if n.lower().endswith("/bin/ffmpeg.exe"))
            with z.open(member) as src, open(dest_dir / "ffmpeg.exe", "wb") as out:
                shutil.copyfileobj(src, out)
    return dest_dir / "ffmpeg.exe"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", help="path to ffmpeg.exe to bundle")
    ap.add_argument("--console", action="store_true", help="keep a console window (for debugging)")
    a = ap.parse_args()

    if os.name != "nt":
        print("Note: you are not on Windows - PyInstaller builds for the OS it runs on.")

    ffmpeg = locate_ffmpeg(a.ffmpeg)
    print(f"Bundling FFmpeg: {ffmpeg}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", "RawToJpeg",
        "--collect-all", "rawpy",
        "--collect-all", "pillow_heif",
        "--add-binary", f"{ffmpeg}{os.pathsep}.",
        str(HERE / "app.py"),
    ]
    if not a.console:
        cmd.insert(cmd.index("--name"), "--windowed")
    subprocess.check_call(cmd, cwd=HERE)
    print("\nDone ->", HERE / "dist" / "RawToJpeg.exe")


if __name__ == "__main__":
    main()
