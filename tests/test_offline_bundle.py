import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from scripts import offline_bundle


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "offline_bundle.py"
PYTHON = Path(sys.executable)
MANIFEST_NAME = "offline_bundle_manifest.json"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def make_bundle(root: Path) -> None:
    (root / "nested").mkdir(parents=True)
    (root / "app.py").write_bytes(b"print('offline')\n")
    (root / "nested" / "runtime.bin").write_bytes(b"weights-placeholder")


def create_bundle(root: Path) -> subprocess.CompletedProcess[str]:
    return run_cli(
        "--create",
        str(root),
        "--platform",
        "windows-x64",
        "--python-version",
        "3.12.4",
    )


def test_create_writes_hashes_and_packager_metadata_then_verify_succeeds(
    tmp_path: Path,
) -> None:
    make_bundle(tmp_path)

    created = create_bundle(tmp_path)

    assert created.returncode == 0, created.stderr
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["platform"] == "windows-x64"
    assert manifest["python_version"] == "3.12.4"
    assert manifest["files"] == [
        {
            "path": "app.py",
            "size": 17,
            "sha256": hashlib.sha256(b"print('offline')\n").hexdigest(),
        },
        {
            "path": "nested/runtime.bin",
            "size": 19,
            "sha256": hashlib.sha256(b"weights-placeholder").hexdigest(),
        },
    ]
    assert "installable" not in manifest
    assert "not an installation proof" in created.stdout.lower()

    verified = run_cli("--verify", str(tmp_path))
    assert verified.returncode == 0, verified.stderr
    assert "valid" in verified.stdout.lower()


@pytest.mark.parametrize(
    "change", ["missing", "changed", "same-size-changed", "extra"]
)
def test_verify_rejects_missing_changed_or_extra_files(
    tmp_path: Path, change: str
) -> None:
    make_bundle(tmp_path)
    assert create_bundle(tmp_path).returncode == 0

    if change == "missing":
        (tmp_path / "app.py").unlink()
    elif change == "changed":
        (tmp_path / "app.py").write_bytes(b"changed")
    elif change == "same-size-changed":
        (tmp_path / "app.py").write_bytes(b"x" * 17)
    else:
        (tmp_path / "extra.txt").write_text("unlisted", encoding="utf-8")

    verified = run_cli("--verify", str(tmp_path))
    assert verified.returncode != 0
    assert any(
        reason in verified.stderr.lower()
        for reason in ("manifest", "file", "mismatch")
    )


def test_create_rejects_symlink_inside_bundle(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("must not be followed", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)

    created = create_bundle(tmp_path)

    assert created.returncode != 0
    assert not (tmp_path / MANIFEST_NAME).exists()


def test_verify_rejects_symlink_added_after_manifest_creation(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    assert create_bundle(tmp_path).returncode == 0
    (tmp_path / "linked.txt").symlink_to(tmp_path / "app.py")

    verified = run_cli("--verify", str(tmp_path))
    assert verified.returncode != 0


def test_verify_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    assert create_bundle(tmp_path).returncode == 0
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside.txt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verified = run_cli("--verify", str(tmp_path))

    assert verified.returncode != 0
    assert "path" in verified.stderr.lower()


def test_verify_rejects_windows_drive_path_in_manifest(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    assert create_bundle(tmp_path).returncode == 0
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "C:/outside.txt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verified = run_cli("--verify", str(tmp_path))

    assert verified.returncode != 0
    assert "path" in verified.stderr.lower()


def test_verify_rejects_unrecognized_manifest_claims(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    assert create_bundle(tmp_path).returncode == 0
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["installable"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verified = run_cli("--verify", str(tmp_path))

    assert verified.returncode != 0
    assert "manifest" in verified.stderr.lower()


def test_create_requires_explicit_platform_and_python_metadata(tmp_path: Path) -> None:
    make_bundle(tmp_path)

    created = run_cli("--create", str(tmp_path), "--platform", "windows-x64")

    assert created.returncode != 0
    assert not (tmp_path / MANIFEST_NAME).exists()


def test_scan_uses_fresh_path_stat_when_direntry_ids_are_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("content", encoding="utf-8")

    class WindowsLikeDirEntry:
        def __init__(self, path: Path) -> None:
            self.path = str(path)
            self.name = path.name

        def stat(self, *, follow_symlinks: bool = True) -> SimpleNamespace:
            actual = Path(self.path).stat(follow_symlinks=follow_symlinks)
            return SimpleNamespace(
                st_mode=actual.st_mode,
                st_size=actual.st_size,
                st_dev=0,
                st_ino=0,
            )

    class WindowsLikeScandir:
        def __init__(self, directory: str | os.PathLike[str]) -> None:
            self.entries = [WindowsLikeDirEntry(Path(directory) / "app.py")]

        def __iter__(self):
            return iter(self.entries)

    def scandir(directory: str | os.PathLike[str]) -> WindowsLikeScandir:
        assert Path(directory) == tmp_path
        return WindowsLikeScandir(directory)

    monkeypatch.setattr(offline_bundle.os, "scandir", scandir)

    scanned = offline_bundle._scan_files(tmp_path)

    assert scanned["app.py"]["size"] == len(b"content")
    assert scanned["app.py"]["sha256"] == hashlib.sha256(b"content").hexdigest()


def test_scan_rejects_file_replaced_after_path_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "app.py"
    replacement = tmp_path / "replacement.tmp"
    target.write_text("original", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    original_stat = Path.stat
    replaced = False

    def stat_then_replace(
        path: Path, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        nonlocal replaced
        result = original_stat(path, follow_symlinks=follow_symlinks)
        if path == target and not follow_symlinks and not replaced:
            os.replace(replacement, target)
            replaced = True
        return result

    monkeypatch.setattr(Path, "stat", stat_then_replace)

    with pytest.raises(offline_bundle.BundleError, match="file changed while scanning"):
        offline_bundle._scan_files(tmp_path)


def test_bundle_root_must_be_explicit() -> None:
    created = run_cli("--create", "--platform", "windows-x64", "--python-version", "3.12")

    assert created.returncode != 0
