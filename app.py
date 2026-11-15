"""Tkinter GUI for the image/video converter. Run: python app.py"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import converter as cv

FORMAT_LABELS = {"JPEG": "jpeg", "WebP": "webp", "PNG": "png", "TIFF": "tiff"}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RAW / HEIC / Video → JPEG, WebP, PNG, TIFF Converter")
        self.geometry("900x780")
        self.minsize(800, 680)

        self.items: dict[str, Path] = {}
        self.results: dict[str, Path] = {}  # row id -> converted file
        self._counter = 0
        self.q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker: threading.Thread | None = None

        self.fmt_label = tk.StringVar(value="JPEG")
        self.quality = tk.IntVar(value=85)
        self.resize = tk.IntVar(value=100)
        self.overwrite = tk.BooleanVar(value=False)
        self.keep_exif = tk.BooleanVar(value=True)
        self.recursive = tk.BooleanVar(value=True)
        self.out_dir = tk.StringVar(value="")
        self.video_time = tk.DoubleVar(value=0.0)

        # per-format options
        self.progressive = tk.BooleanVar(value=False)
        self.subsampling = tk.StringVar(value="4:2:0")
        self.optimize = tk.BooleanVar(value=True)
        self.lossless = tk.BooleanVar(value=False)
        self.png_level = tk.IntVar(value=6)
        self.tiff_comp = tk.StringVar(value="lzw")

        self._build()
        self._update_state()

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = dict(padx=8, pady=4)

        # --- file list
        top = ttk.LabelFrame(self, text="Files")
        top.pack(fill="both", expand=True, **pad)

        btns = ttk.Frame(top)
        btns.pack(fill="x", padx=6, pady=6)
        ttk.Button(btns, text="Add files…", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="Add folder…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Checkbutton(btns, text="Include subfolders", variable=self.recursive).pack(side="left", padx=8)
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="right")
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).pack(side="right", padx=4)

        cols = ("name", "type", "size", "status")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="extended")
        for c, w, anchor in (("name", 380, "w"), ("type", 60, "center"), ("size", 80, "e"), ("status", 240, "w")):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor=anchor)
        self.tree.bind("<Double-1>", self.on_row_open)
        self.tree.bind("<Return>", self.on_row_open)
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        sb.pack(side="right", fill="y", pady=(0, 6), padx=(0, 6))

        # --- settings
        st = ttk.LabelFrame(self, text="Settings")
        st.pack(fill="x", **pad)
        st.columnconfigure(1, weight=1)

        ttk.Label(st, text="Output format").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        fmt_box = ttk.Combobox(st, textvariable=self.fmt_label, values=list(FORMAT_LABELS),
                               state="readonly", width=10)
        fmt_box.grid(row=0, column=1, sticky="w", padx=4)
        fmt_box.bind("<<ComboboxSelected>>", lambda _e: self._update_state())

        ttk.Label(st, text="Quality").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.q_scale = tk.Scale(st, from_=1, to=100, orient="horizontal", variable=self.quality, showvalue=False)
        self.q_scale.grid(row=1, column=1, sticky="ew", padx=4)
        self.q_lbl = ttk.Label(st, width=6)
        self.q_lbl.grid(row=1, column=2, padx=8)

        ttk.Label(st, text="Resize").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        tk.Scale(st, from_=10, to=100, orient="horizontal", variable=self.resize,
                 showvalue=False).grid(row=2, column=1, sticky="ew", padx=4)
        self.r_lbl = ttk.Label(st, width=6)
        self.r_lbl.grid(row=2, column=2, padx=8)

        self.quality.trace_add("write", lambda *_: self._labels())
        self.resize.trace_add("write", lambda *_: self._labels())
        self._labels()

        # --- format-specific options
        fo = ttk.LabelFrame(st, text="Format options")
        fo.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        self.w_prog = ttk.Checkbutton(fo, text="Progressive", variable=self.progressive)
        self.w_prog.grid(row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Label(fo, text="Chroma subsampling").grid(row=0, column=1, sticky="e", padx=(16, 4))
        self.w_sub = ttk.Combobox(fo, textvariable=self.subsampling, values=list(cv.SUBSAMPLING),
                                  state="readonly", width=7)
        self.w_sub.grid(row=0, column=2, sticky="w")
        self.w_opt = ttk.Checkbutton(fo, text="Optimize (JPEG)", variable=self.optimize)
        self.w_opt.grid(row=0, column=3, sticky="w", padx=16)

        self.w_lossless = ttk.Checkbutton(fo, text="WebP lossless", variable=self.lossless,
                                          command=self._update_state)
        self.w_lossless.grid(row=1, column=0, sticky="w", padx=8, pady=3)
        ttk.Label(fo, text="PNG compression (0-9)").grid(row=1, column=1, sticky="e", padx=(16, 4))
        self.w_png = ttk.Spinbox(fo, from_=0, to=9, width=5, textvariable=self.png_level)
        self.w_png.grid(row=1, column=2, sticky="w")
        ttk.Label(fo, text="TIFF compression").grid(row=1, column=3, sticky="e", padx=(16, 4))
        self.w_tiff = ttk.Combobox(fo, textvariable=self.tiff_comp, values=list(cv.TIFF_COMPRESSIONS),
                                   state="readonly", width=9)
        self.w_tiff.grid(row=1, column=4, sticky="w", padx=(0, 8))

        self.hint = ttk.Label(fo, text="", foreground="#666")
        self.hint.grid(row=2, column=0, columnspan=5, sticky="w", padx=8, pady=(2, 4))

        # --- output / misc
        ttk.Label(st, text="Output folder").grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(st, textvariable=self.out_dir).grid(row=4, column=1, sticky="ew", padx=4)
        ttk.Button(st, text="Browse…", command=self.pick_out).grid(row=4, column=2, padx=8)
        ttk.Label(st, text=f"(empty = a '{cv.DEFAULT_OUT_SUBDIR}' folder next to each source file)",
                  foreground="#666").grid(row=5, column=1, sticky="w", padx=4)

        opts = ttk.Frame(st)
        opts.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(opts, text="Overwrite existing (otherwise skip)", variable=self.overwrite).pack(side="left")
        ttk.Checkbutton(opts, text="Keep EXIF (non-RAW)", variable=self.keep_exif).pack(side="left", padx=12)
        ttk.Label(opts, text="Video frame at (s):").pack(side="left", padx=(12, 4))
        ttk.Spinbox(opts, from_=0, to=36000, increment=0.5, width=6, textvariable=self.video_time).pack(side="left")

        # --- run
        run = ttk.Frame(self)
        run.pack(fill="x", **pad)
        self.go_btn = ttk.Button(run, text="Convert", command=self.start)
        self.go_btn.pack(side="left")
        self.stop_btn = ttk.Button(run, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(run, text="Open output folder", command=self.open_output_folder).pack(side="left")
        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.summary = ttk.Label(run, text="")
        self.summary.pack(side="right")

        warn = []
        if cv.rawpy is None:
            warn.append("rawpy missing → ARW/DNG disabled")
        if cv.pillow_heif is None:
            warn.append("pillow-heif missing → HEIC/HEIF disabled")
        if not cv.find_ffmpeg():
            warn.append("FFmpeg not found → video disabled")
        if warn:
            ttk.Label(self, text="⚠ " + "; ".join(warn), foreground="#b00").pack(anchor="w", padx=10)

    def _labels(self):
        self.q_lbl.config(text=f"{self.quality.get()}%")
        self.r_lbl.config(text=f"{self.resize.get()}%")

    def _fmt(self) -> str:
        return FORMAT_LABELS.get(self.fmt_label.get(), "jpeg")

    @staticmethod
    def _set(widget, enabled: bool, readonly: bool = False):
        if isinstance(widget, ttk.Combobox):
            widget.config(state="readonly" if enabled else "disabled")
        elif isinstance(widget, tk.Scale):
            widget.config(state="normal" if enabled else "disabled")
        else:
            widget.config(state="normal" if enabled else "disabled")

    def _update_state(self):
        """Enable only the options that apply to the chosen format."""
        f = self._fmt()
        jpeg, webp, png, tiff = f == "jpeg", f == "webp", f == "png", f == "tiff"
        tiff_jpeg = tiff and self.tiff_comp.get() == "jpeg"

        self._set(self.w_prog, jpeg)
        self._set(self.w_sub, jpeg)
        self._set(self.w_opt, jpeg)
        self._set(self.w_lossless, webp)
        self._set(self.w_png, png)
        self._set(self.w_tiff, tiff)
        self._set(self.q_scale, jpeg or webp or tiff_jpeg)

        hints = {
            "jpeg": "JPEG: lossy, works everywhere. 4:4:4 keeps the most colour detail; progressive loads in passes.",
            "webp": "WebP: usually 25-35% smaller than JPEG at the same quality. Lossless ignores the quality slider.",
            "png": "PNG: lossless and keeps transparency. The quality slider is not used; files are large for photos.",
            "tiff": "TIFF: lossless with lzw/deflate/none; choose 'jpeg' to compress with the quality slider.",
        }
        self.hint.config(text=hints[f])

    # ------------------------------------------------------------ file list
    def _add_paths(self, paths):
        existing = {str(p).lower() for p in self.items.values()}
        added = 0
        for p in cv.collect_files(paths, self.recursive.get()):
            if str(p).lower() in existing:
                continue
            self._counter += 1
            iid = f"i{self._counter}"
            self.items[iid] = p
            size = cv.human(p.stat().st_size)
            self.tree.insert("", "end", iid=iid, values=(str(p), p.suffix.upper().lstrip("."), size, "Queued"))
            added += 1
        self.summary.config(text=f"{len(self.items)} file(s)")
        return added

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(cv.ALL_EXTS))
        exts += " " + " ".join(f"*{e.upper()}" for e in sorted(cv.ALL_EXTS))
        paths = filedialog.askopenfilenames(title="Select files",
                                            filetypes=[("Supported files", exts), ("All files", "*.*")])
        if paths:
            self._add_paths(paths)

    def add_folder(self):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            n = self._add_paths([d])
            if n == 0:
                messagebox.showinfo("Nothing found", "No supported files in that folder.")

    def remove_selected(self):
        for iid in self.tree.selection():
            self.items.pop(iid, None)
            self.results.pop(iid, None)
            self.tree.delete(iid)
        self.summary.config(text=f"{len(self.items)} file(s)")

    def clear(self):
        if self.worker and self.worker.is_alive():
            return
        self.items.clear()
        self.results.clear()
        self.tree.delete(*self.tree.get_children())
        self.progress["value"] = 0
        self.summary.config(text="")

    def pick_out(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.out_dir.set(d)

    # ------------------------------------------------------------ open things
    @staticmethod
    def _open(path: Path):
        """Open a file or folder with the system's default application."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Could not open", f"{path}\n\n{e}")

    def on_row_open(self, event=None):
        """Double-click / Enter on a row: open the source, and the converted file if it exists."""
        if event is not None and event.type == tk.EventType.ButtonPress:
            iid = self.tree.identify_row(event.y)
        else:
            sel = self.tree.selection()
            iid = sel[0] if sel else ""
        if not iid or iid not in self.items:
            return
        src = self.items[iid]
        if src.exists():
            self._open(src)
        else:
            messagebox.showwarning("Missing file", f"Source file not found:\n{src}")
        dst = self.results.get(iid)
        if dst is not None and dst.exists():
            self._open(dst)

    def open_output_folder(self):
        typed = self.out_dir.get().strip()
        if typed:
            folder = Path(typed)
            folder.mkdir(parents=True, exist_ok=True)
            self._open(folder)
            return
        # No fixed output folder: use the one belonging to the selected (or latest) converted file
        sel = [i for i in self.tree.selection() if i in self.results]
        if sel:
            dst = self.results[sel[0]]
        elif self.results:
            dst = list(self.results.values())[-1]
        else:
            messagebox.showinfo("Nothing converted yet",
                                "Convert some files first, or choose an output folder.")
            return
        self._open(dst.parent)

    # ----------------------------------------------------------- conversion
    def start(self):
        if not self.items:
            messagebox.showinfo("No files", "Add some files first.")
            return
        jobs = list(self.items.items())
        out = Path(self.out_dir.get()) if self.out_dir.get().strip() else None
        try:
            png_level = max(0, min(9, int(self.png_level.get())))
        except (tk.TclError, ValueError):
            png_level = 6
        opts = dict(
            out_dir=out,
            quality=self.quality.get(),
            resize_pct=self.resize.get(),
            overwrite=self.overwrite.get(),
            keep_exif=self.keep_exif.get(),
            video_seconds=max(0.0, float(self.video_time.get() or 0)),
            fmt=self._fmt(),
            progressive=self.progressive.get(),
            subsampling=self.subsampling.get(),
            optimize=self.optimize.get(),
            lossless=self.lossless.get(),
            png_level=png_level,
            tiff_compression=self.tiff_comp.get(),
        )
        for iid, _ in jobs:
            self.results.pop(iid, None)
            self.tree.set(iid, "status", "Queued")
        self.progress.config(maximum=len(jobs), value=0)
        self.stop_flag.clear()
        self.go_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.worker = threading.Thread(target=self._work, args=(jobs, opts), daemon=True)
        self.worker.start()
        self.after(100, self._poll)

    def stop(self):
        self.stop_flag.set()
        self.stop_btn.config(state="disabled")

    def _work(self, jobs, opts):
        used: set[str] = set()
        stats = {"ok": 0, "skipped": 0, "error": 0}
        for iid, path in jobs:
            if self.stop_flag.is_set():
                break
            self.q.put(("status", iid, "Converting…"))
            r = cv.convert_file(path, used=used, **opts)
            stats[r.status] += 1
            self.q.put(("done", iid, r))
        self.q.put(("finished", stats))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.tree.set(msg[1], "status", msg[2])
                elif kind == "done":
                    iid, r = msg[1], msg[2]
                    if r.dst is not None and r.status in ("ok", "skipped"):
                        self.results[iid] = r.dst
                    if r.status == "ok":
                        text = f"✔ {cv.human(r.in_size)} → {cv.human(r.out_size)} ({r.message})"
                    elif r.status == "skipped":
                        text = "⏭ Skipped (exists)"
                    else:
                        text = f"✖ {r.message}"
                    self.tree.set(iid, "status", text)
                    self.progress["value"] += 1
                elif kind == "finished":
                    s = msg[1]
                    self.summary.config(text=f"{s['ok']} done, {s['skipped']} skipped, {s['error']} failed")
                    self.go_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()
