# CodeForge

A lightweight, VS Code-inspired code editor written in Python (Tkinter). Dark theme, tabs, file explorer,
syntax highlighting for hundreds of languages, integrated terminal and one-key "Run".

## Download

Grab `CodeForge-windows-x64.zip` from the [Releases](../../releases) page, extract it and run `CodeForge.exe`.

> **Note:** the executable is not code-signed, so Windows SmartScreen may show a warning the first time.
> Click **More info → Run anyway**.

## Features

- Multi-tab editor with line numbers, auto-indent, Tab / Shift+Tab block indent and zoom
- Syntax highlighting powered by [Pygments](https://pygments.org/) (Python, JavaScript, C/C++, Java, HTML, CSS, JSON, Markdown, ...)
- File explorer with lazy loading, right-click menu (new file / new folder / delete)
- Quick open (`Ctrl+P`), find & replace, go to line, toggle line comment
- Integrated terminal with command history and a **Stop** button
- Run the current file with `F5` (Python, Node.js, Bash, Go, Ruby, PHP, Lua, Java, PowerShell, batch, HTML)
- Handles UTF-8, Windows-1252 and Latin-1 files

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+N` / `Ctrl+O` / `Ctrl+Shift+O` | New file / Open file / Open folder |
| `Ctrl+S` / `Ctrl+Shift+S` | Save / Save as |
| `Ctrl+W` or middle-click on a tab | Close tab |
| `Ctrl+P` | Go to file |
| `Ctrl+F` / `Ctrl+H` | Find / Replace |
| `Ctrl+G` | Go to line |
| `Ctrl+/` | Toggle line comment |
| `Ctrl+B` | Toggle explorer |
| ``Ctrl+` `` | Toggle terminal |
| `Ctrl+=` / `Ctrl+-` | Zoom in / out |
| `F5` | Run current file |

## Run from source

Requires Python 3.9+ (Tkinter ships with the official Windows/macOS installers; on Debian/Ubuntu install `python3-tk`).

```bash
pip install -r requirements.txt
python codeforge.py            # optionally: python codeforge.py path/to/folder_or_file
```

## Build the Windows executable

On Windows, run:

```bat
build.bat
```

This installs the dependencies, builds `dist\CodeForge.exe` with PyInstaller and creates
`CodeForge-windows-x64.zip` containing the executable, this README and the license.

### Build on GitHub (no local setup)

The workflow in `.github/workflows/build.yml` builds the executable on a Windows runner.

- **Manually:** *Actions → Build Windows executable → Run workflow*, then download the zip from the run's artifacts.
- **Release:** push a version tag and the zip is attached to a GitHub Release automatically:

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Troubleshooting `build.bat`

- **"Python was not found"** - install Python from python.org and tick *Add python.exe to PATH*
  (the Microsoft Store shortcut for `python` does not count).
- **`No module named tkinter` / the exe crashes on start** - reinstall Python with the *tcl/tk and IDLE* option enabled.
- Run the script from a normal Command Prompt (`cmd`) so you can read any error message.
- Don't want to build locally? Use the GitHub Actions workflow above.

## Notes

- "Run" uses the interpreters installed on your machine (e.g. `python` must be on your `PATH` to run `.py` files).
- The terminal runs one command at a time and does not accept interactive input (stdin is closed).

## License

[MIT](LICENSE)
