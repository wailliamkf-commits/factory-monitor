#!/usr/bin/env python3
"""Create and verify a content manifest for an explicitly selected bundle root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


MANIFEST_NAME = "offline_bundle_manifest.json"
SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_SIZE = 1024 * 1024


class BundleError(ValueError):
    """A bundle is unsafe or does not match its manifest."""


def _bundle_root(path: str | os.PathLike[str]) -> Path:
    supplied = Path(path)
    try:
        supplied_mode = supplied.lstat().st_mode
    except OSError as exc:
        raise BundleError(f"bundle root cannot be read: {exc}") from exc
    if stat.S_ISLNK(supplied_mode):
        raise BundleError("bundle root must not be a symbolic link")
    if not stat.S_ISDIR(supplied_mode):
        raise BundleError("bundle root must be an existing directory")
    return supplied.resolve(strict=True)


def _hash_regular_file(path: Path, expected_stat: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BundleError(f"cannot safely open file {path.name!r}: {exc}") from exc

    digest = hashlib.sha256()
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise BundleError(f"not a regular file: {path}")
        if (opened_stat.st_dev, opened_stat.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ):
            raise BundleError(f"file changed while scanning: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(_CHUNK_SIZE):
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _scan_files(root: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise BundleError(f"cannot scan directory {directory}: {exc}") from exc

        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise BundleError(f"cannot inspect {relative!r}: {exc}") from exc

            if stat.S_ISLNK(entry_stat.st_mode):
                raise BundleError(f"symbolic links are not allowed: {relative!r}")
            if stat.S_ISDIR(entry_stat.st_mode):
                visit(path)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise BundleError(f"only regular files and directories are allowed: {relative!r}")

            if relative.casefold() == MANIFEST_NAME.casefold():
                if relative != MANIFEST_NAME:
                    raise BundleError(
                        f"file name collides with the reserved manifest name: {relative!r}"
                    )
                continue
            files[relative] = {
                "path": relative,
                "size": entry_stat.st_size,
                "sha256": _hash_regular_file(path, entry_stat),
            }

    visit(root)
    return files


def create_manifest(
    root_path: str | os.PathLike[str], *, platform: str, python_version: str
) -> Path:
    """Write a bundle manifest using only caller-supplied compatibility labels."""
    if not isinstance(platform, str) or not platform.strip():
        raise BundleError("platform metadata must be supplied by the packager")
    if not isinstance(python_version, str) or not python_version.strip():
        raise BundleError("Python version metadata must be supplied by the packager")

    root = _bundle_root(root_path)
    entries = _scan_files(root)
    document = {
        "schema_version": SCHEMA_VERSION,
        "platform": platform.strip(),
        "python_version": python_version.strip(),
        "files": [entries[name] for name in sorted(entries)],
    }
    manifest_path = root / MANIFEST_NAME
    try:
        old_mode = manifest_path.lstat().st_mode
    except FileNotFoundError:
        old_mode = None
    except OSError as exc:
        raise BundleError(f"cannot inspect existing manifest: {exc}") from exc
    if old_mode is not None and not stat.S_ISREG(old_mode):
        raise BundleError("existing manifest path must be a regular file")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{MANIFEST_NAME}.",
            suffix=".tmp",
            dir=root,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(document, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, manifest_path)
    except OSError as exc:
        raise BundleError(f"cannot write manifest: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return manifest_path


def _validated_manifest_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BundleError("manifest contains an invalid file path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BundleError(f"manifest path is not a normalized relative path: {value!r}")
    relative = PurePosixPath(value)
    windows_relative = PureWindowsPath(value)
    if (
        relative.is_absolute()
        or windows_relative.is_absolute()
        or windows_relative.drive
        or ":" in value
        or relative.as_posix() != value
    ):
        raise BundleError(f"manifest path is not a normalized relative path: {value!r}")
    if value == MANIFEST_NAME:
        raise BundleError("manifest must not list itself")
    return value


def _read_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / MANIFEST_NAME
    try:
        mode = manifest_path.lstat().st_mode
    except OSError as exc:
        raise BundleError(f"manifest is missing or unreadable: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise BundleError("manifest must be a regular file, not a symbolic link or special file")
    try:
        with manifest_path.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"manifest is unreadable or invalid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise BundleError("manifest root must be a JSON object")
    if set(document) != {"schema_version", "platform", "python_version", "files"}:
        raise BundleError("manifest contains unknown or unsupported claims")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("manifest schema_version is missing or unsupported")
    for field in ("platform", "python_version"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BundleError(f"manifest {field} metadata is missing")
    listed = document.get("files")
    if not isinstance(listed, list):
        raise BundleError("manifest files must be a list")

    seen: set[str] = set()
    for item in listed:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise BundleError("manifest file entries must contain path, size and sha256 only")
        relative = _validated_manifest_path(item["path"])
        if relative in seen:
            raise BundleError(f"manifest lists a file more than once: {relative!r}")
        seen.add(relative)
        size = item["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BundleError(f"manifest size is invalid for {relative!r}")
        if not isinstance(item["sha256"], str) or not _SHA256_RE.fullmatch(item["sha256"]):
            raise BundleError(f"manifest SHA-256 is invalid for {relative!r}")
    return document


def verify_manifest(root_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify exact file membership, sizes and SHA-256 hashes below the root."""
    root = _bundle_root(root_path)
    manifest = _read_manifest(root)
    actual = _scan_files(root)
    expected = {item["path"]: item for item in manifest["files"]}

    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    if missing or extra:
        details = []
        if missing:
            details.append("missing files: " + ", ".join(missing))
        if extra:
            details.append("extra files: " + ", ".join(extra))
        raise BundleError("bundle files do not match manifest (" + "; ".join(details) + ")")

    for relative in sorted(expected):
        wanted = expected[relative]
        found = actual[relative]
        if wanted["size"] != found["size"]:
            raise BundleError(f"size mismatch for {relative!r}")
        if wanted["sha256"] != found["sha256"]:
            raise BundleError(f"SHA-256 mismatch for {relative!r}")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify a SHA-256 content manifest for an offline bundle."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", metavar="BUNDLE_ROOT", help="create a manifest in this directory")
    action.add_argument("--verify", metavar="BUNDLE_ROOT", help="verify this directory against its manifest")
    parser.add_argument("--platform", help="packager-supplied target platform label (required for --create)")
    parser.add_argument("--python-version", help="packager-supplied Python version label (required for --create)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.create is not None:
            if arguments.platform is None or arguments.python_version is None:
                parser.error("--create requires both --platform and --python-version")
            manifest_path = create_manifest(
                arguments.create,
                platform=arguments.platform,
                python_version=arguments.python_version,
            )
            print(
                f"Created {manifest_path}; platform and Python labels were supplied by the packager. "
                "This is not an installation proof."
            )
            return 0

        if arguments.platform is not None or arguments.python_version is not None:
            parser.error("--platform and --python-version apply only to --create")
        verify_manifest(arguments.verify)
        print("Bundle content manifest is valid; this is not an installation proof.")
        return 0
    except BundleError as exc:
        print(f"offline_bundle: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
