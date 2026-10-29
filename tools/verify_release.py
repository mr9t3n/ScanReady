from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image


class VerificationError(RuntimeError):
    """Raised when a release archive violates its embedded contract."""


@dataclass(frozen=True)
class VerificationResult:
    path: Path
    file_count: int
    bytes: int
    sha256: str


def _safe(relative: str) -> str:
    if not relative or "\\" in relative:
        raise VerificationError(f"unsafe archive path: {relative!r}")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationError(f"unsafe archive path: {relative!r}")
    return path.as_posix()


def _forbidden(relative: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        if normalized.endswith("/"):
            prefix = normalized.rstrip("/")
            if relative == prefix or relative.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatch(PurePosixPath(relative).name, normalized) or fnmatch.fnmatch(relative, normalized):
            return True
    return False


def _json_from_archive(archive: zipfile.ZipFile, name: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Could not read embedded {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"Embedded {label} must be a JSON object.")
    return value


def _runtime_expected(runtime: dict[str, Any], manifest: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    expected: set[str] = set()
    archive_hashes: dict[str, str] = {}
    platforms = runtime.get("platforms", {})
    if not isinstance(platforms, dict) or not platforms:
        raise VerificationError("Embedded runtime manifest has no platforms.")
    contract = manifest.get("runtime", {})
    pdf = contract.get("required_pdf_package", {})
    prefix = f"{str(pdf.get('name', 'pypdfium2')).replace('-', '_')}-{pdf.get('version', '')}-".lower()
    for platform, entry in platforms.items():
        archive = entry.get("archive", {})
        archive_path = _safe(str(archive.get("path", "")))
        expected.add(archive_path)
        archive_hashes[archive_path] = str(archive.get("sha256", "")).lower()
        expected.add(_safe(str(entry.get("pdfium_license", ""))))
        wheel_dir = _safe(str(entry.get("wheels", "")))
        expected.add(f"{wheel_dir}/__WHEEL_TREE__")
        entry["_required_pdf_prefix"] = prefix
        entry["_platform_name"] = platform
    return expected, archive_hashes


def verify_archive(path: Path) -> VerificationResult:
    path = path.resolve()
    if not path.is_file():
        raise VerificationError(f"Release archive is missing: {path}")
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise VerificationError("Archive contains duplicate entries.")
        roots = {PurePosixPath(_safe(name)).parts[0] for name in names}
        if len(roots) != 1:
            raise VerificationError(f"Archive must contain exactly one top-level folder; found {sorted(roots)}")
        root = next(iter(roots))
        for info in infos:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise VerificationError(f"Archive contains a symlink: {info.filename}")

        manifest_name = f"{root}/packaging/release_manifest.json"
        manifest = _json_from_archive(archive, manifest_name, "release manifest")
        if root != manifest.get("archive_root"):
            raise VerificationError("Archive root does not match the embedded release manifest.")
        max_bytes = int(manifest.get("max_zip_bytes", 0))
        if path.stat().st_size > max_bytes:
            raise VerificationError(f"Archive exceeds the size limit {max_bytes}.")
        relative_names = {name[len(root) + 1 :] for name in names}
        forbidden = [str(value) for value in manifest.get("forbidden", [])]
        for relative in relative_names:
            _safe(relative)
            if _forbidden(relative, forbidden):
                raise VerificationError(f"Archive contains forbidden entry: {relative}")
            if PurePosixPath(relative).suffix.lower() in {".jpg", ".jpeg", ".png"}:
                try:
                    with Image.open(io.BytesIO(archive.read(f"{root}/{relative}"))) as image:
                        image.load()
                        if image.getexif() or any("xmp" in key.lower() or key.lower() == "comment" for key in image.info):
                            raise VerificationError(f"unsafe image metadata in archive: {relative}")
                except (OSError, ValueError) as exc:
                    raise VerificationError(f"invalid archive image {relative}: {exc}") from exc

        required = {_safe(str(value)) for value in manifest.get("required_files", [])}
        missing = required - relative_names
        if missing:
            raise VerificationError(f"Archive is missing required entries: {sorted(missing)}")
        runtime_relative = _safe(str(manifest.get("runtime", {}).get("manifest", "")))
        runtime = _json_from_archive(archive, f"{root}/{runtime_relative}", "runtime manifest")
        runtime_expected, archive_hashes = _runtime_expected(runtime, manifest)

        allowed_tree_files: set[str] = set()
        for tree in manifest.get("allowed_trees", []):
            tree_path = _safe(str(tree.get("path", "")))
            extensions = {str(value).lower() for value in tree.get("extensions", [])}
            matches = {name for name in relative_names if name.startswith(tree_path + "/")}
            if any(PurePosixPath(name).suffix.lower() not in extensions for name in matches):
                raise VerificationError(f"Archive allowed tree has an unexpected extension: {tree_path}")
            minimum = int(tree.get("minimum_files", 0))
            if len(matches) < minimum:
                raise VerificationError(f"Archive allowed tree {tree_path} has {len(matches)} files; requires {minimum}")
            allowed_tree_files.update(matches)

        runtime_files: set[str] = set()
        for expected in runtime_expected:
            if expected.endswith("/__WHEEL_TREE__"):
                wheel_dir = expected.removesuffix("/__WHEEL_TREE__")
                wheels = {name for name in relative_names if name.startswith(wheel_dir + "/")}
                if not wheels:
                    raise VerificationError(f"Archive has no wheels for {wheel_dir}")
                runtime_files.update(wheels)
            else:
                if expected not in relative_names:
                    raise VerificationError(f"Archive is missing runtime entry: {expected}")
                runtime_files.add(expected)
        expected_all = required | allowed_tree_files | runtime_files
        extras = relative_names - expected_all
        if extras:
            raise VerificationError(f"undeclared archive entry: {sorted(extras)}")

        for name in ("data/manifest.csv", "data/calibration.csv", "data/heldout.csv"):
            if name not in relative_names:
                continue
            try:
                contents = archive.read(f"{root}/{name}").decode("utf-8")
            except UnicodeDecodeError as exc:
                raise VerificationError(f"Dataset manifest is not UTF-8: {name}") from exc
            reader = csv.DictReader(io.StringIO(contents))
            if not {"file", "release_allowed"} <= set(reader.fieldnames or []):
                raise VerificationError(f"Dataset manifest lacks release provenance: {name}")
            licensed_pages = set()
            if "source_page_id" in (reader.fieldnames or []):
                index_name = "data/licenses/notices.json"
                if index_name not in relative_names:
                    raise VerificationError("Dataset license notice index is not archived.")
                try:
                    index = json.loads(archive.read(f"{root}/{index_name}"))
                    notices = index["notices"]
                    if not isinstance(notices, list):
                        raise ValueError("notices must be a list")
                    for notice in notices:
                        notice_file = notice["file"]
                        if (not notice_file.startswith("data/licenses/") or notice_file not in relative_names
                                or not notice["license"] or not notice["source_page_id"]
                                or hashlib.sha256(archive.read(f"{root}/{notice_file}")).hexdigest() != notice["sha256"]):
                            raise ValueError("missing or changed notice")
                        licensed_pages.add(notice["source_page_id"])
                except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as exc:
                    raise VerificationError("Dataset license notice is missing or invalid.") from exc
            for row in reader:
                if "source_page_id" in row and row["source_page_id"] not in licensed_pages:
                    raise VerificationError("Dataset source page has no retained license notice.")
                target = row.get("file", "")
                if (row.get("release_allowed") != "true" or target not in relative_names
                        or PurePosixPath(target).suffix.lower() not in {".png", ".jpg", ".jpeg"}):
                    raise VerificationError(f"Dataset reference is not archived and approved: {name}: {target}")

        for archive_path, expected_hash in archive_hashes.items():
            actual = hashlib.sha256(archive.read(f"{root}/{archive_path}")).hexdigest()
            if len(expected_hash) != 64 or actual != expected_hash:
                raise VerificationError(f"Runtime SHA-256 mismatch: {archive_path}")

        pdf = manifest.get("runtime", {}).get("required_pdf_package", {})
        pdf_prefix = f"{str(pdf.get('name', 'pypdfium2')).replace('-', '_')}-{pdf.get('version', '')}-".lower()
        for platform, entry in runtime.get("platforms", {}).items():
            wheel_dir = _safe(str(entry.get("wheels", "")))
            wheels = [name for name in relative_names if name.startswith(wheel_dir + "/")]
            if not any(PurePosixPath(name).name.lower().startswith(pdf_prefix) for name in wheels):
                raise VerificationError(f"pypdfium2 wheel coverage is missing for {platform}")

        for transform in manifest.get("text_transforms", []):
            extensions = {str(value).lower() for value in transform.get("extensions", [])}
            newline = transform.get("newline")
            expected_mode = int(str(transform["archive_mode"]), 8) if transform.get("archive_mode") else None
            for info in infos:
                relative = info.filename[len(root) + 1 :]
                if PurePosixPath(relative).suffix.lower() not in extensions:
                    continue
                data = archive.read(info)
                if newline == "crlf":
                    remainder = data.replace(b"\r\n", b"")
                    if b"\n" in remainder or b"\r" in remainder:
                        raise VerificationError(f"Incorrect CRLF line endings: {relative}")
                elif newline == "lf" and b"\r" in data:
                    raise VerificationError(f"Incorrect LF line endings: {relative}")
                if expected_mode is not None and stat.S_IMODE(info.external_attr >> 16) != expected_mode:
                    raise VerificationError(f"Incorrect archive mode for {relative}")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return VerificationResult(path=path, file_count=len(names), bytes=path.stat().st_size, sha256=digest)


def verify_fresh_windows(
    path: Path,
    *,
    extraction_parent: Path | None = None,
    timeout: float = 300.0,
    verify_structure: bool = True,
) -> None:
    """Extract a verified release and exercise the instructor's offline Windows path."""
    if os.name != "nt":
        raise VerificationError("Fresh Windows verification must run on Windows.")
    path = path.resolve()
    if verify_structure:
        verify_archive(path)
    parent = (extraction_parent or path.parent / ".verification").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    extracted = (parent / f"scanready-{uuid.uuid4().hex}").resolve()
    if parent not in extracted.parents:
        raise VerificationError("Fresh verification extraction path escaped its parent.")
    extracted.mkdir()
    try:
        try:
            with zipfile.ZipFile(path) as archive:
                roots = {
                    PurePosixPath(_safe(info.filename)).parts[0]
                    for info in archive.infolist()
                    if not info.is_dir()
                }
                if len(roots) != 1:
                    raise VerificationError("Fresh verification requires exactly one archive root.")
                archive.extractall(extracted)
            app_root = extracted / next(iter(roots))
            launcher = app_root / "ScanReady-Windows.bat"
            environment = os.environ.copy()
            environment["PIP_NO_INDEX"] = "1"
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", str(launcher), "--check", "--offline-only"],
                cwd=app_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if completed.returncode != 0:
                output = (completed.stdout + completed.stderr).strip()
                tail = output[-4000:] if output else "no launcher output"
                raise VerificationError(
                    f"Fresh offline Windows check failed with exit code {completed.returncode}:\n{tail}"
                )
        except subprocess.TimeoutExpired as exc:
            raise VerificationError(f"Fresh offline Windows check exceeded {timeout:g} seconds.") from exc
    finally:
        if extracted.exists():
            shutil.rmtree(extracted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a ScanReady release archive.")
    default_dist = Path(__file__).resolve().parents[2] / "dist"
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--fresh-windows", action="store_true")
    parser.add_argument("--dist", type=Path, default=default_dist)
    args = parser.parse_args(argv)
    archive_path = args.archive
    if args.latest:
        candidates = sorted(args.dist.glob("ScanReady_*.zip"), key=lambda candidate: candidate.stat().st_mtime)
        if not candidates:
            parser.exit(2, "Release verification failed: no ScanReady release zip was found.\n")
        archive_path = candidates[-1]
    if archive_path is None:
        parser.error("provide an archive path or --latest")
    try:
        result = verify_archive(archive_path)
        if args.latest or args.fresh_windows:
            verify_fresh_windows(archive_path, verify_structure=False)
    except (VerificationError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"Release verification failed: {exc}\n")
    print(f"Verified: {result.path}")
    print(f"Files: {result.file_count}")
    print(f"Bytes: {result.bytes}")
    print(f"SHA-256: {result.sha256}")
    if args.latest or args.fresh_windows:
        print("Fresh offline Windows check: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

