from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import stat
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image


class ReleaseError(RuntimeError):
    """Raised when release construction would violate the staging contract."""


@dataclass(frozen=True)
class ReleaseResult:
    path: Path
    file_count: int
    bytes: int
    sha256: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(raw: str) -> str:
    if not raw or "\\" in raw:
        raise ReleaseError(f"unsafe relative path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError(f"unsafe relative path: {raw!r}")
    return path.as_posix()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must contain a JSON object.")
    return value


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


def _add_file(files: dict[str, Path], source_root: Path, relative: str, forbidden: list[str]) -> None:
    safe = _safe_relative(relative)
    if _forbidden(safe, forbidden):
        raise ReleaseError(f"forbidden release path: {safe}")
    path = source_root / PurePosixPath(safe)
    current = source_root
    for part in PurePosixPath(safe).parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseError(f"symlinks are not allowed in the release: {safe}")
    if not path.resolve().is_relative_to(source_root):
        raise ReleaseError(f"release path is outside the source root: {safe}")
    if not path.is_file():
        raise ReleaseError(f"required file is missing: {safe}")
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        try:
            with Image.open(path) as image:
                image.load()
                if image.getexif() or any("xmp" in key.lower() or key.lower() == "comment" for key in image.info):
                    raise ReleaseError(f"unsafe image metadata in release: {safe}")
        except (OSError, ValueError) as exc:
            raise ReleaseError(f"invalid release image {safe}: {exc}") from exc
    files[safe] = path


def _runtime_files(
    source_root: Path,
    manifest: dict[str, Any],
    files: dict[str, Path],
    forbidden: list[str],
) -> None:
    runtime_contract = manifest.get("runtime")
    if not isinstance(runtime_contract, dict):
        raise ReleaseError("release manifest runtime contract is missing.")
    runtime_relative = _safe_relative(str(runtime_contract.get("manifest", "")))
    runtime_path = source_root / runtime_relative
    runtime = _read_json(runtime_path, "runtime manifest")
    platforms = runtime.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ReleaseError("runtime manifest must declare platforms.")
    declared_wheel_dirs = {_safe_relative(str(path)) for path in runtime_contract.get("wheel_directories", [])}
    wheel_extensions = {str(value).lower() for value in runtime_contract.get("wheel_extensions", [".whl"])}
    pdf = runtime_contract.get("required_pdf_package", {})
    pdf_name = str(pdf.get("name", "pypdfium2"))
    pdf_version = str(pdf.get("version", ""))

    for platform, raw_entry in sorted(platforms.items()):
        if not isinstance(raw_entry, dict):
            raise ReleaseError(f"runtime platform entry must be an object: {platform}")
        archive = raw_entry.get("archive")
        if not isinstance(archive, dict):
            raise ReleaseError(f"runtime archive declaration is missing for {platform}")
        archive_relative = _safe_relative(str(archive.get("path", "")))
        _add_file(files, source_root, archive_relative, forbidden)
        expected_hash = str(archive.get("sha256", "")).lower()
        if len(expected_hash) != 64 or _sha256_file(files[archive_relative]) != expected_hash:
            raise ReleaseError(f"runtime SHA-256 mismatch for {platform}: {archive_relative}")
        if not str(archive.get("url", "")).startswith("https://"):
            raise ReleaseError(f"runtime URL must use HTTPS for {platform}")

        wheel_relative = _safe_relative(str(raw_entry.get("wheels", "")))
        if wheel_relative not in declared_wheel_dirs:
            raise ReleaseError(f"runtime wheel directory is undeclared for {platform}: {wheel_relative}")
        wheel_dir = source_root / wheel_relative
        if wheel_dir.is_symlink() or not wheel_dir.is_dir():
            raise ReleaseError(f"runtime wheel directory is missing or unsafe for {platform}: {wheel_relative}")
        wheel_paths = sorted(path for path in wheel_dir.iterdir() if path.is_file())
        if any(path.suffix.lower() not in wheel_extensions for path in wheel_paths):
            raise ReleaseError(f"unexpected extension in runtime wheel directory for {platform}")
        required_prefix = f"{pdf_name.replace('-', '_')}-{pdf_version}-".lower()
        if not any(path.name.lower().startswith(required_prefix) for path in wheel_paths):
            raise ReleaseError(f"{pdf_name}=={pdf_version} wheel is missing for {platform}")
        for wheel in wheel_paths:
            _add_file(files, source_root, wheel.relative_to(source_root).as_posix(), forbidden)

        license_relative = _safe_relative(str(raw_entry.get("pdfium_license", "")))
        try:
            _add_file(files, source_root, license_relative, forbidden)
        except ReleaseError as exc:
            raise ReleaseError(f"PDFium license material is missing for {platform}: {exc}") from exc


def collect_release_files(source_root: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    source_root = source_root.resolve()
    forbidden = [str(value) for value in manifest.get("forbidden", [])]
    files: dict[str, Path] = {}
    for relative in manifest.get("required_files", []):
        _add_file(files, source_root, str(relative), forbidden)

    for tree in manifest.get("allowed_trees", []):
        if not isinstance(tree, dict):
            raise ReleaseError("allowed_trees entries must be objects.")
        relative = _safe_relative(str(tree.get("path", "")))
        directory = source_root / relative
        if directory.is_symlink():
            raise ReleaseError(f"symlinks are not allowed in an allowed tree: {relative}")
        if not directory.is_dir():
            raise ReleaseError(f"allowed tree is missing: {relative}")
        extensions = {str(value).lower() for value in tree.get("extensions", [])}
        found = 0
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ReleaseError(f"symlinks are not allowed in the release: {path.relative_to(source_root).as_posix()}")
            if not path.is_file():
                continue
            child = path.relative_to(source_root).as_posix()
            if path.suffix.lower() not in extensions:
                raise ReleaseError(f"unexpected extension in allowed tree {relative}: {child}")
            _add_file(files, source_root, child, forbidden)
            found += 1
        minimum = int(tree.get("minimum_files", 0))
        if found < minimum:
            raise ReleaseError(f"allowed tree {relative} requires at least {minimum} files; found {found}")

    _runtime_files(source_root, manifest, files, forbidden)
    for name in ("data/manifest.csv", "data/calibration.csv", "data/heldout.csv"):
        if name not in files:
            continue
        with files[name].open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"file", "release_allowed"} <= set(reader.fieldnames or []):
                raise ReleaseError(f"Dataset manifest lacks release provenance: {name}")
            licensed_pages = set()
            if "source_page_id" in (reader.fieldnames or []):
                index_name = "data/licenses/notices.json"
                if index_name not in files:
                    raise ReleaseError("Dataset license notice index is not staged.")
                index = _read_json(files[index_name], "dataset license notice index")
                notices = index.get("notices")
                if not isinstance(notices, list):
                    raise ReleaseError("Dataset license notice index is malformed.")
                for notice in notices:
                    if not isinstance(notice, dict):
                        raise ReleaseError("Dataset license notice entry is malformed.")
                    notice_file = notice.get("file", "")
                    if (not str(notice_file).startswith("data/licenses/") or notice_file not in files
                            or not notice.get("license") or not notice.get("source_page_id")
                            or hashlib.sha256(files[notice_file].read_bytes()).hexdigest() != notice.get("sha256")):
                        raise ReleaseError("Dataset license notice is missing or its hash differs.")
                    licensed_pages.add(notice["source_page_id"])
            for row in reader:
                if "source_page_id" in row and row["source_page_id"] not in licensed_pages:
                    raise ReleaseError("Dataset source page has no retained license notice.")
                target = row.get("file", "")
                if (row.get("release_allowed") != "true" or target not in files
                        or PurePosixPath(target).suffix.lower() not in {".png", ".jpg", ".jpeg"}):
                    raise ReleaseError(f"Dataset reference is not staged and approved: {name}: {target}")
    return files


def _transformed_bytes(path: Path, relative: str, transforms: list[dict[str, Any]]) -> tuple[bytes, int]:
    data = path.read_bytes()
    mode = 0o644
    suffix = PurePosixPath(relative).suffix.lower()
    for transform in transforms:
        if suffix not in {str(value).lower() for value in transform.get("extensions", [])}:
            continue
        newline = transform.get("newline")
        if newline:
            normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            data = normalized.replace(b"\n", b"\r\n") if newline == "crlf" else normalized
        if transform.get("archive_mode"):
            mode = int(str(transform["archive_mode"]), 8)
    return data, mode


def build_release(source_root: Path, output_dir: Path, *, timestamp: str | None = None) -> ReleaseResult:
    source_root = source_root.resolve()
    manifest_path = source_root / "packaging" / "release_manifest.json"
    manifest = _read_json(manifest_path, "release manifest")
    archive_root = _safe_relative(str(manifest.get("archive_root", "")))
    if "/" in archive_root:
        raise ReleaseError("archive_root must be one top-level directory name.")
    files = collect_release_files(source_root, manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or time.strftime("%Y%m%d_%H%M")
    destination = output_dir / f"ScanReady_{stamp}.zip"
    if destination.exists():
        raise ReleaseError(f"release output already exists: {destination}")
    partial = output_dir / f".{destination.name}.partial"
    if partial.exists():
        partial.unlink()
    transforms = list(manifest.get("text_transforms", []))
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, path in sorted(files.items()):
                data, mode = _transformed_bytes(path, relative, transforms)
                modified = time.localtime(path.stat().st_mtime)[:6]
                info = zipfile.ZipInfo(f"{archive_root}/{relative}", date_time=modified)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, data)
        max_bytes = int(manifest.get("max_zip_bytes", 0))
        actual_bytes = partial.stat().st_size
        if max_bytes <= 0 or actual_bytes > max_bytes:
            raise ReleaseError(f"release archive size {actual_bytes} exceeds the limit {max_bytes}")
        partial.rename(destination)
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise
    return ReleaseResult(
        path=destination,
        file_count=len(files),
        bytes=destination.stat().st_size,
        sha256=_sha256_file(destination),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the allowlist-only ScanReady release archive.")
    default_source = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--output", type=Path, default=default_source.parent / "dist")
    parser.add_argument("--timestamp")
    args = parser.parse_args(argv)
    try:
        result = build_release(args.source, args.output, timestamp=args.timestamp)
    except ReleaseError as exc:
        parser.exit(2, f"Release build failed: {exc}\n")
    print(f"Built: {result.path}")
    print(f"Files: {result.file_count}")
    print(f"Bytes: {result.bytes}")
    print(f"SHA-256: {result.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

