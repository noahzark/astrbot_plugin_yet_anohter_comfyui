"""Built-in workflow installation into the plugin data directory."""
from __future__ import annotations

import shutil
from pathlib import Path

BUILT_IN_DIR = Path(__file__).resolve().parent.parent / "build_in_wf"


def install_builtin_workflows(dest: Path, source: Path = BUILT_IN_DIR) -> list[str]:
    """Copy built-in workflow files that ``dest`` does not have yet.

    Existing files are never overwritten, so user edits survive upgrades.
    Returns the names of the files copied.
    """
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if not source.is_dir():
        return copied
    for file in sorted(source.iterdir()):
        if file.is_file() and file.suffix == ".json" and not (dest / file.name).exists():
            shutil.copy(file, dest / file.name)
            copied.append(file.name)
    return copied
