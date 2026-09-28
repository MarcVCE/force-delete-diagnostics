#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
force_delete.py
Deletes everything it can inside a folder and reports the exact cause
of every item that could not be removed.

Usage:
    python force_delete.py                      # empties the current directory
    python force_delete.py "D:\\path\\to\\folder" # empties the given folder
    python force_delete.py --dry-run            # simulation only, deletes nothing
    python force_delete.py --yes                # skip confirmation
    python force_delete.py --include-root       # also delete the folder itself

Optional (to find out WHICH process is locking a file):
    pip install psutil
"""

import argparse
import errno
import os
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import psutil  # optional
except ImportError:
    psutil = None

IS_WINDOWS = os.name == "nt"
RETRIES = 3
WAIT = 0.5  # seconds between retries (grows on each attempt)
LONG_PREFIX = "\\\\?\\"


# ---------------------------------------------------------------- helpers
def long_path(p: str) -> str:
    """On Windows, add the \\\\?\\ prefix to bypass the 260-character path limit."""
    if not IS_WINDOWS:
        return p
    p = os.path.abspath(p)
    if p.startswith(LONG_PREFIX):
        return p
    if p.startswith("\\\\"):  # UNC network path
        return LONG_PREFIX + "UNC\\" + p[2:]
    return LONG_PREFIX + p


def clean(p: str) -> str:
    """Remove the long-path prefix for display."""
    return p[len(LONG_PREFIX):] if p.startswith(LONG_PREFIX) else p


def clear_readonly(p: str) -> None:
    try:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except OSError:
        pass
    if IS_WINDOWS:
        try:
            import ctypes
            FILE_ATTRIBUTE_NORMAL = 0x80
            ctypes.windll.kernel32.SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL)
        except Exception:
            pass


def locking_processes(p: str) -> list:
    """Return processes that have the file/folder open (requires psutil)."""
    if psutil is None:
        return []
    target = os.path.normcase(os.path.abspath(clean(p)))
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for f in proc.open_files():
                path = os.path.normcase(f.path)
                if path == target or path.startswith(target + os.sep):
                    found.append(f"{proc.info['name']} (PID {proc.info['pid']})")
                    break
            else:
                cwd = proc.cwd()
                if cwd and os.path.normcase(cwd).startswith(target):
                    found.append(f"{proc.info['name']} (PID {proc.info['pid']}) [working directory]")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            continue
    return found


def diagnose(p: str, exc: Exception) -> str:
    """Translate the OS error into a human-readable cause."""
    winerr = getattr(exc, "winerror", None)
    en = getattr(exc, "errno", None)
    causes = []

    if winerr == 32 or en == errno.EBUSY:
        causes.append("File in use by another process")
    elif winerr == 5 or en in (errno.EACCES, errno.EPERM):
        causes.append("Access denied (permissions / owner / antivirus / file in use)")
    elif winerr == 145 or en == errno.ENOTEMPTY:
        causes.append("Folder not empty (something inside could not be deleted)")
    elif winerr == 1392:
        causes.append("File system corruption - run 'chkdsk X: /f' from another drive")
    elif winerr in (3, 206) or en == errno.ENAMETOOLONG:
        causes.append("Path too long or not found")
    elif en == errno.EROFS:
        causes.append("Read-only file system")
    elif winerr == 1920 or en == errno.ELOOP:
        causes.append("Broken or looping symlink/junction")
    else:
        causes.append(type(exc).__name__)

    lockers = locking_processes(p)
    if lockers:
        causes.append("Locked by: " + ", ".join(lockers))
    elif psutil is None and (winerr in (5, 32) or en in (errno.EACCES, errno.EBUSY)):
        causes.append("(install psutil to see which process is locking it)")

    return " | ".join(causes) + f" -> {exc}"


def is_junction(p: str) -> bool:
    try:
        return os.path.isjunction(p)  # Python 3.12+
    except AttributeError:
        try:
            return bool(os.lstat(p).st_file_attributes & 0x400)  # REPARSE_POINT
        except (OSError, AttributeError):
            return False


# ---------------------------------------------------------------- deletion
class Deleter:
    def __init__(self, dry_run: bool, excluded=()):
        self.dry_run = dry_run
        self.excluded = {os.path.normcase(os.path.abspath(e)) for e in excluded}
        self.files_deleted = 0
        self.dirs_deleted = 0
        self.errors = []  # (path, cause)
        self.total = 0
        self.done = 0

    def _progress(self, status: str, p: str) -> None:
        self.done += 1
        pct = f"{self.done * 100 / self.total:5.1f}%" if self.total else ""
        print(f"[{self.done}/{self.total} {pct}] {status} {clean(p)}", flush=True)

    def _try(self, func, p: str) -> bool:
        last = None
        for attempt in range(RETRIES):
            try:
                func(p)
                return True
            except FileNotFoundError:
                return True  # already gone
            except OSError as e:
                last = e
                clear_readonly(p)
                time.sleep(WAIT * (attempt + 1))
        self.errors.append((clean(p), diagnose(p, last)))
        return False

    def delete_file(self, p: str) -> None:
        if os.path.normcase(os.path.abspath(clean(p))) in self.excluded:
            print(f"[SKIPPED] {clean(p)} (this script)")
            return
        if self.dry_run:
            self._progress("WOULD DELETE FILE  ", p)
            self.files_deleted += 1
            return
        if self._try(os.remove, p):
            self.files_deleted += 1
            self._progress("DELETED FILE  ", p)
        else:
            self._progress("ERROR         ", p)

    def delete_dir(self, p: str) -> None:
        if self.dry_run:
            self._progress("WOULD DELETE FOLDER", p)
            self.dirs_deleted += 1
            return
        if self._try(os.rmdir, p):
            self.dirs_deleted += 1
            self._progress("DELETED FOLDER", p)
        else:
            self._progress("ERROR         ", p)

    def empty(self, root: str) -> None:
        """Walk bottom-up so contents are removed before their folders."""
        def on_error(e):
            name = getattr(e, "filename", "") or "?"
            self.errors.append((clean(name), diagnose(name, e)))

        for current, dirs, files in os.walk(root, topdown=False, onerror=on_error, followlinks=False):
            for name in files:
                self.delete_file(os.path.join(current, name))
            for name in dirs:
                p = os.path.join(current, name)
                # Symlinks / junctions: remove the link itself, never enter it
                if os.path.islink(p) or (IS_WINDOWS and is_junction(p)):
                    if self.dry_run:
                        self._progress("WOULD REMOVE LINK  ", p)
                        continue
                    try:
                        os.unlink(p)
                        self._progress("REMOVED LINK  ", p)
                    except OSError:
                        self.delete_dir(p)
                    continue
                self.delete_dir(p)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Delete everything inside a folder and report why anything failed.")
    ap.add_argument("folder", nargs="?", default=os.getcwd(),
                    help="Folder to empty (default: current directory)")
    ap.add_argument("--dry-run", action="store_true", help="Only show what would be deleted")
    ap.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    ap.add_argument("--include-root", action="store_true", help="Also delete the given folder itself")
    args = ap.parse_args()

    target = Path(args.folder).expanduser().resolve()
    if not target.exists():
        print(f"Does not exist: {target}")
        return 1
    if not target.is_dir():
        print(f"Not a folder: {target}")
        return 1

    # Guard against dangerous paths
    dangerous = {Path(target.anchor), Path.home().resolve()}
    if IS_WINDOWS:
        dangerous.add(Path(os.environ.get("WINDIR", "C:\\Windows")).resolve())
    if target in dangerous:
        print(f"Refusing to delete a critical path: {target}")
        return 1

    if not args.dry_run and not args.yes:
        print(f"EVERYTHING inside this folder will be permanently deleted (no Recycle Bin):\n  {target}")
        if input("Type DELETE to confirm: ").strip() != "DELETE":
            print("Cancelled.")
            return 0

    try:
        if Path.cwd().resolve().is_relative_to(target):
            os.chdir(target.parent)  # avoid locking the folder with our own process
    except AttributeError:  # Python < 3.9
        pass

    d = Deleter(args.dry_run, excluded=[os.path.abspath(__file__)])
    root = long_path(str(target))

    print("Counting items...", flush=True)
    d.total = sum(len(ds) + len(fs) for _, ds, fs in os.walk(root))
    print(f"Items to delete: {d.total}\n", flush=True)

    start = time.time()
    d.empty(root)
    if args.include_root:
        d.delete_dir(root)

    # ---------- report
    print("\n" + "=" * 70)
    print(f"Files deleted   : {d.files_deleted}")
    print(f"Folders deleted : {d.dirs_deleted}")
    print(f"Errors          : {len(d.errors)}")
    print(f"Time            : {time.time() - start:.1f} s")

    if d.errors:
        summary = {}
        for _, cause in d.errors:
            key = cause.split(" -> ")[0].split(" | ")[0]
            summary[key] = summary.get(key, 0) + 1
        print("\nCauses summary:")
        for cause, n in sorted(summary.items(), key=lambda x: -x[1]):
            print(f"  {n:>5}  {cause}")

        print("\nFirst 20 errors:")
        for path, cause in d.errors[:20]:
            print(f"  - {path}\n      {cause}")

        log = Path(__file__).with_name(f"delete_errors_{datetime.now():%Y%m%d_%H%M%S}.txt")
        with open(log, "w", encoding="utf-8") as f:
            for path, cause in d.errors:
                f.write(f"{path}\n    {cause}\n")
        print(f"\nFull report: {log}")
    print("=" * 70)
    return 0 if not d.errors else 2


if __name__ == "__main__":
    sys.exit(main())
