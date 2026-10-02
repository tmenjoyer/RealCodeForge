#!/usr/bin/env python3
"""CodeForge - a lightweight, VS Code-inspired code editor written in Python (Tkinter).

Features: file explorer, tabs, syntax highlighting (Pygments), line numbers,
find/replace, quick open (Ctrl+P), integrated terminal, run file (F5),
comment toggling, auto-indent and zoom.
"""

import os
import re
import sys
import queue
import shutil
import threading
import subprocess
import webbrowser
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename
    from pygments.token import Keyword, Name, String, Number, Comment, Operator
    from pygments.util import ClassNotFound
    HAS_PYGMENTS = True
except ImportError:  # the editor still works, just without highlighting
    HAS_PYGMENTS = False

APP_NAME = "CodeForge"
VERSION = "1.0.0"

THEME = {
    "bg": "#1e1e1e",
    "side": "#252526",
    "fg": "#d4d4d4",
    "sel": "#264f78",
    "line_fg": "#858585",
    "tab_bg": "#2d2d2d",
    "tab_fg": "#969696",
    "status": "#007acc",
    "input": "#3c3c3c",
    "border": "#3a3a3a",
}

SYNTAX = {
    "keyword": "#569cd6",
    "builtin": "#4ec9b0",
    "function": "#dcdcaa",
    "class_": "#4ec9b0",
    "string": "#ce9178",
    "comment": "#6a9955",
    "number": "#b5cea8",
    "decorator": "#c586c0",
}

if HAS_PYGMENTS:
    TOKEN_TAGS = {
        Keyword: "keyword",
        Operator.Word: "keyword",
        Name.Tag: "keyword",
        Name.Builtin: "builtin",
        Name.Attribute: "builtin",
        Name.Function: "function",
        Name.Class: "class_",
        Name.Exception: "class_",
        Name.Decorator: "decorator",
        String: "string",
        Number: "number",
        Comment: "comment",
    }
else:
    TOKEN_TAGS = {}

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}

COMMENT_PREFIX = {
    ".py": "# ", ".sh": "# ", ".rb": "# ", ".yml": "# ", ".yaml": "# ", ".toml": "# ",
    ".js": "// ", ".ts": "// ", ".java": "// ", ".c": "// ", ".cpp": "// ", ".h": "// ",
    ".cs": "// ", ".go": "// ", ".rs": "// ", ".php": "// ", ".sql": "-- ", ".lua": "-- ",
    ".bat": "REM ",
}

RUNNERS = {
    ".py": 'python "{f}"',
    ".js": 'node "{f}"',
    ".sh": 'bash "{f}"',
    ".bat": '"{f}"',
    ".cmd": '"{f}"',
    ".ps1": 'powershell -ExecutionPolicy Bypass -File "{f}"',
    ".rb": 'ruby "{f}"',
    ".go": 'go run "{f}"',
    ".php": 'php "{f}"',
    ".lua": 'lua "{f}"',
    ".java": 'java "{f}"',
}

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def tag_for(ttype):
    """Map a Pygments token type to one of our tag names (walks up the hierarchy)."""
    while ttype is not None:
        tag = TOKEN_TAGS.get(ttype)
        if tag:
            return tag
        ttype = ttype.parent
    return None


def token_ranges(text, lexer):
    """Yield (tag, start_index, end_index) in Tk 'line.col' notation."""
    line, col = 1, 0
    for ttype, value in lex(text, lexer):
        newlines = value.count("\n")
        if newlines:
            end_line = line + newlines
            end_col = len(value) - value.rfind("\n") - 1
        else:
            end_line, end_col = line, col + len(value)
        tag = tag_for(ttype)
        if tag:
            yield tag, f"{line}.{col}", f"{end_line}.{end_col}"
        line, col = end_line, end_col


class Editor(ttk.Frame):
    """One editor tab: text widget + line numbers + scrollbars."""

    def __init__(self, app, path=None):
        super().__init__(app.notebook)
        self.app = app
        self.path = path
        self.encoding = "utf-8"
        self.modified = False
        self.untitled = None
        self.language = "Plain Text"
        self.lexer = None
        self._hl_job = None
        self._loading = False

        self.numbers = tk.Canvas(self, width=52, bg=THEME["bg"], highlightthickness=0, bd=0)
        self.text = tk.Text(
            self, undo=True, maxundo=-1, wrap="none", font=app.font,
            bg=THEME["bg"], fg=THEME["fg"], insertbackground=THEME["fg"],
            selectbackground=THEME["sel"], selectforeground="#ffffff",
            inactiveselectbackground=THEME["sel"], relief="flat", borderwidth=0,
            highlightthickness=0, padx=8, pady=4,
        )
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self.hsb.set)

        self.numbers.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        self.vsb.grid(row=0, column=2, sticky="ns")
        self.hsb.grid(row=1, column=1, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        for name, color in SYNTAX.items():
            self.text.tag_configure(name, foreground=color)
        self.text.tag_raise("sel")
        self.apply_tabs()

        t = self.text
        t.bind("<<Modified>>", self._on_modified)
        t.bind("<Return>", self._auto_indent)
        t.bind("<Tab>", self._on_tab)
        t.bind("<Shift-Tab>", self._on_shift_tab)
        t.bind("<ISO_Left_Tab>", self._on_shift_tab)
        t.bind("<Control-a>", self._select_all)
        t.bind("<Control-y>", lambda e: self._redo())
        t.bind("<KeyRelease>", lambda e: app.update_status())
        t.bind("<ButtonRelease-1>", lambda e: app.update_status())
        t.bind("<Configure>", lambda e: self.redraw_numbers())
        t.bind("<Button-3>", self._context_menu)
        # App-level shortcuts must be bound here too, because Tk's class
        # bindings (e.g. Ctrl+O inserts a newline) run before bind_all ones.
        for seq, fn in app.shortcuts:
            t.bind(seq, lambda e, f=fn: (f(), "break")[1])

        self.menu = tk.Menu(t, tearoff=0, bg=THEME["side"], fg=THEME["fg"],
                            activebackground=THEME["sel"], activeforeground="#ffffff")
        for label, ev in (("Cut", "<<Cut>>"), ("Copy", "<<Copy>>"), ("Paste", "<<Paste>>")):
            self.menu.add_command(label=label, command=lambda ev=ev: t.event_generate(ev))
        self.menu.add_separator()
        self.menu.add_command(label="Select All", command=lambda: self._select_all())

    # ------------------------------------------------------------ helpers
    @property
    def display_name(self):
        if self.path:
            return os.path.basename(self.path)
        return f"Untitled-{self.untitled or 1}"

    def apply_tabs(self):
        self.text.configure(tabs=(self.app.font.measure(" " * 4),))

    def _context_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def _select_all(self, event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _redo(self):
        try:
            self.text.edit_redo()
        except tk.TclError:
            pass
        return "break"

    def _on_yscroll(self, first, last):
        self.vsb.set(first, last)
        self.redraw_numbers()

    def redraw_numbers(self):
        t = self.text
        self.numbers.delete("all")
        total = int(t.index("end-1c").split(".")[0])
        digits = max(3, len(str(total)))
        width = self.app.font.measure("0") * digits + 20
        if int(self.numbers.cget("width")) != width:
            self.numbers.configure(width=width)
        index = t.index("@0,0")
        while True:
            info = t.dlineinfo(index)
            if info is None:
                break
            self.numbers.create_text(
                width - 10, info[1], anchor="ne", text=index.split(".")[0],
                fill=THEME["line_fg"], font=self.app.font,
            )
            nxt = t.index(f"{index}+1line")
            if t.compare(nxt, ">=", "end") or nxt == index:
                break
            index = nxt

    # ------------------------------------------------------ modification
    def set_modified(self, flag):
        self.modified = flag
        self.app.update_tab_title(self)

    def _on_modified(self, event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            if not self._loading:
                self.set_modified(True)
            self.schedule_highlight()
            self.redraw_numbers()

    # ------------------------------------------------------ highlighting
    def detect_lexer(self):
        self.lexer = None
        self.language = "Plain Text"
        if HAS_PYGMENTS and self.path:
            try:
                self.lexer = get_lexer_for_filename(self.path, stripnl=False)
                self.language = self.lexer.name
            except ClassNotFound:
                pass

    def schedule_highlight(self, delay=250):
        if self._hl_job:
            self.after_cancel(self._hl_job)
        self._hl_job = self.after(delay, self.highlight)

    def highlight(self):
        self._hl_job = None
        t = self.text
        for tag in SYNTAX:
            t.tag_remove(tag, "1.0", "end")
        if not self.lexer:
            return
        content = t.get("1.0", "end-1c")
        if len(content) > 400_000:  # keep huge files responsive
            return
        for tag, start, end in token_ranges(content, self.lexer):
            t.tag_add(tag, start, end)

    # ------------------------------------------------------- file I/O
    def load(self, path):
        data = None
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as fh:
                    data = fh.read()
                self.encoding = enc
                break
            except UnicodeDecodeError:
                continue
        self._loading = True
        self.text.delete("1.0", "end")
        self.text.insert("1.0", data or "")
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._loading = False
        self.path = path
        self.set_modified(False)
        self.detect_lexer()
        self.highlight()
        self.text.mark_set("insert", "1.0")
        self.redraw_numbers()

    def save(self, path=None):
        target = path or self.path
        if not target:
            return False
        data = self.text.get("1.0", "end-1c")
        try:
            with open(target, "w", encoding=self.encoding, errors="replace", newline="") as fh:
                fh.write(data)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save file:\n{exc}")
            return False
        if path and path != self.path:
            self.path = path
            self.detect_lexer()
            self.highlight()
        self.set_modified(False)
        self.app.update_status()
        return True

    # --------------------------------------------------- text editing
    def selected_lines(self):
        t = self.text
        try:
            first = int(t.index("sel.first").split(".")[0])
            last_line, last_col = map(int, t.index("sel.last").split("."))
            if last_col == 0 and last_line > first:
                last_line -= 1
            return first, last_line
        except tk.TclError:
            line = int(t.index("insert").split(".")[0])
            return line, line

    def _has_multiline_selection(self):
        first, last = self.selected_lines()
        return bool(self.text.tag_ranges("sel")) and last > first

    def indent_lines(self, dedent=False):
        t = self.text
        first, last = self.selected_lines()
        for n in range(first, last + 1):
            if dedent:
                line = t.get(f"{n}.0", f"{n}.end")
                if line.startswith("\t"):
                    t.delete(f"{n}.0", f"{n}.1")
                else:
                    remove = len(line) - len(line.lstrip(" "))
                    remove = min(remove, 4)
                    if remove:
                        t.delete(f"{n}.0", f"{n}.{remove}")
            else:
                t.insert(f"{n}.0", "    ")
        t.edit_separator()

    def _on_tab(self, event):
        t = self.text
        if self._has_multiline_selection():
            self.indent_lines()
        else:
            if t.tag_ranges("sel"):
                t.delete("sel.first", "sel.last")
            t.insert("insert", "    ")
        return "break"

    def _on_shift_tab(self, event):
        self.indent_lines(dedent=True)
        return "break"

    def _auto_indent(self, event):
        t = self.text
        if t.tag_ranges("sel"):
            t.delete("sel.first", "sel.last")
        before = t.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", before).group(0)
        stripped = before.rstrip()
        is_py = (self.path or "").lower().endswith(".py")
        if stripped.endswith(("{", "(", "[")) or (is_py and stripped.endswith(":")):
            indent += "    "
        t.insert("insert", "\n" + indent)
        t.see("insert")
        return "break"

    def toggle_comment(self):
        t = self.text
        ext = os.path.splitext(self.path or "")[1].lower()
        prefix = COMMENT_PREFIX.get(ext, "# ")
        bare = prefix.strip()
        first, last = self.selected_lines()
        numbers = list(range(first, last + 1))
        lines = [t.get(f"{n}.0", f"{n}.end") for n in numbers]
        nonempty = [ln for ln in lines if ln.strip()]
        if not nonempty:
            return
        uncomment = all(ln.lstrip().startswith(bare) for ln in nonempty)
        for n, ln in zip(numbers, lines):
            if not ln.strip():
                continue
            if uncomment:
                idx = ln.index(bare)
                length = len(bare)
                if ln[idx + length: idx + length + 1] == " ":
                    length += 1
                t.delete(f"{n}.{idx}", f"{n}.{idx + length}")
            else:
                indent = len(ln) - len(ln.lstrip())
                t.insert(f"{n}.{indent}", prefix)
        t.edit_separator()


class App(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x800")
        self.minsize(720, 480)
        self.configure(bg=THEME["bg"])

        self.workspace = os.path.expanduser("~")
        for arg in args:
            if os.path.isdir(arg):
                self.workspace = os.path.abspath(arg)
        self.cwd = self.workspace
        self.untitled_count = 0
        self.out_queue = queue.Queue()
        self.proc = None
        self.history = []
        self.hist_pos = 0
        self.explorer_visible = True
        self.terminal_visible = True
        self.node_paths = {}
        self.dummies = set()
        self._status_job = None

        self.font = tkfont.Font(family=self._pick_font(), size=11)
        self.shortcuts = [
            ("<Control-n>", self.new_file),
            ("<Control-o>", self.open_file_dialog),
            ("<Control-O>", self.open_folder_dialog),
            ("<Control-s>", self.save_current),
            ("<Control-S>", self.save_as),
            ("<Control-w>", self.close_current),
            ("<Control-f>", self.show_find),
            ("<Control-h>", lambda: self.show_find(True)),
            ("<Control-g>", self.goto_line),
            ("<Control-p>", self.quick_open),
            ("<Control-b>", self.toggle_explorer),
            ("<Control-grave>", self.toggle_terminal),
            ("<Control-slash>", self.toggle_comment),
            ("<Control-equal>", lambda: self.zoom(1)),
            ("<Control-plus>", lambda: self.zoom(1)),
            ("<Control-minus>", lambda: self.zoom(-1)),
            ("<F5>", self.run_current),
        ]

        self._setup_style()
        self._build_menu()
        self._build_layout()
        for seq, fn in self.shortcuts:
            self.bind_all(seq, lambda e, f=fn: (f(), "break")[1])

        self.refresh_tree()
        for arg in args:
            if os.path.isfile(arg):
                self.open_file(arg)
        if not self.notebook.tabs():
            self.new_file()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self.poll_output)

    # ---------------------------------------------------------- setup
    @staticmethod
    def _pick_font():
        families = set(tkfont.families())
        for name in ("Cascadia Code", "Consolas", "DejaVu Sans Mono", "Menlo", "Courier New"):
            if name in families:
                return name
        return "Courier"

    def _setup_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=THEME["bg"], foreground=THEME["fg"], borderwidth=0)
        s.configure("TFrame", background=THEME["bg"])
        s.configure("Side.TLabel", background=THEME["side"], foreground=THEME["line_fg"],
                    font=("Segoe UI", 9))
        s.configure("Treeview", background=THEME["side"], fieldbackground=THEME["side"],
                    foreground=THEME["fg"], rowheight=22, borderwidth=0)
        s.map("Treeview", background=[("selected", THEME["sel"])],
              foreground=[("selected", "#ffffff")])
        s.configure("TNotebook", background=THEME["side"], borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=THEME["tab_bg"], foreground=THEME["tab_fg"],
                    padding=(14, 6), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", THEME["bg"])],
              foreground=[("selected", "#ffffff")])
        s.configure("TScrollbar", background="#424242", troughcolor=THEME["bg"],
                    bordercolor=THEME["bg"], arrowcolor=THEME["fg"], relief="flat")
        s.map("TScrollbar", background=[("active", "#4f4f4f")])
        s.configure("Tool.TButton", background=THEME["input"], foreground=THEME["fg"],
                    padding=(8, 2), relief="flat")
        s.map("Tool.TButton", background=[("active", "#505050")])

    def _build_menu(self):
        bar = tk.Menu(self, bg=THEME["side"], fg=THEME["fg"], relief="flat", bd=0,
                      activebackground=THEME["sel"], activeforeground="#ffffff")

        def make(label, items):
            m = tk.Menu(bar, tearoff=0, bg=THEME["side"], fg=THEME["fg"],
                        activebackground=THEME["sel"], activeforeground="#ffffff")
            for item in items:
                if item is None:
                    m.add_separator()
                else:
                    m.add_command(label=item[0], command=item[1],
                                  accelerator=item[2] if len(item) > 2 else "")
            bar.add_cascade(label=label, menu=m)

        make("File", [
            ("New File", self.new_file, "Ctrl+N"),
            ("Open File...", self.open_file_dialog, "Ctrl+O"),
            ("Open Folder...", self.open_folder_dialog, "Ctrl+Shift+O"),
            None,
            ("Save", self.save_current, "Ctrl+S"),
            ("Save As...", self.save_as, "Ctrl+Shift+S"),
            None,
            ("Close Tab", self.close_current, "Ctrl+W"),
            ("Exit", self.on_close),
        ])
        make("Edit", [
            ("Undo", lambda: self.edit_cmd("undo"), "Ctrl+Z"),
            ("Redo", lambda: self.edit_cmd("redo"), "Ctrl+Y"),
            None,
            ("Cut", lambda: self.clip("<<Cut>>"), "Ctrl+X"),
            ("Copy", lambda: self.clip("<<Copy>>"), "Ctrl+C"),
            ("Paste", lambda: self.clip("<<Paste>>"), "Ctrl+V"),
            None,
            ("Find", self.show_find, "Ctrl+F"),
            ("Replace", lambda: self.show_find(True), "Ctrl+H"),
            ("Go to Line...", self.goto_line, "Ctrl+G"),
            ("Toggle Line Comment", self.toggle_comment, "Ctrl+/"),
        ])
        make("View", [
            ("Go to File...", self.quick_open, "Ctrl+P"),
            ("Toggle Explorer", self.toggle_explorer, "Ctrl+B"),
            ("Toggle Terminal", self.toggle_terminal, "Ctrl+`"),
            None,
            ("Zoom In", lambda: self.zoom(1), "Ctrl+="),
            ("Zoom Out", lambda: self.zoom(-1), "Ctrl+-"),
        ])
        make("Run", [("Run File", self.run_current, "F5"), ("Stop", self.stop_process)])
        make("Help", [("About", self.about)])
        self.config(menu=bar)

    def _build_layout(self):
        # Status bar
        status = tk.Frame(self, bg=THEME["status"], height=22)
        status.pack(side="bottom", fill="x")
        self.status_msg = tk.Label(status, text="Ready", bg=THEME["status"], fg="#ffffff",
                                   font=("Segoe UI", 9), anchor="w")
        self.status_msg.pack(side="left", padx=10)
        self.status_lang = tk.Label(status, text="", bg=THEME["status"], fg="#ffffff",
                                    font=("Segoe UI", 9))
        self.status_lang.pack(side="right", padx=10)
        self.status_pos = tk.Label(status, text="", bg=THEME["status"], fg="#ffffff",
                                   font=("Segoe UI", 9))
        self.status_pos.pack(side="right", padx=10)

        # Panes: [explorer | [editor / terminal]]
        self.hpane = tk.PanedWindow(self, orient="horizontal", bg=THEME["border"],
                                    sashwidth=3, bd=0, sashrelief="flat")
        self.hpane.pack(fill="both", expand=True)

        self.explorer = self._build_explorer(self.hpane)
        self.right = tk.PanedWindow(self.hpane, orient="vertical", bg=THEME["border"],
                                    sashwidth=3, bd=0, sashrelief="flat")
        self.hpane.add(self.explorer, width=260, minsize=120)
        self.hpane.add(self.right, minsize=300)

        self.editor_area = ttk.Frame(self.right)
        self.notebook = ttk.Notebook(self.editor_area)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.notebook.bind("<Button-2>", self.on_tab_middle_click)
        self._build_find_bar()

        self.terminal = self._build_terminal(self.right)
        self.right.add(self.editor_area, minsize=150, stretch="always")
        self.right.add(self.terminal, height=200, minsize=80)

    def _build_explorer(self, parent):
        frame = tk.Frame(parent, bg=THEME["side"])
        self.explorer_title = ttk.Label(frame, text="EXPLORER", style="Side.TLabel")
        self.explorer_title.pack(anchor="w", padx=12, pady=(10, 6))
        holder = tk.Frame(frame, bg=THEME["side"])
        holder.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(holder, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(holder, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewOpen>>", self.on_tree_open)
        self.tree.bind("<Double-1>", self.on_tree_double)
        self.tree.bind("<Button-3>", self.on_tree_menu)

        self.tree_menu = tk.Menu(self, tearoff=0, bg=THEME["side"], fg=THEME["fg"],
                                 activebackground=THEME["sel"], activeforeground="#ffffff")
        self.tree_menu.add_command(label="New File...", command=self.tree_new_file)
        self.tree_menu.add_command(label="New Folder...", command=self.tree_new_folder)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="Delete", command=self.tree_delete)
        self.tree_menu.add_command(label="Refresh", command=self.refresh_tree)
        self._menu_node = ""
        return frame

    def _build_find_bar(self):
        self.find_bar = tk.Frame(self.editor_area, bg=THEME["side"])
        self.find_var = tk.StringVar()
        self.repl_var = tk.StringVar()
        entry_opts = dict(bg=THEME["input"], fg=THEME["fg"], insertbackground=THEME["fg"],
                          relief="flat", highlightthickness=1,
                          highlightbackground=THEME["border"], highlightcolor=THEME["status"])
        self.find_entry = tk.Entry(self.find_bar, textvariable=self.find_var, width=26, **entry_opts)
        self.find_entry.pack(side="left", padx=(8, 4), pady=6)
        ttk.Button(self.find_bar, text="Prev", style="Tool.TButton",
                   command=lambda: self.find(True)).pack(side="left", padx=2)
        ttk.Button(self.find_bar, text="Next", style="Tool.TButton",
                   command=self.find).pack(side="left", padx=2)
        self.repl_entry = tk.Entry(self.find_bar, textvariable=self.repl_var, width=22, **entry_opts)
        self.repl_entry.pack(side="left", padx=(12, 4))
        ttk.Button(self.find_bar, text="Replace", style="Tool.TButton",
                   command=self.replace_one).pack(side="left", padx=2)
        ttk.Button(self.find_bar, text="Replace All", style="Tool.TButton",
                   command=self.replace_all).pack(side="left", padx=2)
        ttk.Button(self.find_bar, text="Close", style="Tool.TButton",
                   command=self.hide_find).pack(side="right", padx=8)
        self.find_entry.bind("<Return>", lambda e: self.find())
        self.find_entry.bind("<Shift-Return>", lambda e: self.find(True))
        self.find_entry.bind("<Escape>", lambda e: self.hide_find())
        self.repl_entry.bind("<Return>", lambda e: self.replace_one())
        self.repl_entry.bind("<Escape>", lambda e: self.hide_find())

    def _build_terminal(self, parent):
        frame = tk.Frame(parent, bg=THEME["bg"])
        header = tk.Frame(frame, bg=THEME["side"])
        header.pack(fill="x")
        ttk.Label(header, text="TERMINAL", style="Side.TLabel").pack(side="left", padx=12, pady=4)
        ttk.Button(header, text="Clear", style="Tool.TButton",
                   command=self.clear_output).pack(side="right", padx=4, pady=2)
        ttk.Button(header, text="Stop", style="Tool.TButton",
                   command=self.stop_process).pack(side="right", padx=2, pady=2)

        body = tk.Frame(frame, bg=THEME["bg"])
        body.pack(fill="both", expand=True)
        self.out = tk.Text(body, height=8, bg=THEME["bg"], fg=THEME["fg"], font=self.font,
                           relief="flat", borderwidth=0, highlightthickness=0, wrap="word",
                           state="disabled", padx=8, pady=4, insertbackground=THEME["fg"])
        sb = ttk.Scrollbar(body, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.out.pack(side="left", fill="both", expand=True)
        self.out.tag_configure("cmd", foreground="#4ec9b0")

        row = tk.Frame(frame, bg=THEME["bg"])
        row.pack(fill="x")
        self.prompt = tk.Label(row, text=f"{self.cwd}>", bg=THEME["bg"], fg="#4ec9b0",
                               font=self.font)
        self.prompt.pack(side="left", padx=(8, 4))
        self.cmd_entry = tk.Entry(row, bg=THEME["bg"], fg=THEME["fg"], font=self.font,
                                  insertbackground=THEME["fg"], relief="flat",
                                  highlightthickness=0)
        self.cmd_entry.pack(side="left", fill="x", expand=True, pady=4)
        self.cmd_entry.bind("<Return>", self.on_cmd_return)
        self.cmd_entry.bind("<Up>", lambda e: self.history_step(-1))
        self.cmd_entry.bind("<Down>", lambda e: self.history_step(1))
        return frame

    # ------------------------------------------------------ tabs/files
    def editors(self):
        return [self.nametowidget(t) for t in self.notebook.tabs()]

    def current(self):
        sel = self.notebook.select()
        return self.nametowidget(sel) if sel else None

    def add_editor(self, ed):
        self.notebook.add(ed, text=ed.display_name)
        self.notebook.select(ed)
        ed.text.focus_set()

    def new_file(self):
        self.untitled_count += 1
        ed = Editor(self)
        ed.untitled = self.untitled_count
        self.add_editor(ed)

    def open_file(self, path):
        path = os.path.abspath(path)
        for ed in self.editors():
            if ed.path and os.path.normcase(ed.path) == os.path.normcase(path):
                self.notebook.select(ed)
                return
        if os.path.getsize(path) > 20 * 1024 * 1024:
            messagebox.showwarning(APP_NAME, "This file is larger than 20 MB and was not opened.")
            return
        ed = Editor(self)
        try:
            ed.load(path)
        except OSError as exc:
            ed.destroy()
            messagebox.showerror(APP_NAME, f"Could not open file:\n{exc}")
            return
        # replace an untouched, empty "Untitled" tab
        tabs = self.editors()
        if len(tabs) == 1 and not tabs[0].path and not tabs[0].modified \
                and not tabs[0].text.get("1.0", "end-1c"):
            self.notebook.forget(tabs[0])
            tabs[0].destroy()
        self.add_editor(ed)

    def open_file_dialog(self):
        for path in filedialog.askopenfilenames(initialdir=self.workspace) or ():
            self.open_file(path)

    def open_folder_dialog(self):
        path = filedialog.askdirectory(initialdir=self.workspace)
        if path:
            self.workspace = os.path.abspath(path)
            self.cwd = self.workspace
            self.prompt.config(text=f"{self.cwd}>")
            self.refresh_tree()

    def save_editor(self, ed, force_dialog=False):
        path = ed.path
        if force_dialog or not path:
            path = filedialog.asksaveasfilename(
                initialdir=self.workspace, initialfile=ed.display_name if ed.path else "")
            if not path:
                return False
            path = os.path.abspath(path)
        ok = ed.save(path if path != ed.path else None)
        if ok:
            self.update_tab_title(ed)
            self.on_tab_changed()
            self.flash(f"Saved {os.path.basename(path)}")
            if os.path.dirname(path).startswith(self.workspace):
                self.refresh_tree()
        return ok

    def save_current(self):
        ed = self.current()
        if ed:
            self.save_editor(ed)

    def save_as(self):
        ed = self.current()
        if ed:
            self.save_editor(ed, force_dialog=True)

    def close_editor(self, ed):
        if ed.modified:
            self.notebook.select(ed)
            ans = messagebox.askyesnocancel(APP_NAME, f"Save changes to {ed.display_name}?")
            if ans is None:
                return False
            if ans and not self.save_editor(ed):
                return False
        self.notebook.forget(ed)
        ed.destroy()
        if not self.notebook.tabs():
            self.update_status()
            self.title(APP_NAME)
        return True

    def close_current(self):
        ed = self.current()
        if ed:
            self.close_editor(ed)

    def on_tab_middle_click(self, event):
        try:
            idx = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self.close_editor(self.editors()[idx])

    def update_tab_title(self, ed):
        try:
            self.notebook.tab(ed, text=ed.display_name + ("  \u25cf" if ed.modified else ""))
        except tk.TclError:
            pass
        if ed is self.current():
            self.title(f"{ed.display_name}{' *' if ed.modified else ''} - {APP_NAME}")

    def on_tab_changed(self, event=None):
        ed = self.current()
        if ed:
            self.title(f"{ed.display_name}{' *' if ed.modified else ''} - {APP_NAME}")
            ed.redraw_numbers()
            ed.text.focus_set()
        self.update_status()

    def on_close(self):
        for ed in self.editors():
            if ed.modified:
                self.notebook.select(ed)
                ans = messagebox.askyesnocancel(APP_NAME, f"Save changes to {ed.display_name}?")
                if ans is None:
                    return
                if ans and not self.save_editor(ed):
                    return
        self.stop_process()
        self.destroy()

    # --------------------------------------------------------- status
    def update_status(self):
        ed = self.current()
        if ed:
            line, col = ed.text.index("insert").split(".")
            self.status_pos.config(text=f"Ln {line}, Col {int(col) + 1}")
            self.status_lang.config(text=f"{ed.language}    {ed.encoding.upper()}")
        else:
            self.status_pos.config(text="")
            self.status_lang.config(text="")

    def flash(self, message, ms=3000):
        self.status_msg.config(text=message)
        if self._status_job:
            self.after_cancel(self._status_job)
        self._status_job = self.after(ms, lambda: self.status_msg.config(text="Ready"))

    def about(self):
        messagebox.showinfo(APP_NAME, f"{APP_NAME} {VERSION}\n\nA lightweight, VS Code-inspired "
                            "code editor built with Python and Tkinter.\nLicensed under MIT.")

    # ----------------------------------------------------- edit actions
    def edit_cmd(self, which):
        ed = self.current()
        if not ed:
            return
        try:
            ed.text.edit_undo() if which == "undo" else ed.text.edit_redo()
        except tk.TclError:
            pass

    def clip(self, event_name):
        widget = self.focus_get()
        if widget:
            widget.event_generate(event_name)

    def toggle_comment(self):
        ed = self.current()
        if ed:
            ed.toggle_comment()

    def goto_line(self):
        ed = self.current()
        if not ed:
            return
        total = int(ed.text.index("end-1c").split(".")[0])
        n = simpledialog.askinteger("Go to Line", f"Line number (1-{total}):", parent=self,
                                    minvalue=1, maxvalue=total)
        if n:
            ed.text.mark_set("insert", f"{n}.0")
            ed.text.see("insert")
            ed.text.focus_set()
            self.update_status()

    def zoom(self, delta):
        size = max(7, min(40, int(self.font.cget("size")) + delta))
        self.font.configure(size=size)
        for ed in self.editors():
            ed.apply_tabs()
            ed.redraw_numbers()

    # -------------------------------------------------------- find bar
    def show_find(self, replace=False):
        ed = self.current()
        if not self.find_bar.winfo_ismapped():
            self.find_bar.pack(side="top", fill="x", before=self.notebook)
        if ed and ed.text.tag_ranges("sel"):
            sel = ed.text.get("sel.first", "sel.last")
            if "\n" not in sel:
                self.find_var.set(sel)
        target = self.repl_entry if replace else self.find_entry
        target.focus_set()
        self.find_entry.select_range(0, "end")

    def hide_find(self):
        self.find_bar.pack_forget()
        ed = self.current()
        if ed:
            ed.text.focus_set()

    def find(self, backwards=False):
        ed = self.current()
        term = self.find_var.get()
        if not ed or not term:
            return False
        t = ed.text
        if backwards:
            try:
                start = t.index("sel.first")
            except tk.TclError:
                start = t.index("insert")
            pos = t.search(term, start, stopindex="1.0", backwards=True, nocase=True)
            if not pos:
                pos = t.search(term, "end-1c", stopindex="1.0", backwards=True, nocase=True)
        else:
            try:
                start = t.index("sel.last")
            except tk.TclError:
                start = t.index("insert")
            pos = t.search(term, start, stopindex="end", nocase=True)
            if not pos:
                pos = t.search(term, "1.0", stopindex="end", nocase=True)
        if not pos:
            self.flash("No results")
            return False
        end = f"{pos}+{len(term)}c"
        t.tag_remove("sel", "1.0", "end")
        t.tag_add("sel", pos, end)
        t.mark_set("insert", pos if backwards else end)
        t.see(pos)
        ed.redraw_numbers()
        self.update_status()
        return True

    def replace_one(self):
        ed = self.current()
        term = self.find_var.get()
        if not ed or not term:
            return
        t = ed.text
        try:
            selected = t.get("sel.first", "sel.last")
        except tk.TclError:
            selected = ""
        if selected.lower() == term.lower():
            start = t.index("sel.first")
            t.delete("sel.first", "sel.last")
            t.insert(start, self.repl_var.get())
            t.edit_separator()
        self.find()

    def replace_all(self):
        ed = self.current()
        term = self.find_var.get()
        if not ed or not term:
            return
        t = ed.text
        content = t.get("1.0", "end-1c")
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        repl = self.repl_var.get()
        new, count = pattern.subn(lambda m: repl, content)
        if count:
            t.edit_separator()
            t.delete("1.0", "end-1c")
            t.insert("1.0", new)
            t.edit_separator()
        self.flash(f"Replaced {count} occurrence(s)")

    # ----------------------------------------------------- quick open
    def quick_open(self):
        files = []
        for root, dirs, names in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for name in names:
                files.append(os.path.relpath(os.path.join(root, name), self.workspace))
            if len(files) > 5000:
                break
        files.sort(key=str.lower)

        top = tk.Toplevel(self)
        top.title("Go to File")
        top.configure(bg=THEME["side"])
        top.transient(self)
        top.geometry(f"560x340+{self.winfo_rootx() + 340}+{self.winfo_rooty() + 80}")
        var = tk.StringVar()
        entry = tk.Entry(top, textvariable=var, bg=THEME["input"], fg=THEME["fg"],
                         insertbackground=THEME["fg"], relief="flat", font=self.font)
        entry.pack(fill="x", padx=8, pady=8, ipady=4)
        box = tk.Listbox(top, bg=THEME["side"], fg=THEME["fg"], selectbackground=THEME["sel"],
                         selectforeground="#ffffff", relief="flat", highlightthickness=0,
                         activestyle="none", font=self.font)
        box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        def refilter(*_):
            query = var.get().lower()
            box.delete(0, "end")
            shown = 0
            for f in files:
                if query in f.lower():
                    box.insert("end", f)
                    shown += 1
                    if shown >= 200:
                        break
            if box.size():
                box.selection_set(0)

        def choose(*_):
            sel = box.curselection()
            if sel:
                rel = box.get(sel[0])
                top.destroy()
                self.open_file(os.path.join(self.workspace, rel))

        def move(delta):
            sel = box.curselection()
            if not box.size():
                return "break"
            idx = max(0, min(box.size() - 1, (sel[0] if sel else 0) + delta))
            box.selection_clear(0, "end")
            box.selection_set(idx)
            box.see(idx)
            return "break"

        var.trace_add("write", refilter)
        entry.bind("<Return>", choose)
        entry.bind("<Down>", lambda e: move(1))
        entry.bind("<Up>", lambda e: move(-1))
        entry.bind("<Escape>", lambda e: top.destroy())
        box.bind("<Double-1>", choose)
        refilter()
        entry.focus_set()

    # --------------------------------------------------------- explorer
    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.node_paths.clear()
        self.dummies.clear()
        name = os.path.basename(self.workspace.rstrip("\\/")) or self.workspace
        root = self.tree.insert("", "end", text=name.upper())
        self.node_paths[root] = self.workspace
        self.populate(root)
        self.tree.item(root, open=True)

    def populate(self, node):
        path = self.node_paths[node]
        for child in self.tree.get_children(node):
            self.node_paths.pop(child, None)
            self.dummies.discard(child)
        self.tree.delete(*self.tree.get_children(node))
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        entries.sort(key=lambda e: (not self._is_dir(e), e.name.lower()))
        for entry in entries:
            if entry.name in IGNORE_DIRS:
                continue
            iid = self.tree.insert(node, "end", text=entry.name)
            self.node_paths[iid] = entry.path
            if self._is_dir(entry):
                self.dummies.add(self.tree.insert(iid, "end", text="..."))

    @staticmethod
    def _is_dir(entry):
        try:
            return entry.is_dir()
        except OSError:
            return False

    def on_tree_open(self, event):
        node = self.tree.focus()
        kids = self.tree.get_children(node)
        if len(kids) == 1 and kids[0] in self.dummies:
            self.populate(node)

    def on_tree_double(self, event):
        node = self.tree.identify_row(event.y)
        path = self.node_paths.get(node)
        if path and os.path.isfile(path):
            self.open_file(path)

    def on_tree_menu(self, event):
        node = self.tree.identify_row(event.y)
        if node:
            self.tree.selection_set(node)
            self.tree.focus(node)
        self._menu_node = node
        self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _target_dir(self):
        """Return (node, directory) where new items should be created."""
        node = self._menu_node
        if not node:
            roots = self.tree.get_children("")
            node = roots[0] if roots else ""
        path = self.node_paths.get(node, self.workspace)
        if os.path.isfile(path):
            node = self.tree.parent(node)
            path = os.path.dirname(path)
        return node, path

    def tree_new_file(self):
        node, directory = self._target_dir()
        name = simpledialog.askstring("New File", "File name:", parent=self)
        if not name:
            return
        path = os.path.join(directory, name)
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).touch(exist_ok=False)
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.populate(node)
        self.tree.item(node, open=True)
        self.open_file(path)

    def tree_new_folder(self):
        node, directory = self._target_dir()
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name:
            return
        try:
            os.makedirs(os.path.join(directory, name), exist_ok=False)
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.populate(node)
        self.tree.item(node, open=True)

    def tree_delete(self):
        node = self._menu_node
        path = self.node_paths.get(node)
        if not node or not path or path == self.workspace:
            return
        if not messagebox.askyesno(APP_NAME, f"Delete '{os.path.basename(path)}'?\n"
                                   "This cannot be undone."):
            return
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        parent = self.tree.parent(node)
        self.populate(parent)

    def toggle_explorer(self):
        if self.explorer_visible:
            self.hpane.forget(self.explorer)
        else:
            self.hpane.add(self.explorer, before=self.right, width=260, minsize=120)
        self.explorer_visible = not self.explorer_visible

    # --------------------------------------------------------- terminal
    def toggle_terminal(self):
        if self.terminal_visible:
            self.right.forget(self.terminal)
        else:
            self.right.add(self.terminal, height=200, minsize=80)
            self.cmd_entry.focus_set()
        self.terminal_visible = not self.terminal_visible

    def show_terminal(self):
        if not self.terminal_visible:
            self.toggle_terminal()

    def print_out(self, text, tag=None):
        self.out.configure(state="normal")
        self.out.insert("end", text, tag) if tag else self.out.insert("end", text)
        self.out.see("end")
        self.out.configure(state="disabled")

    def clear_output(self):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")

    def poll_output(self):
        try:
            while True:
                self.print_out(self.out_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(50, self.poll_output)

    def on_cmd_return(self, event):
        cmd = self.cmd_entry.get().strip()
        self.cmd_entry.delete(0, "end")
        if cmd:
            self.history.append(cmd)
            self.hist_pos = len(self.history)
            self.run_command(cmd)

    def history_step(self, delta):
        if not self.history:
            return "break"
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + delta))
        self.cmd_entry.delete(0, "end")
        if self.hist_pos < len(self.history):
            self.cmd_entry.insert(0, self.history[self.hist_pos])
        return "break"

    def run_command(self, cmd, cwd=None):
        cwd = cwd or self.cwd
        self.print_out(f"{cwd}> {cmd}\n", "cmd")
        if cmd.lower() in ("clear", "cls"):
            self.clear_output()
            return
        if re.match(r"cd(\s|$)", cmd, re.IGNORECASE):
            target = re.sub(r"^cd\s*(/d\s+)?", "", cmd, flags=re.IGNORECASE).strip().strip('"')
            new = os.path.abspath(os.path.join(self.cwd, os.path.expanduser(target or "~")))
            if os.path.isdir(new):
                self.cwd = new
                self.prompt.config(text=f"{self.cwd}>")
            else:
                self.print_out(f"The directory does not exist: {new}\n")
            return
        if self.proc and self.proc.poll() is None:
            self.print_out("A process is already running. Press Stop to end it.\n")
            return

        env = dict(os.environ, PYTHONUNBUFFERED="1")

        def worker():
            try:
                proc = subprocess.Popen(
                    cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                    errors="replace", creationflags=NO_WINDOW,
                )
                self.proc = proc
                for line in proc.stdout:
                    self.out_queue.put(line)
                proc.wait()
                self.out_queue.put(f"[process exited with code {proc.returncode}]\n")
            except Exception as exc:  # noqa: BLE001 - report anything to the user
                self.out_queue.put(f"Error: {exc}\n")

        threading.Thread(target=worker, daemon=True).start()

    def stop_process(self):
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   creationflags=NO_WINDOW, capture_output=True)
                else:
                    proc.terminate()
            except OSError:
                pass

    def run_current(self):
        ed = self.current()
        if not ed:
            return
        if not ed.path and not self.save_editor(ed):
            return
        if ed.modified and not self.save_editor(ed):
            return
        ext = os.path.splitext(ed.path)[1].lower()
        if ext in (".html", ".htm"):
            webbrowser.open(Path(ed.path).as_uri())
            return
        template = RUNNERS.get(ext)
        if not template:
            self.flash(f"No runner configured for '{ext or 'this file type'}'")
            return
        self.show_terminal()
        self.run_command(template.format(f=ed.path), cwd=os.path.dirname(ed.path))


def main():
    if os.name == "nt":
        try:  # crisp text on high-DPI displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    App(sys.argv[1:]).mainloop()


if __name__ == "__main__":
    main()
