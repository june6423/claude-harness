"""Cleanup of old tower drafts in runtime/drafts/<tower>/ (researcher decision 2026-11-09, DESIGN §29).

The drafts directory is scratch space. Records keep the published text of each record draft, the
message ledger keeps each message, and the documents for the researcher are in archive/reports/
(D27). So an old draft that no kept layer names is a copy or a one-time script, and the harness
deletes it at each handover. The rule is mechanical, so the harness applies it, not a tower.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from .records import KINDS, RecordError, split_front_matter

DEFAULTS: dict[str, Any] = {"prune_towers": ["dt"], "keep_days": 7}
KEEP_NAMES = {"handover.md"}
SUBDIRS = ("records", "msg", "scripts")
CACHE_DIRS = {"__pycache__"}


def settings(config: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(config.get("drafts") or {})}


def _referenced_text(root: Path) -> str:
    """The text of the kept layers. A draft whose file name occurs here stays."""
    parts = []
    for folder, pattern in (("archive", "**/*.md"), ("worklog", "*.md"), ("state", "*.md")):
        for path in sorted((root / folder).glob(pattern)):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


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


def prune(root: Path, tower: str, keep_days: float, dry_run: bool = False,
          keep: tuple[Path, ...] = (), now: float | None = None) -> dict[str, list[Path]]:
    """Remove the old drafts of a tower. Returns {"deleted": [...], "archived": [...]} with the draft paths.

    A file stays when: it is younger than keep_days (by mtime), its name is in KEEP_NAMES, it is in
    `keep`, it is in a hidden directory, or its file name occurs in archive/, worklog/, or state/.
    An old record draft (JSON front matter with a record kind) whose body no record has goes to
    archive/unpublished/<tower>/, because it is the only copy of a plan (researcher, 2026-11-09:
    keep the project drafts). Each other old file is deleted. A cache directory (__pycache__) goes at
    any age. The subdirectories records/, msg/, and scripts/ stay."""
    base = root / "runtime" / "drafts" / tower
    result: dict[str, list[Path]] = {"deleted": [], "archived": []}
    if not base.is_dir() or keep_days <= 0:
        return result
    limit = (time.time() if now is None else now) - keep_days * 86400
    keep_resolved = {p.resolve() for p in keep}
    referenced = _referenced_text(root)
    doomed: list[Path] = []
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_dir():
            if path.name in CACHE_DIRS and not any(p.name in CACHE_DIRS for p in relative.parents):
                doomed.append(path)
            continue
        if any(part in CACHE_DIRS for part in relative.parts[:-1]):
            continue  # removed with its cache directory
        if (path.name in KEEP_NAMES or path.resolve() in keep_resolved
                or path.stat().st_mtime > limit or path.name in referenced):
            continue
        doomed.append(path)
    published = _published_bodies(root) if doomed else set()
    target_dir = root / "archive" / "unpublished" / tower
    for path in doomed:
        archived = path.is_file() and _unpublished_kind(path, published) is not None
        result["archived" if archived else "deleted"].append(path)
        if dry_run:
            continue
        if archived:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / path.name
            number = 2
            while target.exists():
                target = target_dir / f"{path.stem}-{number}{path.suffix}"
                number += 1
            shutil.move(str(path), target)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    if not dry_run:
        for name in SUBDIRS:
            (base / name).mkdir(exist_ok=True)
    return result
