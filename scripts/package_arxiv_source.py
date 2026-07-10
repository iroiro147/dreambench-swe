#!/usr/bin/env python3
"""Build a deterministic arXiv source tarball for the paper."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "paper_arXiv"
DEFAULT_OUTPUT = ROOT / "dist" / "dreambench-swe-paper-arxiv-source.tar.gz"

INCLUDE_FILES = (
    "main.tex",
    "main.bbl",
    "bibliography/sources.bib",
    "analysis/fold/v2_tables.tex",
)

INCLUDE_DIRS = (
    "sections",
    "figures",
)

FORBIDDEN_UPLOAD_FILES = {
    "main.pdf",
    "main.log",
    "main.blg",
    "main.aux",
    "main.out",
    "main.fdb_latexmk",
    "main.fls",
    "main.synctex.gz",
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    members = collect_members(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_tarball(args.source, members, args.output)
    print(f"archive={display_path(args.output)}")
    print(f"archive_sha256={sha256_file(args.output)}")
    print(f"files={len(members)}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def collect_members(source: Path) -> list[Path]:
    if not source.is_dir():
        raise SystemExit(f"source directory missing: {source}")
    members: set[Path] = set()
    for rel in INCLUDE_FILES:
        path = source / rel
        if not path.is_file():
            raise SystemExit(f"required arXiv source file missing: {rel}")
        members.add(Path(rel))
    for rel_dir in INCLUDE_DIRS:
        path = source / rel_dir
        if not path.is_dir():
            raise SystemExit(f"required arXiv source directory missing: {rel_dir}")
        for child in path.rglob("*"):
            if child.is_file():
                members.add(child.relative_to(source))
    forbidden_present = sorted(
        rel.as_posix()
        for rel in members
        if rel.name in FORBIDDEN_UPLOAD_FILES or rel.as_posix() in FORBIDDEN_UPLOAD_FILES
    )
    if forbidden_present:
        raise SystemExit("forbidden generated upload files selected: " + ", ".join(forbidden_present))
    return sorted(members, key=lambda item: item.as_posix())


def write_tarball(source: Path, members: list[Path], output: Path) -> None:
    if output.exists():
        output.unlink()
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                dirs = sorted({parent for rel in members for parent in rel.parents if str(parent) != "."}, key=lambda item: item.as_posix())
                for rel_dir in dirs:
                    add_path(tar, source / rel_dir, rel_dir.as_posix())
                for rel in members:
                    add_path(tar, source / rel, rel.as_posix())


def add_path(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    if path.is_symlink():
        raise SystemExit(f"refusing to package symlink: {path}")
    info = tar.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if path.is_file():
        with path.open("rb") as handle:
            tar.addfile(info, fileobj=handle)
    else:
        tar.addfile(info)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
