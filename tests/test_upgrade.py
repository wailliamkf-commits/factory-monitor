import importlib.util
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('upgrade', Path(__file__).parents[1] / 'scripts/windows/upgrade.py')
upgrade = importlib.util.module_from_spec(spec)

def load():
    spec.loader.exec_module(upgrade)
    return upgrade


def test_upgrade_preserves_source_and_unrelated_windows_repair(tmp_path):
    m = load()
    source = tmp_path / 'source'; source.mkdir(); (source/'src').mkdir()
    (source/'src/a.py').write_text('x = 1\n# onsite repair stays\n')
    (source/'config.json').write_text('private settings')
    patch = tmp_path/'change.patch'
    patch.write_text('diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n # onsite repair stays\n')
    dest = tmp_path/'candidate'
    m.prepare(source, dest, patch, 'git')
    assert (source/'src/a.py').read_text() == 'x = 1\n# onsite repair stays\n'
    assert (dest/'src/a.py').read_text() == 'x = 2\n# onsite repair stays\n'
    assert not (dest/'config.json').exists()
    assert (dest/'source-fingerprints.json').exists()


def test_upgrade_conflict_does_not_modify_source(tmp_path):
    m=load(); source=tmp_path/'source'; source.mkdir(); (source/'src').mkdir()
    (source/'src/a.py').write_text('x = 9\n')
    patch=tmp_path/'change.patch'; patch.write_text('diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n # onsite repair stays\n')
    with pytest.raises(subprocess.CalledProcessError): m.prepare(source,tmp_path/'candidate',patch,'git')
    assert (source/'src/a.py').read_text() == 'x = 9\n'


def test_upgrade_refuses_destination_inside_source(tmp_path):
    m=load(); source=tmp_path/'source'; source.mkdir()
    patch=tmp_path/'p';patch.write_text('')
    with pytest.raises(ValueError): m.prepare(source,source/'candidate',patch,'git')


def test_upgrade_refuses_symlink_source_files(tmp_path):
    m=load(); source=tmp_path/'source';source.mkdir();(source/'src').mkdir()
    outside=tmp_path/'outside';outside.write_text('secret')
    (source/'src/private.py').symlink_to(outside)
    patch=tmp_path/'p';patch.write_text('')
    with pytest.raises(ValueError):m.prepare(source,tmp_path/'candidate',patch,'git')
