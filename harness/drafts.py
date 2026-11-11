"""Cleanup of old tower drafts in runtime/drafts/<tower>/ (researcher decisions 2026-11-09, DESIGN §29).

The drafts directory is scratch space. Records keep the published text of each record draft, the
message ledger keeps each message, and the documents for the researcher are in archive/reports/
(D27). So an old draft that no kept layer names is a copy, a one-time script, or a work directory,
and the harness removes it at each handover. The rule is mechanical, so the harness applies it, not
a tower.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from .records import KINDS, RecordError, split_front_matter

DEFAULTS: dict[str, Any] = {
    "prune_towers": ["dt", "et", "vt", "dt2"],
    "keep_days": 7,
    # A tower in this list keeps a draft only when worklog/ or state/ names it, not when only the
    # archive names it. VT work directories are source trees that `git archive <commit>` makes again,
    # and the archive logs name them in passing (researcher, 2026-11-09).
    "live_reference_towers": ["vt"],
}
KEEP_NAMES = {"handover.md"}
# DT rule 15 and DT2 rule D4: a file in these subdirectories is one unit
SUBDIRS_BY_TOWER = {"dt": ("records", "msg", "scripts"), "dt2": ("records", "scripts")}
SUBDIR_TOWERS = set(SUBDIRS_BY_TOWER)
CACHE_DIRS = {"__pycache__"}
VENV_MARKER = "pyvenv.cfg"  # a Python virtual environment is a tool, not a draft: it always stays


def settings(config: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(config.get("drafts") or {})}


def _referenced_text(root: Path, live_only: bool = False) -> str:
    """The text of the kept layers. A draft whose name occurs here stays."""
    scopes = (("worklog", "*.md"), ("state", "*.md"))
    if not live_only:
        scopes = (("archive", "**/*.md"),) + scopes
    parts = []
    for folder, pattern in scopes:
        for path in sorted((root / folder).glob(pattern)):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _named(name: str, text: str) -> bool:
    """True when `name` occurs in `text` as a whole name: "r2" does not match "r20" or "pr2"."""
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", text) is not None


def _normal(text: str) -> str:
    return " ".join(text.split())


def _unpublished_kind(path: Path, published: set[str]) -> str | None:
    """The record kind of a draft that no record has published, else None."""
    if path.suffix != ".md":
        return None
    try:
        meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
    except RecordError:
        return None
    kind = meta.get("kind")
    if kind not in KINDS or _normal(body) in published:
        return None
    return kind


def _published_bodies(root: Path) -> set[str]:
    bodies = set()
    for path in (root / "records").rglob("*.md"):
        try:
            bodies.add(_normal(split_front_matter(path.read_text(encoding="utf-8", errors="replace"))[1]))
        except RecordError:
            continue
    return bodies


def _newest(path: Path) -> float:
    newest = path.stat().st_mtime
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            newest = max(newest, item.stat().st_mtime)
    return newest


def _units(base: Path, tower: str) -> tuple[list[Path], list[Path]]:
    """(units to judge, cache directories). A unit is a top-level file, a top-level directory, or a
    file in a DT subdirectory (records/, msg/, scripts/). Hidden entries and virtual environments
    are not units."""
    units, caches = [], []
    for entry in sorted(base.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir() and entry.name in CACHE_DIRS:
            caches.append(entry)
        elif entry.is_dir() and entry.name in SUBDIRS_BY_TOWER.get(tower, ()):
            for item in sorted(entry.rglob("*")):
                relative = item.relative_to(entry)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                if item.is_dir() and item.name in CACHE_DIRS:
                    if not any(part in CACHE_DIRS for part in relative.parts[:-1]):
                        caches.append(item)
                elif item.is_file() and not any(part in CACHE_DIRS for part in relative.parts):
                    units.append(item)
        elif entry.is_dir() and (entry / VENV_MARKER).exists():
            continue
        else:
            units.append(entry)
    return units, caches


def prune(root: Path, tower: str, keep_days: float, dry_run: bool = False, keep: tuple[Path, ...] = (),
          now: float | None = None, live_only: bool = False) -> dict[str, list[Path]]:
    """Remove the old drafts of a tower. Returns {"deleted": [...], "archived": [...]} with the paths.

    A unit stays when: its newest file is younger than keep_days (by mtime), its name is in
    KEEP_NAMES, it is in `keep`, or its name occurs as a whole name in archive/, worklog/, or state/
    (with live_only: in worklog/ or state/ only). A virtual environment (a directory with pyvenv.cfg)
    and a hidden entry always stay. An old record draft (JSON front matter with a record kind) whose
    body no record has goes to archive/unpublished/<tower>/, also from inside a removed directory,
    because it is the only copy of a plan (researcher, 2026-11-09: keep the project drafts). Each
    other old unit is deleted. A cache directory (__pycache__) goes at any age."""
    base = root / "runtime" / "drafts" / tower
    result: dict[str, list[Path]] = {"deleted": [], "archived": []}
    if not base.is_dir() or keep_days <= 0:
        return result
    limit = (time.time() if now is None else now) - keep_days * 86400
    keep_resolved = {p.resolve() for p in keep}
    referenced = _referenced_text(root, live_only)
    units, caches = _units(base, tower)
    doomed = list(caches)
    for unit in units:
        if (unit.name in KEEP_NAMES or unit.resolve() in keep_resolved
                or _newest(unit) > limit or _named(unit.name, referenced)):
            continue
        doomed.append(unit)
    published = _published_bodies(root) if doomed else set()
    target_dir = root / "archive" / "unpublished" / tower
    for path in doomed:
        if path.is_dir():
            if path.name not in CACHE_DIRS:
                for inner in sorted(path.rglob("*.md")):
                    if inner.is_file() and _unpublished_kind(inner, published) is not None:
                        result["archived"].append(inner)
                        if not dry_run:
                            _move(inner, target_dir / path.name)
            result["deleted"].append(path)
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
        elif _unpublished_kind(path, published) is not None:
            result["archived"].append(path)
            if not dry_run:
                _move(path, target_dir)
        else:
            result["deleted"].append(path)
            if not dry_run:
                path.unlink(missing_ok=True)
    if not dry_run:
        for name in SUBDIRS_BY_TOWER.get(tower, ()):
            (base / name).mkdir(exist_ok=True)
    return result


def _move(path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    number = 2
    while target.exists():
        target = target_dir / f"{path.stem}-{number}{path.suffix}"
        number += 1
    shutil.move(str(path), target)
