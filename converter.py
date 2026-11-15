"""
Core conversion engine: RAW / HEIC / TIFF / PNG / video -> JPEG, WebP, PNG or TIFF.

Can be used as a library (see app.py) or as a CLI:

    python converter.py photo.ARW folder_with_photos -q 85 -s 50 -o out_dir -r
    python converter.py shots/ --format webp -q 80
    python converter.py shots/ --format jpeg --progressive --subsampling 4:4:4
    python converter.py shots/ --format png --png-level 9
    python converter.py shots/ --format tiff --tiff-compression deflate
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # HEIC/HEIF unavailable
    pillow_heif = None

try:
    import rawpy
except ImportError:  # RAW unavailable
    rawpy = None

# --------------------------------------------------------------------------
# Supported formats
# --------------------------------------------------------------------------
RAW_EXTS = {".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2"}
HEIF_EXTS = {".heic", ".heif"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
ALL_EXTS = RAW_EXTS | HEIF_EXTS | IMAGE_EXTS | VIDEO_EXTS

# Output formats: key -> (PIL format name, file extension)
OUTPUT_FORMATS = {
    "jpeg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
    "png": ("PNG", ".png"),
    "tiff": ("TIFF", ".tif"),
}
SUBSAMPLING = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}
TIFF_COMPRESSIONS = {
    "lzw": "tiff_lzw",
    "deflate": "tiff_adobe_deflate",
    "jpeg": "jpeg",
    "none": "raw",
}

DEFAULT_OUT_SUBDIR = "converted"
_ALL_OUT_SUBDIRS = {"converted", "jpeg_output"}  # skipped when scanning folders


# --------------------------------------------------------------------------
# FFmpeg lookup (works from source and from a PyInstaller bundle)
# --------------------------------------------------------------------------
def find_ffmpeg() -> str | None:
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / exe)
        candidates.append(Path(sys.executable).parent / exe)
    here = Path(__file__).resolve().parent
    candidates += [here / exe, here / "ffmpeg" / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("ffmpeg")


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def _load_raw(path: Path) -> Image.Image:
    if rawpy is None:
        raise RuntimeError("rawpy is not installed (pip install rawpy)")
    with rawpy.imread(str(path)) as raw:
        try:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
            return Image.fromarray(rgb)
        except Exception:
            # Fall back to the embedded preview if demosaicing fails
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(thumb.data))
                img.load()
                return ImageOps.exif_transpose(img)
            return Image.fromarray(thumb.data)


def _load_video_frame(path: Path, seconds: float) -> Image.Image:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found (install it or put ffmpeg.exe next to the app)")
    cmd = [ffmpeg, "-v", "error"]
    if seconds > 0:
        cmd += ["-ss", str(seconds)]
    cmd += ["-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, capture_output=True, creationflags=flags)
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace").strip() or "no frame returned"
        raise RuntimeError(f"FFmpeg: {err[:200]}")
    img = Image.open(io.BytesIO(proc.stdout))
    img.load()
    return img


def load_image(path: Path, video_seconds: float = 0.0) -> Image.Image:
    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        try:
            return _load_raw(path)
        except RuntimeError:
            raise
        except Exception:
            if ext == ".dng":  # some DNGs are plain TIFF-like files
                img = Image.open(path)
                img.load()
                return img
            raise
    if ext in VIDEO_EXTS:
        return _load_video_frame(path, video_seconds)
    if ext in HEIF_EXTS and pillow_heif is None:
        raise RuntimeError("pillow-heif is not installed (pip install pillow-heif)")
    img = Image.open(path)
    img.load()
    return ImageOps.exif_transpose(img)


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def _prepare(img: Image.Image, keep_alpha: bool) -> Image.Image:
    """Normalise the mode. JPEG flattens transparency onto white; others may keep it."""
    if img.mode in ("I;16", "I;16L", "I;16B", "I"):
        img = img.point(lambda i: i * (1 / 256)).convert("L")
    if _has_alpha(img):
        rgba = img.convert("RGBA")
        if keep_alpha:
            return rgba
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


@dataclass
class Result:
    src: Path
    status: str  # "ok" | "skipped" | "error"
    dst: Path | None = None
    message: str = ""
    in_size: int = 0
    out_size: int = 0


def plan_output(src: Path, out_dir: Path | None, used: set[str], fmt: str = "jpeg") -> Path:
    """Work out the destination path, avoiding clashes inside one batch."""
    out_ext = OUTPUT_FORMATS[fmt][1]
    folder = Path(out_dir) if out_dir else src.parent / DEFAULT_OUT_SUBDIR
    suffix = "_frame" if src.suffix.lower() in VIDEO_EXTS else ""
    dst = folder / f"{src.stem}{suffix}{out_ext}"
    try:
        if dst.resolve() == src.resolve():
            dst = folder / f"{src.stem}_converted{out_ext}"
    except OSError:
        pass
    base, n = dst, 1
    while str(dst).lower() in used:
        dst = base.with_name(f"{base.stem}_{n}{out_ext}")
        n += 1
    used.add(str(dst).lower())
    return dst


def build_save_kwargs(
    fmt: str,
    quality: int,
    progressive: bool,
    subsampling: str,
    optimize: bool,
    lossless: bool,
    png_level: int,
    tiff_compression: str,
) -> dict:
    """Translate the user-facing options into Pillow save() arguments."""
    pil_fmt = OUTPUT_FORMATS[fmt][0]
    kw: dict = {"format": pil_fmt}
    if fmt == "jpeg":
        kw.update(quality=int(quality), optimize=bool(optimize), progressive=bool(progressive))
        if subsampling in SUBSAMPLING:
            kw["subsampling"] = SUBSAMPLING[subsampling]
    elif fmt == "webp":
        kw.update(quality=int(quality), lossless=bool(lossless), method=6)
    elif fmt == "png":
        # Note: Pillow's optimize=True forces level 9 and would override png_level,
        # so PNG is controlled by compress_level alone.
        kw.update(compress_level=max(0, min(9, int(png_level))))
    elif fmt == "tiff":
        kw["compression"] = TIFF_COMPRESSIONS.get(tiff_compression, "tiff_lzw")
        if kw["compression"] == "jpeg":
            kw["quality"] = int(quality)
    return kw


def convert_file(
    src: Path,
    out_dir: Path | None = None,
    quality: int = 85,
    resize_pct: int = 100,
    overwrite: bool = False,
    keep_exif: bool = True,
    video_seconds: float = 0.0,
    used: set[str] | None = None,
    fmt: str = "jpeg",
    progressive: bool = False,
    subsampling: str = "4:2:0",
    optimize: bool = True,
    lossless: bool = False,
    png_level: int = 6,
    tiff_compression: str = "lzw",
) -> Result:
    src = Path(src)
    used = used if used is not None else set()
    try:
        if fmt not in OUTPUT_FORMATS:
            raise ValueError(f"unknown output format '{fmt}'")
        in_size = src.stat().st_size
        dst = plan_output(src, out_dir, used, fmt)
        if dst.exists() and not overwrite:
            return Result(src, "skipped", dst, "already exists", in_size, dst.stat().st_size)

        img = load_image(src, video_seconds)
        exif_bytes = b""
        if keep_exif and fmt in ("jpeg", "webp", "png") and src.suffix.lower() not in RAW_EXTS | VIDEO_EXTS:
            try:
                exif = img.getexif()
                if exif:
                    exif[0x0112] = 1  # orientation already applied
                    exif_bytes = exif.tobytes()
            except Exception:
                exif_bytes = b""

        img = _prepare(img, keep_alpha=(fmt != "jpeg"))
        if resize_pct < 100:
            w, h = img.size
            img = img.resize(
                (max(1, round(w * resize_pct / 100)), max(1, round(h * resize_pct / 100))),
                Image.LANCZOS,
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        kwargs = build_save_kwargs(fmt, quality, progressive, subsampling, optimize,
                                   lossless, png_level, tiff_compression)
        if exif_bytes:
            kwargs["exif"] = exif_bytes
        img.save(dst, **kwargs)
        return Result(src, "ok", dst, f"{img.size[0]}x{img.size[1]}", in_size, dst.stat().st_size)
    except Exception as e:  # noqa: BLE001
        return Result(src, "error", None, str(e))


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------
def collect_files(paths, recursive: bool = True) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path):
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            found.append(p)

    for raw_p in paths:
        p = Path(raw_p)
        if p.is_file():
            if p.suffix.lower() in ALL_EXTS:
                add(p)
        elif p.is_dir():
            if recursive:
                for root, dirs, files in os.walk(p):
                    dirs[:] = sorted(d for d in dirs if d not in _ALL_OUT_SUBDIRS)
                    for f in sorted(files):
                        fp = Path(root) / f
                        if fp.suffix.lower() in ALL_EXTS:
                            add(fp)
            else:
                for fp in sorted(p.iterdir()):
                    if fp.is_file() and fp.suffix.lower() in ALL_EXTS:
                        add(fp)
    return found


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Convert RAW/HEIC/TIFF/PNG/video to JPEG, WebP, PNG or TIFF")
    ap.add_argument("inputs", nargs="+", help="files and/or folders")
    ap.add_argument("-o", "--out", help=f"output folder (default: a '{DEFAULT_OUT_SUBDIR}' folder next to each file)")
    ap.add_argument("-f", "--format", choices=list(OUTPUT_FORMATS), default="jpeg", help="output format (default jpeg)")
    ap.add_argument("-q", "--quality", type=int, default=85, help="quality 1-100 for JPEG/WebP/TIFF-jpeg (default 85)")
    ap.add_argument("-s", "--resize", type=int, default=100, help="resize percent 10-100 (default 100)")
    ap.add_argument("-r", "--recursive", action="store_true", help="scan folders recursively")
    ap.add_argument("--overwrite", action="store_true", help="overwrite existing output files")
    ap.add_argument("--no-exif", action="store_true", help="do not copy EXIF data")
    ap.add_argument("--video-time", type=float, default=0.0, help="video frame position in seconds (default 0)")
    ap.add_argument("--progressive", action="store_true", help="JPEG: progressive encoding")
    ap.add_argument("--subsampling", choices=list(SUBSAMPLING), default="4:2:0",
                    help="JPEG: chroma subsampling (default 4:2:0; 4:4:4 = best colour, biggest file)")
    ap.add_argument("--no-optimize", action="store_true", help="JPEG: disable the extra optimisation pass")
    ap.add_argument("--lossless", action="store_true", help="WebP: lossless mode (quality then means effort)")
    ap.add_argument("--png-level", type=int, default=6, help="PNG: zlib compression level 0-9 (default 6)")
    ap.add_argument("--tiff-compression", choices=list(TIFF_COMPRESSIONS), default="lzw",
                    help="TIFF: compression method (default lzw)")
    a = ap.parse_args(argv)

    quality = max(1, min(100, a.quality))
    resize = max(10, min(100, a.resize))
    files = collect_files(a.inputs, a.recursive)
    if not files:
        print("No supported files found.")
        return 1

    used: set[str] = set()
    errors = 0
    for i, f in enumerate(files, 1):
        r = convert_file(
            f, Path(a.out) if a.out else None, quality, resize,
            a.overwrite, not a.no_exif, a.video_time, used,
            fmt=a.format, progressive=a.progressive, subsampling=a.subsampling,
            optimize=not a.no_optimize, lossless=a.lossless,
            png_level=a.png_level, tiff_compression=a.tiff_compression,
        )
        tag = {"ok": "OK  ", "skipped": "SKIP", "error": "FAIL"}[r.status]
        extra = f"{human(r.in_size)} -> {human(r.out_size)}" if r.status == "ok" else r.message
        print(f"[{i}/{len(files)}] {tag} {f.name}  {extra}")
        errors += r.status == "error"
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
