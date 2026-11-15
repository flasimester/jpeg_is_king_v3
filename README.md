# RAW / HEIC / Video → JPEG, WebP, PNG, TIFF Converter

| Input | Result |
|---|---|
| `.ARW` `.DNG` (also `.CR2 .CR3 .NEF .RAF .ORF .RW2`) | RAW → image |
| `.HEIC` `.HEIF` | iPhone photos → image |
| `.TIFF` `.PNG` `.JPG` `.WEBP` `.BMP` | convert / compress |
| `.MOV` `.MP4` `.M4V` `.AVI` `.MKV` | one frame (default: first) → image |

## Output formats and options

| Format | Options |
|---|---|
| **JPEG** | quality 1–100, progressive, chroma subsampling (4:4:4 / 4:2:2 / 4:2:0), optimize |
| **WebP** | quality 1–100, lossless mode |
| **PNG** | compression level 0–9 (lossless, keeps transparency; 9 = smallest, slowest) |
| **TIFF** | compression: lzw / deflate / jpeg (uses quality) / none |

All formats also support the resize slider (10–100 %), overwrite / skip existing,
batch + folder import (optional subfolders). EXIF is kept for non-RAW files when
writing JPEG, WebP or PNG. JPEG flattens transparency onto white; WebP, PNG and
TIFF keep it.

## Using the window
- **Double-click a row** (or select it and press Enter) to open the source image; if it has been converted, the converted image opens too.
- **Open output folder** opens the chosen output folder, or the folder of the selected / latest converted file.

## Run from source
```
pip install -r requirements.txt
python app.py                                          # GUI
python converter.py shots/ -q 80 -s 50 -r -o out       # JPEG, command line
python converter.py shots/ -f webp -q 80               # WebP
python converter.py shots/ --progressive --subsampling 4:4:4
python converter.py shots/ -f png --png-level 9
python converter.py shots/ -f tiff --tiff-compression deflate
```
Video needs `ffmpeg` on PATH (or `ffmpeg.exe` next to the scripts).

## Build the Windows .exe
Double-click `build_exe.bat` (or `python build_exe.py`).
It downloads FFmpeg if needed and produces `dist\RawToJpeg.exe`.

## Notes
- Without an output folder, files go to a `converted` folder next to each source.
- If two inputs share a name (e.g. `IMG_1.ARW` + `IMG_1.DNG`), the second gets `_1`.
- Video output is named `<name>_frame.<ext>`; set "Video frame at (s)" to grab a later frame.
