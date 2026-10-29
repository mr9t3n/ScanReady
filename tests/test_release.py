from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from tools.build_release import ReleaseError, build_release
from tools.verify_release import VerificationError, verify_archive, verify_fresh_windows


PLATFORMS = {
    "windows-x64": "win_amd64",
    "macos-arm64": "macosx_11_0_arm64",
    "macos-x86_64": "macosx_10_15_x86_64",
    "linux-x86_64": "manylinux_2_17_x86_64",
}


@pytest.fixture
def release_root(request: pytest.FixtureRequest) -> Path:
    parent = (Path(__file__).resolve().parent / ".release-tmp").resolve()
    root = (parent / request.node.name).resolve()
    if parent not in root.parents:
        raise RuntimeError("Release test path escaped its controlled directory.")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    yield root
    if root.exists():
        shutil.rmtree(root)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release_fixture(parent: Path) -> tuple[Path, Path]:
    source = parent / "ScanReady"
    output = parent / "dist"
    (source / "packaging").mkdir(parents=True)
    (source / "demo_images").mkdir()
    (source / "vendor" / "python").mkdir(parents=True)
    (source / "vendor" / "wheels").mkdir(parents=True)
    (source / "vendor" / "licenses" / "pdfium").mkdir(parents=True)
    (source / "app.py").write_text("print('fixture')\n", encoding="utf-8")
    (source / "ScanReady-Windows.bat").write_bytes(
        b"@echo off\n"
        b"if /I not \"%~1\"==\"--check\" exit /b 9\n"
        b"if /I not \"%~2\"==\"--offline-only\" exit /b 9\n"
        b"echo offline fixture passed\n"
    )
    (source / "ScanReady-macOS.command").write_bytes(b"#!/usr/bin/env bash\r\necho fixture\r\n")
    (source / "ScanReady-Linux.sh").write_bytes(b"#!/usr/bin/env bash\r\necho fixture\r\n")
    Image.new("RGB", (8, 8), "white").save(source / "demo_images" / "page.png")

    platforms: dict[str, object] = {}
    for platform, wheel_tag in PLATFORMS.items():
        archive = source / "vendor" / "python" / f"python-{platform}.tar.gz"
        archive.write_bytes(f"runtime:{platform}".encode("utf-8"))
        wheel_dir = source / "vendor" / "wheels" / platform
        wheel_dir.mkdir()
        (wheel_dir / f"pypdfium2-4.30.0-py3-none-{wheel_tag}.whl").write_bytes(b"wheel-fixture")
        license_path = source / "vendor" / "licenses" / "pdfium" / f"{platform}-LICENSE.txt"
        license_path.write_text("PDFium fixture license\n", encoding="utf-8")
        platforms[platform] = {
            "archive": {
                "path": archive.relative_to(source).as_posix(),
                "url": f"https://example.edu/{archive.name}",
                "sha256": _sha256(archive),
            },
            "wheels": wheel_dir.relative_to(source).as_posix(),
            "pdfium_license": license_path.relative_to(source).as_posix(),
        }
    runtime = {"schema_version": 1, "python_version": "3.11.16", "platforms": platforms}
    (source / "packaging" / "runtime.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "archive_root": "ScanReady",
        "max_zip_bytes": 10_000_000,
        "reject_symlinks": True,
        "reject_path_escape": True,
        "required_files": [
            "app.py",
            "ScanReady-Windows.bat",
            "ScanReady-macOS.command",
            "ScanReady-Linux.sh",
            "packaging/runtime.json",
            "packaging/release_manifest.json",
        ],
        "allowed_trees": [
            {"path": "demo_images", "extensions": [".png"], "minimum_files": 1, "purpose": "fixture"}
        ],
        "runtime": {
            "manifest": "packaging/runtime.json",
            "wheel_directories": [f"vendor/wheels/{platform}" for platform in PLATFORMS],
            "wheel_extensions": [".whl"],
            "required_pdf_package": {
                "name": "pypdfium2",
                "version": "4.30.0",
                "platform_wheels_required": True,
                "preserve_bundled_pdfium_licenses": True,
            },
        },
        "text_transforms": [
            {"extensions": [".bat"], "newline": "crlf"},
            {"extensions": [".sh", ".command"], "newline": "lf", "archive_mode": "0755"},
        ],
        "forbidden": [".git/", ".runtime/", "data/raw/", "*.zip"],
    }
    (source / "packaging" / "release_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return source, output


def test_builder_creates_one_root_and_normalizes_scripts(release_root: Path) -> None:
    source, output = _release_fixture(release_root)

    result = build_release(source, output, timestamp="20260926_2000")

    assert result.path.name == "ScanReady_20260926_2000.zip"
    assert result.sha256 == _sha256(result.path)
    with zipfile.ZipFile(result.path) as archive:
        names = archive.namelist()
        assert names and all(name.startswith("ScanReady/") for name in names)
        assert archive.read("ScanReady/ScanReady-Windows.bat").count(b"\r\n") == 4
        assert b"\r\n" not in archive.read("ScanReady/ScanReady-Linux.sh")
        unix_mode = archive.getinfo("ScanReady/ScanReady-Linux.sh").external_attr >> 16
        assert stat.S_IMODE(unix_mode) == 0o755
    verified = verify_archive(result.path)
    assert verified.file_count == result.file_count


def test_builder_rejects_missing_required_file(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    (source / "app.py").unlink()
    with pytest.raises(ReleaseError, match="required file is missing"):
        build_release(source, output, timestamp="20260926_2001")


def test_builder_rejects_manifest_path_escape(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    manifest_path = source / "packaging" / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["required_files"].append("../outside.txt")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseError, match="unsafe relative path"):
        build_release(source, output, timestamp="20260926_2002")


def test_builder_rejects_unexpected_allowed_tree_extension(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    (source / "demo_images" / "notes.exe").write_bytes(b"not allowed")
    with pytest.raises(ReleaseError, match="unexpected extension"):
        build_release(source, output, timestamp="20260926_2003")


def test_builder_rejects_runtime_hash_mismatch(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    (source / "vendor" / "python" / "python-windows-x64.tar.gz").write_bytes(b"tampered")
    with pytest.raises(ReleaseError, match="runtime SHA-256 mismatch"):
        build_release(source, output, timestamp="20260926_2004")


def test_builder_requires_pdfium_wheel_and_license_for_each_platform(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    wheel = next((source / "vendor" / "wheels" / "macos-arm64").glob("pypdfium2-*.whl"))
    wheel.unlink()
    with pytest.raises(ReleaseError, match="pypdfium2==4.30.0.*macos-arm64"):
        build_release(source, output, timestamp="20260926_2005")


def test_builder_enforces_zip_size_ceiling(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    manifest_path = source / "packaging" / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["max_zip_bytes"] = 100
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseError, match="exceeds.*100"):
        build_release(source, output, timestamp="20260926_2006")


def test_verifier_rejects_extra_archive_entry(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    result = build_release(source, output, timestamp="20260926_2007")
    with zipfile.ZipFile(result.path, "a") as archive:
        archive.writestr("ScanReady/extra.txt", "undeclared")
    with pytest.raises(VerificationError, match="undeclared archive entry"):
        verify_archive(result.path)


@pytest.mark.skipif(os.name != "nt", reason="Fresh Windows verification requires cmd.exe")
def test_fresh_windows_verifier_extracts_and_runs_offline_check(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    result = build_release(source, output, timestamp="20260926_2008")
    extraction_parent = release_root / "verification"

    verify_fresh_windows(result.path, extraction_parent=extraction_parent, timeout=15)

    assert list(extraction_parent.iterdir()) == []



@pytest.mark.skipif(os.name == "nt", reason="Symlink creation requires privileges on Windows")
def test_builder_rejects_a_symlinked_ancestor(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    outside = release_root / "outside"
    outside.mkdir()
    (outside / "entry.py").write_text("private content\n")
    (source / "linked").symlink_to(outside, target_is_directory=True)
    path = source / "packaging/release_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["required_files"].append("linked/entry.py")
    path.write_text(json.dumps(manifest))
    with pytest.raises(ReleaseError, match="symlink|outside"):
        build_release(source, output)


def _private_image_bytes() -> bytes:
    import io
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[270] = "PRIVATE METADATA"
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG", exif=exif)
    return buffer.getvalue()


def test_builder_rejects_image_metadata(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    (source / "demo_images/page.png").write_bytes(_private_image_bytes())
    with pytest.raises(ReleaseError, match="metadata"):
        build_release(source, output)


def test_verifier_rejects_image_metadata_in_an_archive(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    result = build_release(source, output)
    tampered = release_root / "metadata.zip"
    with zipfile.ZipFile(result.path) as original, zipfile.ZipFile(tampered, "w") as destination:
        for info in original.infolist():
            data = _private_image_bytes() if info.filename == "ScanReady/demo_images/page.png" else original.read(info)
            destination.writestr(info, data)
    with pytest.raises(VerificationError, match="metadata"):
        verify_archive(tampered)


def _add_dataset_manifest(source: Path, contents: str) -> None:
    (source / 'data').mkdir(exist_ok=True)
    (source / 'data/manifest.csv').write_text(contents)
    path = source / 'packaging/release_manifest.json'
    contract = json.loads(path.read_text())
    contract['required_files'].append('data/manifest.csv')
    path.write_text(json.dumps(contract))


@pytest.mark.parametrize('row', [
    'data/generated/local-only/missing.png,true',
    'demo_images/page.png,false',
    '../outside.png,true',
])
def test_builder_rejects_unstaged_or_unapproved_dataset_rows(release_root: Path, row: str) -> None:
    source, output = _release_fixture(release_root)
    _add_dataset_manifest(source, 'file,release_allowed\n' + row + '\n')
    with pytest.raises(ReleaseError, match='[Dd]ataset'):
        build_release(source, output, timestamp='20260927_2100')


def test_verifier_rejects_dataset_reference_missing_from_archive(release_root: Path) -> None:
    source, output = _release_fixture(release_root)
    _add_dataset_manifest(source, 'file,release_allowed\ndemo_images/page.png,true\n')
    result = build_release(source, output, timestamp='20260927_2101')
    verify_archive(result.path)
    tampered = release_root / 'missing-dataset.zip'
    with zipfile.ZipFile(result.path) as original, zipfile.ZipFile(tampered, 'w') as destination:
        for info in original.infolist():
            content = original.read(info)
            if info.filename == 'ScanReady/data/manifest.csv':
                content = b'file,release_allowed\ndata/generated/local-only/missing.png,true\n'
            destination.writestr(info, content)
    with pytest.raises(VerificationError, match='[Dd]ataset'):
        verify_archive(tampered)


@pytest.mark.parametrize("failure", ["missing_notice", "notice_hash", "missing_index", "valid"])
def test_builder_requires_retained_dataset_license(release_root: Path, failure: str) -> None:
    source, output = _release_fixture(release_root)
    _add_dataset_manifest(source, 'file,release_allowed,source_page_id,origin\ndemo_images/page.png,true,page,scholarly\n')
    folder = source / 'data/licenses'
    folder.mkdir()
    notice = folder / 'NOTICE.md'
    notice.write_bytes(b'Approved exact notice')
    index = folder / 'notices.json'
    index.write_text(json.dumps({'notices': [{'source_page_id': 'page', 'license': 'MIT', 'file': 'data/licenses/NOTICE.md', 'sha256': _sha256(notice)}]}))
    contract_path = source / 'packaging/release_manifest.json'
    contract = json.loads(contract_path.read_text())
    contract['allowed_trees'].append({'path': 'data/licenses', 'extensions': ['.md', '.json'], 'minimum_files': 0})
    contract_path.write_text(json.dumps(contract))
    if failure == 'missing_notice': notice.unlink()
    if failure == 'notice_hash': notice.write_bytes(b'changed')
    if failure == 'missing_index': index.unlink()
    if failure == 'valid':
        result = build_release(source, output, timestamp='20260928_0000')
        verify_archive(result.path)
    else:
        with pytest.raises(ReleaseError, match='[Ll]icense|[Nn]otice'):
            build_release(source, output, timestamp='20260928_0000')
