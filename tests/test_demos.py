from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from scanready import cli


@pytest.fixture
def demo_root(tmp_path):
    source = Path(__file__).parents[1]
    shutil.copytree(source / "demo_images", tmp_path / "demo_images")
    shutil.copytree(source / "config", tmp_path / "config")
    return tmp_path


def test_demo_check_corrects_saves_png_and_preserves_inputs(demo_root, capsys):
    before = {p.name: sha256(p.read_bytes()).hexdigest() for p in (demo_root / "demo_images").iterdir()}
    assert cli.main(["check-demos", "--root", str(demo_root)]) == 0
    output = capsys.readouterr().out
    assert "3" in output and "READY" in output
    assert {p.name: sha256(p.read_bytes()).hexdigest() for p in (demo_root / "demo_images").iterdir()} == before
    assert not (demo_root / "results").exists()


def test_demo_check_fails_for_wrong_outcome(demo_root, capsys):
    (demo_root / "demo_images/clean_document.jpg").write_bytes(
        (demo_root / "demo_images/retry_capture_document.jpg").read_bytes())
    assert cli.main(["check-demos", "--root", str(demo_root)]) == 1
    assert "clean_document.jpg" in capsys.readouterr().err


@pytest.mark.parametrize("contents", ["file,expected_outcome\n", "file,expected_outcome\n../README.md,READY\n"])
def test_demo_check_rejects_empty_or_unsafe_manifest(demo_root, contents):
    (demo_root / "demo_images/expected.csv").write_text(contents)
    assert cli.main(["check-demos", "--root", str(demo_root)]) != 0
