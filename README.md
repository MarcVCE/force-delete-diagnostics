# force-delete-diagnostics

A single-file Python script that **deletes everything it can** inside a folder and, for anything it *can't* delete, **tells you exactly why** — file in use (and by which process), access denied, path too long, file system corruption, and more.

Built for the classic Windows situation: *"I can't delete this folder and I have no idea why."*

## Features

- **Deletes as much as possible** instead of stopping at the first error.
- **Diagnoses every failure** with a human-readable cause (WinError / errno mapped to plain English).
- **Finds the locking process** (name + PID) when a file is in use — requires the optional `psutil`.
- **Long path support** on Windows (`\\?\` prefix), so paths over 260 characters are no longer a problem.
- **Clears read-only attributes** automatically and retries up to 3 times with backoff.
- **Safe with symlinks and junctions**: removes the link, never follows it into the target.
- **Live progress**: `[n/total pct%]` line for every deleted item.
- **Error report**: a summary grouped by cause plus a full `.txt` log.
- **Dry-run mode** to preview what would be deleted.
- **Safety guards**: requires typing `DELETE`, refuses to run on the drive root, your home folder or `C:\Windows`, and never deletes itself.
- Zero required dependencies — standard library only.

## Installation

```bash
git clone https://github.com/MarcVCE/force-delete-diagnostics.git
cd force-delete-diagnostics
pip install psutil   # optional, recommended
```

Requires Python 3.8+ (3.12+ recommended for native junction detection).

## Usage

```bash
# Preview what would be deleted in the current directory
python force_delete.py --dry-run

# Empty the current directory (asks for confirmation)
python force_delete.py

# Empty a specific folder
python force_delete.py "D:\path\to\folder"

# Also delete the folder itself, no confirmation
python force_delete.py "D:\path\to\folder" --include-root --yes
```

| Option | Description |
|---|---|
| `folder` | Folder to empty. Defaults to the current working directory. |
| `--dry-run` | Show what would be deleted without touching anything. |
| `--yes` | Skip the confirmation prompt. |
| `--include-root` | Delete the target folder itself after emptying it. |

Exit codes: `0` success, `1` invalid path / refused, `2` finished with errors.

### Example output

```
Counting items...
Items to delete: 3

[1/3  33.3%] DELETED FILE   D:\covers\img001.jpg
[2/3  66.7%] ERROR          D:\covers\Chapter 024
[3/3 100.0%] ERROR          D:\covers

======================================================================
Files deleted   : 1
Folders deleted : 0
Errors          : 2

Causes summary:
      1  File system corruption - run 'chkdsk X: /f' from another drive
      1  Folder not empty (something inside could not be deleted)
======================================================================
```

## Common causes and fixes

| Cause | What to do |
|---|---|
| File in use (WinError 32) | Close the process shown in the report, or reboot and run again. |
| Access denied (WinError 5) | Run as administrator. If it persists: antivirus, OneDrive sync, or another owner → `takeown /f "path" /r /d y` + `icacls "path" /grant %username%:F /t`. |
| File system corruption (WinError 1392) | Run `chkdsk X: /f` **from another drive** (e.g. from `C:`). Common on exFAT USB drives unplugged without ejecting. |
| Folder not empty (WinError 145) | A side effect — look at the errors of the files inside it. |
| Path too long (WinError 206) | Already handled via long-path prefix; if it still appears, the path is likely invalid. |

## Advantages vs. traditional methods

**Compared to Windows Explorer (Delete / Shift+Delete):**
- Much faster on large trees of small files: no Recycle Bin bookkeeping, no pre-scan dialog, no UI refresh or shell extension notifications per item.
- Doesn't abort the whole operation on the first problem file.
- Tells you *why* something failed and *which* process is holding it, instead of a generic "The action can't be completed" dialog.
- Handles paths longer than 260 characters.

**Compared to `rmdir /s /q`, `del /f /s /q` or `Remove-Item -Recurse -Force`:**
- Clear, grouped diagnosis instead of terse or silent errors.
- Automatic retries and read-only attribute removal.
- Live progress, dry-run mode and a persistent error log.
- Safety guards against catastrophic targets.

**Compared to `shutil.rmtree`:**
- Keeps going after errors instead of raising on the first one.
- Handles long paths, read-only files and junctions correctly on Windows.

## Disadvantages / limitations

- **Deletion is permanent** — nothing goes to the Recycle Bin. Always try `--dry-run` first.
- **It cannot fix what's broken**: file system corruption, missing ownership or kernel-level locks still require `chkdsk`, `takeown`/`icacls`, or a reboot. The script only tells you which one you need.
- **Slower than native commands** for huge trees with no errors, because it counts items first and prints progress for each one.
- **Locking-process detection is limited**: needs `psutil`, and without admin rights it can't inspect processes owned by other users or the system. It won't detect handles held by the kernel or antivirus drivers.
- **No secure erase**: data is removed from the file system index, not overwritten, so it may be recoverable with forensic tools.
- Primarily designed and tested for **Windows**; it works on Linux/macOS, but some diagnostics are Windows-specific.

## License

MIT
