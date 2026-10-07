"""Wood WOP Labeling - window app.

Pick a folder (or files), review the labels, then add them. Each file is saved
back in its own folder under the same name; the original is copied to
Documents\\WoodWOP Label Backups first. Files or folders dropped onto the
program's icon are loaded at start.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import woodwop_label as wl

TITLE = "Wood WOP Labeling"
STATUS_TEXT = {
    wl.LABELED: "Ready to label",
    wl.ALREADY: "Already labeled",
    wl.NO_LABEL: "No label",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files: list[Path] = []
        self.results: dict[Path, wl.Result] = {}
        self.backup_root = wl.default_backup_root()

        root.title(TITLE)
        root.geometry("1100x700")
        root.minsize(800, 520)

        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Button(top, text="Choose Folder...", command=self.choose_folder).pack(side="left")
        ttk.Button(top, text="Choose Files...", command=self.choose_files).pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        self.where = ttk.Label(top, text="Choose the folder that holds the part files.")
        self.where.pack(side="left", padx=12)

        mid = ttk.Frame(root, padding=(10, 0))
        mid.pack(fill="both", expand=True)
        cols = ("file", "size", "job", "part", "status")
        self.table = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for col, text, width, stretch in (
            ("file", "File", 380, True),
            ("size", "Size (in)  L x W x T", 170, False),
            ("job", "Job", 90, False),
            ("part", "Part", 130, False),
            ("status", "Status", 260, True),
        ):
            self.table.heading(col, text=text, anchor="w")
            self.table.column(col, width=width, stretch=stretch, anchor="w")
        self.table.tag_configure("ok", foreground="#1a7f37")
        self.table.tag_configure("problem", foreground="#c62828")
        self.table.tag_configure("skip", foreground="#777777")
        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self.show_selected)

        self.preview = tk.Canvas(root, height=170, background="#c8d4f0", highlightthickness=0)
        self.preview.pack(fill="x", padx=10, pady=(6, 0))
        self.preview.bind("<Configure>", lambda _e: self.show_selected())

        info = ttk.Frame(root, padding=(10, 6, 10, 0))
        info.pack(fill="x")
        ttk.Label(info, text="Label text:").pack(side="left")
        self.label_text = tk.StringVar()
        ttk.Entry(info, textvariable=self.label_text, state="readonly").pack(side="left", fill="x", expand=True, padx=6)

        bottom = ttk.Frame(root, padding=10)
        bottom.pack(fill="x")
        self.add_btn = ttk.Button(bottom, text="Add Labels", command=self.add_labels)
        self.add_btn.pack(side="left")
        self.remove_btn = ttk.Button(bottom, text="Remove Labels", command=self.remove_labels)
        self.remove_btn.pack(side="left", padx=6)
        ttk.Button(bottom, text="Open Backups Folder", command=self.open_backups).pack(side="right")
        self.summary = ttk.Label(
            bottom, text="Files are saved back in their own folder. Originals are backed up first."
        )
        self.summary.pack(side="left", padx=12)
        self.update_buttons()

    # ------------------------------------------------------------ loading

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Folder with the part files")
        if folder:
            self.load([Path(folder)])

    def choose_files(self):
        names = filedialog.askopenfilenames(
            title="Part files", filetypes=[("woodWOP files", "*.mpr *.MPR"), ("All files", "*.*")]
        )
        if names:
            self.load([Path(n) for n in names])

    def load(self, paths: list[Path]):
        self.files = wl.find_mpr_files(paths)
        folders = sorted({str(f.parent) for f in self.files})
        if not self.files:
            self.where.config(text="No .mpr files found there.")
        elif len(folders) == 1:
            self.where.config(text=folders[0])
        else:
            self.where.config(text=f"{len(folders)} folders")
        self.refresh()

    def refresh(self):
        self.results = {}
        for f in self.files:
            try:
                self.results[f] = wl.plan(f)
            except OSError as e:
                self.results[f] = wl.Result(wl.PROBLEM, f"can't read file: {e.strerror or e}")
        self.redraw()

    # ------------------------------------------------------------ display

    def redraw(self, done: dict[Path, str] | None = None):
        done = done or {}
        self.table.delete(*self.table.get_children())
        for f in self.files:
            r = self.results[f]
            info = r.info
            if f in done:
                status, tag = done[f], ("problem" if done[f].startswith("Problem") else "ok")
            elif r.status == wl.PROBLEM:
                text = r.message if r.message.startswith("DANGER") else f"Problem: {r.message}"
                status, tag = text, "problem"
            else:
                status = STATUS_TEXT.get(r.status, r.message)
                tag = "skip" if r.status in (wl.ALREADY, wl.NO_LABEL) else ""
                if r.status == wl.SHEET:
                    tag = "ok" if r.message.startswith("sheet OK") else "skip"
            self.table.insert(
                "",
                "end",
                iid=str(f),
                values=(f.name, info.size_text if info else "", (info.job or "") if info else "",
                        info.part if info else "", status),
                tags=(tag,),
            )
        counts = {}
        for r in self.results.values():
            counts[r.status] = counts.get(r.status, 0) + 1
        if self.files and not done:
            parts = [f"{len(self.files)} files"]
            for key, word in ((wl.LABELED, "to label"), (wl.ALREADY, "already labeled"),
                              (wl.SHEET, "sheet file(s) checked"), (wl.PROBLEM, "problem(s)")):
                if counts.get(key):
                    parts.append(f"{counts[key]} {word}")
            self.summary.config(text=", ".join(parts))
        self.label_text.set("")
        self.update_buttons()

    def show_selected(self, _event=None):
        sel = self.table.selection()
        r = self.results.get(Path(sel[0])) if sel else None
        self.label_text.set(r.info.label if r and r.info else (r.message if r else ""))
        self.draw_preview(r)

    def draw_preview(self, r: wl.Result | None):
        """The part outline with the label drawn on it, as it will look in woodWOP."""
        c = self.preview
        c.delete("all")
        if not r or not r.info:
            c.create_text(12, 12, anchor="nw", fill="#555555",
                          text="Click a part file above to see the label drawn on it.")
            return
        cw, ch = max(c.winfo_width(), 200), int(c["height"])
        length, width = r.info.length_mm, r.info.width_mm
        k = min((cw - 24) / length, (ch - 24) / width)
        ox, oy = (cw - length * k) / 2, (ch - width * k) / 2
        c.create_rectangle(ox, oy, ox + length * k, oy + width * k, outline="#d32f2f", width=2)
        for poly in r.drawing:
            c.create_line(*[v for x, y in poly for v in (ox + x * k, oy + (width - y) * k)], fill="black")
        if not r.drawing:
            c.create_text(cw / 2, ch / 2, fill="#555555",
                          text="Part too small to draw on - the label is still added as hidden text.")

    def update_buttons(self):
        has_files = bool(self.files)
        to_label = any(r.status == wl.LABELED for r in self.results.values())
        self.add_btn.state(["!disabled"] if to_label else ["disabled"])
        self.remove_btn.state(["!disabled"] if has_files else ["disabled"])

    # ------------------------------------------------------------ actions

    def add_labels(self):
        todo = [f for f in self.files if self.results[f].status == wl.LABELED]
        if not todo:
            return
        if not messagebox.askyesno(
            TITLE,
            f"Add labels to {len(todo)} file(s) and save them in place?\n\n"
            f"Originals are copied to:\n{self.backup_root}",
        ):
            return
        self.write(todo, {f: self.results[f] for f in todo}, "Labeled")

    def remove_labels(self):
        plans = {}
        for f in self.files:
            try:
                r = wl.plan(f, remove=True)
            except OSError:
                continue
            if r.status == wl.REMOVED:
                plans[f] = r
        if not plans:
            messagebox.showinfo(TITLE, "None of these files has a label.")
            return
        if not messagebox.askyesno(
            TITLE,
            f"Remove labels from {len(plans)} file(s) and save them in place?\n\n"
            f"Originals are copied to:\n{self.backup_root}",
        ):
            return
        self.write(list(plans), plans, "Label removed")

    def write(self, files: list[Path], plans: dict[Path, wl.Result], ok_text: str):
        backup_dir = wl.new_backup_dir(self.backup_root)
        done: dict[Path, str] = {}
        failed = 0
        for f in files:
            try:
                wl.save(f, plans[f], backup_dir)
                done[f] = ok_text
            except (OSError, wl.LabelError) as e:
                done[f] = f"Problem: {getattr(e, 'strerror', None) or e}"
                failed += 1
        # re-read so the table shows what is on disk now, with the save result as status
        for f in self.files:
            try:
                self.results[f] = wl.plan(f)
            except OSError as e:
                self.results[f] = wl.Result(wl.PROBLEM, str(e))
        self.redraw(done)
        msg = f"{len(files) - failed} of {len(files)} file(s) saved."
        if failed:
            msg += f" {failed} had problems and were left as they were."
        self.summary.config(text=msg + f"  Backups: {backup_dir}")
        (messagebox.showwarning if failed else messagebox.showinfo)(TITLE, msg)

    def open_backups(self):
        self.backup_root.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(self.backup_root)  # noqa: S606 - opens Explorer on our own folder
        else:
            subprocess.Popen(["xdg-open", str(self.backup_root)])


def selftest(sample_dir: Path) -> int:
    """Label a temporary copy of ``sample_dir`` through the real window and check the result.

    Used by the build to prove the finished .exe works on Windows. Exit code 0 = pass.
    """
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    job = tmp / "job"
    shutil.copytree(sample_dir, job)
    root = tk.Tk()
    app = App(root)
    app.backup_root = tmp / "backups"
    app.load([job])
    root.update()
    todo = [f for f in app.files if app.results[f].status == wl.LABELED]
    expected = {f: app.results[f].new_data for f in todo}
    originals = {f: f.read_bytes() for f in todo}
    messagebox.showinfo = messagebox.showwarning = lambda *a, **k: None
    app.write(todo, {f: app.results[f] for f in todo}, "Labeled")
    root.update()
    backups = {b.name: b.read_bytes() for b in app.backup_root.rglob("*") if b.is_file()}
    ok = (
        bool(todo)
        and all(f.read_bytes() == expected[f] for f in todo)
        and all(app.results[f].status == wl.ALREADY for f in todo)
        and all(backups.get(f.name) == originals[f] for f in todo)
        and sorted(p.name for p in job.iterdir()) == sorted(p.name for p in Path(sample_dir).iterdir())
    )
    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(Path(sys.argv[2])))
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    app = App(root)
    dropped = [Path(a) for a in sys.argv[1:]]
    if dropped:
        app.load(dropped)
    root.mainloop()


if __name__ == "__main__":
    main()
