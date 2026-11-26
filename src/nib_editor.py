#!/usr/bin/env python3
"""Nib — a small UTF-8 terminal editor. Usage: nib [file]"""
import argparse
import curses as c
import nib_update
from zipfile import ZipFile, BadZipFile
import keyword
import os
from pathlib import Path
import re
import sys
import subprocess
import stat
import tempfile
import unicodedata

GROUPS = {
    "py pyw": ("#", keyword.kwlist + ["match", "case", "self"]),
    "sh bash zsh": ("#", "if then else elif fi for while do done case esac function in export local".split()),
    "c h cpp hpp cc js jsx ts tsx java rs go cs swift": ("//", """
        if else for while return break continue switch case default try catch throw
        class struct enum interface extends new this static public private protected
        import export from package namespace using const let var function async await
        void int float double char bool string true false null undefined fn mut impl
        trait pub use mod match self func defer range type nil guard def protocol
        """.split()),
    "json": ("", ["true", "false", "null"]),
    "yaml yml toml": ("#", ["true", "false", "null"]),
    "sql": ("--", "select from where join on as insert into values update set delete create table order by group and or null".split()),
}
STRING = r"""(?:"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?)"""


def syntax(path):
    ext = path.suffix.lower()[1:]
    for extensions, (comment, words) in GROUPS.items():
        if ext in extensions.split():
            parts = [("string", STRING), ("number", r"\b(?:0[xX][\da-fA-F]+|\d+(?:\.\d+)?)\b"),
                     ("word", r"\b(?:" + "|".join(words) + r")\b")]
            if comment:
                parts.append(("comment", re.escape(comment) + ".*"))
            return re.compile("|".join(f"(?P<{name}>{rule})" for name, rule in parts),
                              re.I if ext == "sql" else 0)
    return None


def stamp(path):
    try:
        s = path.stat()
        return s.st_ino, s.st_size, s.st_mtime_ns
    except FileNotFoundError:
        return None


def save(path, lines, newline, original):
    if stamp(path) != original:
        raise OSError("File changed on disk. Save cancelled.")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
    try:
        if original and not os.access(path, os.W_OK):
            raise PermissionError("File is read-only")
        tmp.write_bytes(newline.join(lines).encode("utf-8"))
        if original:
            tmp.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return stamp(path)


def glyph(ch, col):
    if ch == "\t":
        n = 4 - col % 4
        return " " * n, n
    if unicodedata.combining(ch):
        return ch, 0
    if not ch.isprintable():
        return "?", 1
    return ch, 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def width(line):
    col = 0
    for ch in line:
        col += glyph(ch, col)[1]
    return col


def put(win, y, x, s, attr=0):
    try:
        win.addstr(y, x, s, attr)
    except c.error:
        pass


ENTER = ("\r", "\n", c.KEY_ENTER)
BACK = (c.KEY_BACKSPACE, "\x7f", "\b")
PAIRS = {"(": ")", "[": "]", "{": "}"}


def prompt(win, label):
    value = ""
    while True:
        h, w = win.getmaxyx()
        text = (label + "  " + value)[-max(1, w-1):]
        put(win, h-1, 0, text.ljust(w), c.A_REVERSE)
        win.move(h-1, min(width(text), w-1))
        key = win.get_wch()
        if key in ENTER:
            return value
        if key == "\x1b":
            return None
        if key in BACK:
            value = value[:-1]
        elif isinstance(key, str) and key.isprintable():
            value += key


def menu(win, title, choices, searchable=True, panel=False):
    selected = 0
    query = ""
    while True:
        h, w = win.getmaxyx()
        win.erase()
        put(win, 0, 0, (" Nib  /  " + title)[:w].ljust(w), c.A_REVERSE)
        x = max(1, (w-44)//2) if panel else 1
        y = max(3, (h-len(choices))//2) if panel else 3
        card = max(1, min(44, w-2*x))
        if panel:
            put(win, y-2, x, "nib", c.A_BOLD)
            put(win, y-1, x, "─" * card, accent)
        matches = [item for item in choices if query.casefold() in item[0].casefold()]
        selected = max(0, min(selected, len(matches)-1))
        rows = max(1, h-5)
        start = max(0, selected-rows+1)
        if searchable:
            put(win, 1, 1, ("Filter  " + query)[:max(0,w-2)])
        for idx, (label, _) in enumerate(matches[start:start+rows], start):
            active = idx == selected
            text = ("› " if active else "  ") + label
            put(win, y+idx-start, x, text[:card].ljust(card) if panel else text[:max(0,w-2)],
                accent if active else 0)
        if not matches:
            put(win, 3, 1, "No matches")
        put(win, h-1, 0, " Up/Down  Enter select  Esc back"[:w].ljust(w), c.A_DIM)
        key = win.get_wch()
        if panel and key == c.KEY_MOUSE:
            try:
                _, mx, my, _, state = c.getmouse()
                if x <= mx < x+card and y <= my < y+len(matches) and state & (c.BUTTON1_CLICKED | c.BUTTON1_PRESSED):
                    return matches[my-y][1]
            except c.error:
                pass
        if panel and isinstance(key, str) and key.isalpha():
            for label, value in choices:
                if label[0].lower() == key.lower():
                    return value
        if key in ENTER and matches:
            return matches[selected][1]
        if key in ("\x1b", "\x11"):
            return None
        if key == c.KEY_UP:
            selected -= 1
        elif key == c.KEY_DOWN:
            selected += 1
        elif searchable and key in BACK:
            query = query[:-1]
            selected = 0
        elif searchable and isinstance(key, str) and key.isprintable():
            query += key
            selected = 0


def save_location(win, folder):
    message = ""
    while True:
        entries = [("Choose this folder", "choose"), ("../  Parent folder", folder.parent)]
        if folder != Path.home():
            entries.append(("~/   Home folder", Path.home()))
        try:
            children = sorted((p for p in folder.iterdir() if p.is_dir()),
                              key=lambda p: p.name.casefold())
            entries.extend((p.name + "/", p) for p in children)
        except OSError as e:
            message = e.strerror
        label = f"Save location  {folder}"
        selected = menu(win, f"{message}  |  {label}" if message else label, entries, False)
        message = ""
        if selected is None:
            return None
        if selected != "choose":
            folder = selected
            continue
        name = prompt(win, "Filename")
        if not name:
            continue
        if name in (".", "..") or Path(name).name != name:
            message = "Enter a filename only"
            continue
        target = folder / name
        if os.path.lexists(target):
            message = "Name already exists. Choose another."
            continue
        return target


ACTIONS = [("Save file", "\x13"), ("Save as…", "saveas"), ("Find and replace", "\x12"), ("Find text", "\x06"), ("Go to line", "goto"),
           ("Python function and class picker", c.KEY_F2), ("Toggle indent guides", "guides"),
           ("Toggle line numbers", "numbers"), ("Undo", "\x1a"), ("Redo", "\x19"),
           ("Quit editor", "\x11")]


def read_event(win):
    key = win.get_wch()
    if key != "\x1b":
        return key
    win.timeout(60)
    prefix = ""
    try:
        while "[200~".startswith(prefix) and prefix != "[200~":
            try:
                ch = win.get_wch()
            except c.error:
                break
            if not isinstance(ch, str):
                c.ungetch(ch)
                break
            prefix += ch
    finally:
        win.timeout(-1)
    if prefix != "[200~":
        for ch in reversed(prefix):
            c.unget_wch(ch)
        return key
    chars = []
    win.keypad(False)
    while True:
        ch = win.get_wch()
        if isinstance(ch, str):
            chars.append(ch)
            if ch == "~" and chars[-6:] == list("\x1b[201~"):
                break
    win.keypad(True)
    return ("paste", "".join(chars[:-6]).replace("\r\n", "\n").replace("\r", "\n"))


def editor(win, path, lines, newline, original, unnamed=False):
    c.raw()
    win.keypad(True)
    colours = {}
    if c.has_colors():
        c.start_color()
        try:
            c.use_default_colors()
            bg = -1
        except c.error:
            bg = c.COLOR_BLACK
        for i, (name, fg) in enumerate(zip(("word", "string", "number", "comment"),
                                           (c.COLOR_CYAN, c.COLOR_GREEN, c.COLOR_MAGENTA, c.COLOR_YELLOW)), 1):
            c.init_pair(i, fg, bg)
            colours[name] = c.color_pair(i)
    rule = syntax(path)
    row = col = top = left = 0
    undo, redo = [], []
    version = saved = serial = 0
    notice = ""
    confirm = False
    guides = numbers = True

    def edit(start, count, replacement, new_cursor):
        nonlocal row, col, version, serial
        undo.append((start, lines[start:start + count], len(replacement), row, col, version))
        lines[start:start + count] = replacement
        row, col = new_cursor
        serial += 1
        version = serial
        redo.clear()

    while True:
        h, w = win.getmaxyx()
        if h < 4 or w < 12:
            win.erase()
            put(win, 0, 0, "Enlarge terminal"[:w])
            if win.get_wch() == "\x11" and version == saved:
                return
            continue
        height = h - 2
        top = min(row, max(top, row - height + 1))
        gutter = len(str(len(lines))) + 1 if numbers else 0
        area = max(1, w - gutter)
        visual = width(lines[row][:col])
        left = min(visual, max(left, visual - area + 1))
        win.erase()
        title = f" Nib  {path.name}{' *' if version != saved else ''}   {row+1}:{col+1}"
        put(win, 0, 0, title[:w].ljust(w), accent)
        for index in range(top, min(len(lines), top + height)):
            y = index - top + 1
            line = lines[index]
            if numbers:
                put(win, y, 0, str(index + 1).rjust(gutter - 1), c.A_DIM)
            tokens = iter(rule.finditer(line)) if rule else iter(())
            token = next(tokens, None)
            indent_end = len(line) - len(line.lstrip(" \t"))
            x = 0
            for pos, ch in enumerate(line):
                if token and pos >= token.end():
                    token = next(tokens, None)
                attr = colours.get(token.lastgroup, 0) if token and pos >= token.start() else 0
                char, cells = glyph(ch, x)
                if guides and pos < indent_end and x % 4 == 0:
                    char = "│" + char[1:]
                    attr = c.A_DIM
                if left <= x and x + cells <= left + area:
                    put(win, y, gutter + x - left, char, attr)
                x += cells
                if x >= left + area:
                    break
        footer = notice or "^S Save  ^Q Quit  ^F Find  ^R Replace  ^P Commands  F2 Symbols"
        put(win, h - 1, 0, footer[:w].ljust(w), c.A_REVERSE)
        win.move(row - top + 1, gutter + visual - left)
        key = read_event(win)
        if isinstance(key, tuple):
            pasted = key[1].split("\n")
            end = len(pasted[-1]) + (col if len(pasted) == 1 else 0)
            pasted[0] = lines[row][:col] + pasted[0]
            pasted[-1] += lines[row][col:]
            edit(row, 1, pasted, (row+len(pasted)-1, end))
            confirm = False
            notice = ""
            continue
        notice = ""
        if key in (c.KEY_F1, "\x10"):
            key = menu(win, "Commands", ACTIONS)
        if key == "\x11":
            if saved == version or confirm:
                return
            confirm = True
            notice = "Unsaved edits. Ctrl-Q again to discard, Ctrl-S to save."
            continue
        confirm = False
        line = lines[row]
        if key == "guides":
            guides = not guides
        elif key == "numbers":
            numbers = not numbers
        elif key == "goto":
            value = prompt(win, "Line")
            if value and value.isascii() and value.isdecimal() and len(value) < 12:
                row = min(len(lines)-1, max(0, int(value)-1))
                col = 0
        elif key == c.KEY_F2:
            if path.suffix.lower() in (".py", ".pyw"):
                symbols = [(f"{m[1]} {m[2]}   ·   {i+1}", i) for i, text in enumerate(lines)
                           if (m := re.match(r"\s*(?:async\s+)?(class|def)\s+(\w+)", text))]
                dest = menu(win, "Python symbols", symbols)
                if dest is not None:
                    row, col = dest, 0
            else:
                notice = "Symbol picker supports Python files"
        elif key in ("\x13", "saveas"):
            try:
                destination = save_location(win, path.parent) if unnamed or key == "saveas" else path
                if destination is None:
                    notice = "Save cancelled"
                    continue
                previous = original if destination == path and not unnamed else None
                original = save(destination, lines, newline, previous)
                path = destination
                unnamed = False
                rule = syntax(path)
                saved = version
                notice = "Saved " + str(path)
            except OSError as e:
                notice = str(e)
        elif key in ("\x1a", "\x19"):
            src, dst = (undo, redo) if key == "\x1a" else (redo, undo)
            if src:
                start, old, count, r, k, v = src.pop()
                dst.append((start, lines[start:start + count], len(old), row, col, version))
                lines[start:start + count] = old
                row, col, version = r, k, v
        elif key == "\x12":
            needle = prompt(win, "Find text")
            if not needle:
                continue
            replacement = prompt(win, "Replace with")
            if replacement is None:
                continue
            count = 0
            updated = lines.copy()
            replace_all = False
            stopped = False
            for index, source in enumerate(lines):
                offset = 0
                chunks = []
                while (hit := source.find(needle, offset)) >= 0:
                    if not replace_all:
                        h, w = win.getmaxyx()
                        win.erase()
                        put(win, 0, 0, f" Match on line {index+1}, column {hit+1}", accent)
                        put(win, 2, 0, source[max(0,hit-16):hit][-16:], c.A_DIM)
                        marker = width(source[max(0,hit-16):hit])
                        put(win, 2, marker, needle[:max(1,w-marker)], c.A_REVERSE)
                        put(win, 2, marker+width(needle), source[hit+len(needle):][:w])
                        put(win, h-1, 0, " R Replace   S Skip   A Replace all   Esc Finish"[:w], accent)
                        choice = win.get_wch()
                        if choice == "\x1b":
                            stopped = True
                            break
                        if choice not in ("r", "R", "s", "S", "a", "A"):
                            continue
                        replace_all = choice.lower() == "a"
                    apply = replace_all or choice.lower() == "r"
                    chunks.extend((source[offset:hit], replacement if apply else needle))
                    count += int(apply)
                    offset = hit + len(needle)
                updated[index] = "".join(chunks) + source[offset:]
                if stopped:
                    break
            if count and updated != lines:
                edit(0, len(lines), updated, (row, min(col,len(updated[row]))))
            notice = f"Replaced {count} matches"
        elif key == "\x06":
            query = prompt(win, "Find")
            if query:
                for step in range(len(lines) + 1):
                    r = (row + step) % len(lines)
                    found = lines[r].find(query, col + 1 if step == 0 else 0)
                    if found >= 0:
                        row, col = r, found
                        break
                else:
                    notice = "No match"
        elif key in (c.KEY_UP, c.KEY_DOWN, c.KEY_PPAGE, c.KEY_NPAGE):
            delta = {c.KEY_UP: -1, c.KEY_DOWN: 1, c.KEY_PPAGE: -height, c.KEY_NPAGE: height}[key]
            row = max(0, min(len(lines) - 1, row + delta))
            col = min(col, len(lines[row]))
        elif key == c.KEY_LEFT:
            if col:
                col -= 1
            elif row:
                row -= 1
                col = len(lines[row])
        elif key == c.KEY_RIGHT:
            if col < len(line):
                col += 1
            elif row + 1 < len(lines):
                row, col = row + 1, 0
        elif key in (c.KEY_HOME, "\x01"):
            col = 0
        elif key in (c.KEY_END, "\x05"):
            col = len(line)
        elif key in ("\r", "\n", c.KEY_ENTER):
            prefix = line[:col]
            indent = prefix[:len(prefix)-len(prefix.lstrip(" \t"))] if rule else ""
            if rule and prefix.rstrip().endswith((":", "{")):
                indent += "    "
            edit(row, 1, [prefix, indent + line[col:]], (row + 1, len(indent)))
        elif key in (c.KEY_BACKSPACE, "\x7f", "\b"):
            if col:
                end = col + int(bool(rule and PAIRS.get(line[col-1]) == line[col:col+1]))
                edit(row, 1, [line[:col-1] + line[end:]], (row, col-1))
            elif row:
                edit(row-1, 2, [lines[row-1] + line], (row-1, len(lines[row-1])))
        elif key == c.KEY_DC:
            if col < len(line):
                edit(row, 1, [line[:col] + line[col+1:]], (row, col))
            elif row + 1 < len(lines):
                edit(row, 2, [line + lines[row+1]], (row, col))
        elif key == "\t" and path.suffix in (".py", ".pyw") and line.strip() == "main" and col == len(line):
            indent = line[:-4]
            body = indent + "    "
            edit(row, 1, [indent + 'if __name__ == "__main__":', body], (row+1, len(body)))
        elif rule and key in PAIRS and (not line[col:] or line[col].isspace() or line[col] in ")]}"):
            edit(row, 1, [line[:col] + key + PAIRS[key] + line[col:]], (row, col+1))
        elif rule and key in PAIRS.values() and line[col:col+1] == key:
            col += 1
        elif isinstance(key, str) and (key == "\t" or key.isprintable()):
            edit(row, 1, [line[:col] + key + line[col:]], (row, col + 1))


def open_file(win, path):
    path = path.expanduser().resolve()
    original = stamp(path)
    if original and not path.is_file():
        raise ValueError("Choose a regular text file")
    data = path.read_bytes().decode("utf-8") if original else ""
    if "\0" in data:
        raise ValueError("Binary file")
    edit_document(win, path, data, original)


def edit_document(win, path, data, original, unnamed=False):
    newline = "\r\n" if "\r\n" in data and "\n" not in data.replace("\r\n", "") else "\n"
    sys.stdout.write("\x1b[?2004h")
    sys.stdout.flush()
    try:
        editor(win, path, data.split(newline), newline, original, unnamed)
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()


accent = c.A_REVERSE


def session(win, path, release, target):
    global accent
    if c.has_colors() and c.COLOR_PAIRS > 5:
        c.init_pair(5, c.COLOR_WHITE, c.COLOR_BLUE)
        accent = c.color_pair(5) | c.A_BOLD
    c.mousemask(c.BUTTON1_CLICKED | c.BUTTON1_PRESSED)
    c.raw()
    win.keypad(True)
    if release:
        answer = menu(win, f"Nib {release[0]} is available. Update?", [("No", False), ("Yes", True)], False, True)
        if answer:
            try:
                nib_update.install(release, target)
                return True
            except (ValueError, OSError, SyntaxError, BadZipFile, KeyError, nib_update.http_error) as error:
                win.erase()
                put(win, 0, 0, f"Update failed: {error}")
                put(win, 2, 0, "Continuing with installed Nib")
                win.refresh()
                c.napms(900)
    if path is not None:
        open_file(win, path)
        return
    message = "Home"
    while True:
        action = menu(win, message, [("New file", "new"), ("Open file", "open"), ("Quit", None)], False, True)
        if action is None:
            return
        if action == "new":
            edit_document(win, Path.cwd() / "Untitled", "", None, unnamed=True)
            message = "Home"
            continue
        name = prompt(win, "Open path")
        if not name:
            continue
        target = Path(name).expanduser()
        if action == "open" and not target.is_file():
            message = "File not found. Check the path."
            continue
        try:
            open_file(win, target)
            message = "Home"
        except (ValueError, OSError) as e:
            message = str(e)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, nargs="?")
    target = Path(sys.argv[0]).resolve()
    with ZipFile(target) as bundle:
        version = bundle.read("VERSION").decode().strip()
    parser.add_argument("--version", action="version", version="Nib " + version)
    args = parser.parse_args()
    try:
        release = nib_update.latest(version)
        if c.wrapper(session, args.file, release, target):
            argv = [sys.executable, str(target), *sys.argv[1:]]
            if os.name == "nt":
                sys.exit(subprocess.call(argv))
            os.execv(sys.executable, argv)
    except (OSError, ValueError, c.error) as e:
        parser.exit(1, f"Nib: {e}\n")


if __name__ == "__main__":
    main()
