"""Wood WOP Labeling - window app.

Pick a folder (or files), review the labels, then add them. Each file is saved
back in its own folder under the same name; the original is copied to
Documents\\WoodWOP Label Backups first. Files or folders dropped onto the
program's icon are loaded at start.

With "Auto-label" on, every part file in the chosen folder gets labeled as soon
as it has finished saving, for as long as the window is open.
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
AUTO_SECONDS = 3  # how often auto-label looks for new files; a file must be unchanged for this long
WAITING = "waiting"
STATUS_TEXT = {
    wl.LABELED: "Ready to label",
    wl.ALREADY: "Already labeled",
    wl.NO_LABEL: "No label",
    WAITING: "Waiting for the file to finish saving...",
}


def plan_file(f: Path, remove: bool = False) -> wl.Result:
    try:
        return wl.plan(f, remove)
    except OSError as e:
        return wl.Result(wl.PROBLEM, f"can't read file: {e.strerror or e}")


def stat_key(f: Path) -> tuple[int, int] | None:
    try:
        st = f.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.sources: list[Path] = []
        self.files: list[Path] = []
        self.results: dict[Path, wl.Result] = {}
        self.done: dict[Path, str] = {}  # last save result per file, shown while the file stays labeled
        self.planned: dict[Path, tuple[int, int] | None] = {}  # file state each result was built from
        self.seen: dict[Path, tuple[int, int] | None] = {}  # file state at the last auto-label look
        self.backup_root = wl.default_backup_root()
        self.auto_backup_dir: Path | None = None

        root.title(TITLE)
        root.geometry("1000x680")
        root.minsize(760, 520)

        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Button(top, text="Choose Folder...", command=self.choose_folder).pack(side="left")
        ttk.Button(top, text="Choose Files...", command=self.choose_files).pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Auto-label new files in this folder", variable=self.auto,
                        command=self.toggle_auto).pack(side="left", padx=14)
        self.where = ttk.Label(top, text="Choose the folder that holds the part files.")
        self.where.pack(side="left", padx=6)

        mid = ttk.Frame(root, padding=(10, 0))
        mid.pack(fill="both", expand=True)
        cols = ("file", "size", "status")
        self.table = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for col, text, width, stretch in (
            ("file", "File", 420, True),
            ("size", "Label (in)  L x W", 150, False),
            ("status", "Status", 330, True),
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
        self.auto.set(False)
        self.sources = paths
        self.refresh()

    def refresh(self):
        self.files = wl.find_mpr_files(self.sources)
        folders = sorted({str(f.parent) for f in self.files} | {str(p) for p in self.sources if p.is_dir()})
        if not self.sources:
            pass
        elif len(folders) == 1:
            self.where.config(text=folders[0] + ("" if self.files else "  (no .mpr files yet)"))
        elif folders:
            self.where.config(text=f"{len(folders)} folders")
        else:
            self.where.config(text="No .mpr files found there.")
        self.done = {}
        self.results = {}
        for f in self.files:
            self.planned[f] = stat_key(f)
            self.results[f] = plan_file(f)
        self.redraw()

    # ------------------------------------------------------------ display

    def redraw(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for f in self.files:
            r = self.results[f]
            if f in self.done and (r.status in (wl.ALREADY, wl.NO_LABEL) or self.done[f].startswith("Problem")):
                status = self.done[f]
                tag = "problem" if status.startswith("Problem") else "ok"
            elif r.status == wl.PROBLEM:
                status = r.message if r.message.startswith("DANGER") else f"Problem: {r.message}"
                tag = "problem"
            elif r.status == wl.SHEET:
                status, tag = r.message, ("ok" if r.message.startswith("sheet OK") else "skip")
            else:
                status = STATUS_TEXT.get(r.status, r.message)
                tag = "skip" if r.status in (wl.ALREADY, wl.NO_LABEL, WAITING) else ""
            size = r.info.size_text if r.info else ""
            self.table.insert("", "end", iid=str(f), values=(f.name, size, status), tags=(tag,))
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        if self.files and not self.done:
            counts: dict[str, int] = {}
            for r in self.results.values():
                counts[r.status] = counts.get(r.status, 0) + 1
            parts = [f"{len(self.files)} files"]
            for key, word in ((wl.LABELED, "to label"), (wl.ALREADY, "already labeled"),
                              (wl.SHEET, "sheet file(s) checked"), (wl.PROBLEM, "problem(s)")):
                if counts.get(key):
                    parts.append(f"{counts[key]} {word}")
            self.summary.config(text=", ".join(parts))
        self.show_selected()
        self.update_buttons()

    def show_selected(self, _event=None):
        sel = self.table.selection()
        self.draw_preview(self.results.get(Path(sel[0])) if sel else None)

    def draw_preview(self, r: wl.Result | None):
        """The part outline with the label drawn on it, as it will look in woodWOP."""
        c = self.preview
        c.delete("all")
        if not r or not r.info:
            c.create_text(12, 12, anchor="nw", fill="#555555",
                          text=r.message if r else "Click a part file above to see the label drawn on it.")
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
                          text="Part too small to draw on - the size is still added to its variable list.")

    def update_buttons(self):
        to_label = any(r.status == wl.LABELED for r in self.results.values())
        self.add_btn.state(["!disabled"] if to_label and not self.auto.get() else ["disabled"])
        self.remove_btn.state(["!disabled"] if self.files and not self.auto.get() else ["disabled"])

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
        self.write(todo, {f: self.results[f] for f in todo}, "Labeled", wl.new_backup_dir(self.backup_root))

    def remove_labels(self):
        plans = {f: r for f in self.files if (r := plan_file(f, remove=True)).status == wl.REMOVED}
        if not plans:
            messagebox.showinfo(TITLE, "None of these files has a label.")
            return
        if not messagebox.askyesno(
            TITLE,
            f"Remove labels from {len(plans)} file(s) and save them in place?\n\n"
            f"Originals are copied to:\n{self.backup_root}",
        ):
            return
        self.write(list(plans), plans, "Label removed", wl.new_backup_dir(self.backup_root))

    def write(self, files: list[Path], plans: dict[Path, wl.Result], ok_text: str, backup_dir: Path,
              quiet: bool = False):
        failed = 0
        for f in files:
            try:
                wl.save(f, plans[f], backup_dir)
                self.done[f] = ok_text
            except (OSError, wl.LabelError) as e:
                self.done[f] = f"Problem: {getattr(e, 'strerror', None) or e}"
                failed += 1
            # show what is on disk now
            self.planned[f] = stat_key(f)
            self.results[f] = plan_file(f)
        msg = f"{len(files) - failed} of {len(files)} file(s) saved."
        if failed:
            msg += f" {failed} had problems and were left as they were."
        self.redraw()
        self.summary.config(text=msg + f"  Backups: {backup_dir}")
        if not quiet:
            (messagebox.showwarning if failed else messagebox.showinfo)(TITLE, msg)

    # ------------------------------------------------------------ auto-label

    def toggle_auto(self):
        if not self.auto.get():
            self.summary.config(text="Auto-label is off.")
            self.update_buttons()
            return
        folders = [p for p in self.sources if p.is_dir()]
        if not folders:
            self.auto.set(False)
            messagebox.showinfo(TITLE, "Choose a folder first, then turn on auto-label.")
            return
        if not messagebox.askyesno(
            TITLE,
            "Turn on auto-label?\n\n"
            f"Every part file in {folders[0]} that isn't labeled yet will be labeled and saved, "
            "now and whenever new files arrive, while this window is open.\n\n"
            f"Sheet files are only checked, never changed. Originals are copied to:\n{self.backup_root}",
        ):
            self.auto.set(False)
            return
        self.auto_backup_dir = wl.new_backup_dir(self.backup_root)
        self.seen = {}
        self.update_buttons()
        self.auto_tick()

    def auto_tick(self):
        if not self.auto.get():
            return
        try:
            self.auto_scan()
        finally:
            self.root.after(AUTO_SECONDS * 1000, self.auto_tick)

    def auto_scan(self):
        """One auto-label pass: label every part file that has stopped changing since the last pass."""
        self.files = wl.find_mpr_files(self.sources)
        ready = []
        for f in self.files:
            key = stat_key(f)
            settled = key is not None and self.seen.get(f) == key
            self.seen[f] = key
            if not settled:
                if self.planned.get(f) != key:
                    self.results[f] = wl.Result(WAITING, STATUS_TEXT[WAITING])
                    self.planned[f] = None
                continue
            if self.planned.get(f) != key:
                self.results[f] = plan_file(f)
                self.planned[f] = key
            if self.results[f].status == wl.LABELED:
                ready.append(f)
        if ready:
            assert self.auto_backup_dir is not None
            self.write(ready, {f: self.results[f] for f in ready}, "Labeled automatically", self.auto_backup_dir,
                       quiet=True)
        else:
            self.redraw()
            self.summary.config(text=f"Auto-label is on. Watching {len(self.files)} file(s).")

    def open_backups(self):
        self.backup_root.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(self.backup_root)  # noqa: S606 - opens Explorer on our own folder
        else:
            subprocess.Popen(["xdg-open", str(self.backup_root)])


def selftest(sample_dir: Path) -> int:
    """Label a temporary copy of ``sample_dir`` through the real window, both by
    button and by auto-label, and check the result.

    Used by the build to prove the finished .exe works on Windows. Exit code 0 = pass.
    """
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    job = tmp / "job"
    shutil.copytree(sample_dir, job)
    names = sorted(p.name for p in job.iterdir())
    root = tk.Tk()
    app = App(root)
    app.backup_root = tmp / "backups"
    messagebox.showinfo = messagebox.showwarning = lambda *a, **k: None
    messagebox.askyesno = lambda *a, **k: True
    ok = True

    # 1. by button
    app.load([job])
    root.update()
    todo = [f for f in app.files if app.results[f].status == wl.LABELED]
    expected = {f: app.results[f].new_data for f in todo}
    originals = {f: f.read_bytes() for f in todo}
    app.add_labels()
    root.update()
    backups = {b.name: b.read_bytes() for b in app.backup_root.rglob("*") if b.is_file()}
    ok &= bool(todo) and all(f.read_bytes() == expected[f] for f in todo)
    ok &= all(app.results[f].status == wl.ALREADY for f in todo)
    ok &= all(backups.get(f.name) == originals[f] for f in todo)
    ok &= sorted(p.name for p in job.iterdir()) == names

    # 2. by auto-label: a new unlabeled part dropped into the folder
    new = job / ("NEW_" + todo[0].name)
    new.write_bytes(originals[todo[0]])
    app.auto.set(True)
    app.toggle_auto()  # runs the first pass and schedules more
    app.auto_scan()  # second pass: the new file has settled, so it gets labeled
    root.update()
    ok &= new.read_bytes() == expected[todo[0]]
    ok &= app.done.get(new) == "Labeled automatically"
    app.auto.set(False)

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
