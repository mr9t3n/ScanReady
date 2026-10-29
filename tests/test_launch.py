from __future__ import annotations

import hashlib
import os
import socket
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scanready import cli
import scanready.launch as launch
from scanready.launch import (
    LaunchError,
    launch_app,
    requirements_sha256,
    runtime_is_ready,
    select_port,
    validate_installation,
    windows_path_warning,
)


@pytest.fixture
def writable_root(request: pytest.FixtureRequest) -> Path:
    parent = (Path(__file__).resolve().parent / ".launch-tmp").resolve()
    root = (parent / request.node.name).resolve()
    if parent not in root.parents:
        raise RuntimeError("Launcher test path escaped its controlled directory.")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    yield root
    if root.exists():
        shutil.rmtree(root)


class FakeProcess:
    def __init__(self, events: list[str], *, returncode: int | None = None) -> None:
        self.events = events
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.events.append("terminate")
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.events.append("wait")
        return self.returncode or 0

    def kill(self) -> None:
        self.events.append("kill")
        self.returncode = -9


def _complete_root(tmp_path: Path) -> Path:
    for relative in ("app.py", "requirements-lock.txt", "scanready/__init__.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    return tmp_path


def test_select_port_honors_a_free_requested_port() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    assert select_port(port) == port


def test_select_port_rejects_a_busy_requested_port() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        with pytest.raises(LaunchError, match="already in use"):
            select_port(port)


def test_launch_opens_browser_only_after_health_succeeds(writable_root: Path) -> None:
    root = _complete_root(writable_root)
    events: list[str] = []
    process = FakeProcess(events)

    def start_process(command: list[str], cwd: Path) -> FakeProcess:
        assert cwd == root
        assert command[-2:] == ["--server.headless", "true"]
        events.append("start")
        return process

    def health_check(url: str, child: FakeProcess, timeout: float) -> None:
        assert url.endswith("/_stcore/health")
        assert child is process
        events.append("healthy")

    def open_browser(url: str) -> bool:
        assert url.startswith("http://127.0.0.1:")
        events.append("browser")
        return True

    result = launch_app(
        root=root,
        port=8765,
        start_process=start_process,
        health_check=health_check,
        open_browser=open_browser,
    )

    assert result == 0
    assert events == ["start", "healthy", "browser", "wait"]


def test_launch_timeout_terminates_child_without_opening_browser(writable_root: Path) -> None:
    root = _complete_root(writable_root)
    events: list[str] = []
    process = FakeProcess(events)

    def start_process(command: list[str], cwd: Path) -> FakeProcess:
        events.append("start")
        return process

    def timed_out(url: str, child: FakeProcess, timeout: float) -> None:
        events.append("timeout")
        raise LaunchError("did not become healthy")

    def open_browser(url: str) -> bool:
        events.append("browser")
        return True

    with pytest.raises(LaunchError, match="did not become healthy"):
        launch_app(
            root=root,
            port=8766,
            start_process=start_process,
            health_check=timed_out,
            open_browser=open_browser,
        )

    assert events == ["start", "timeout", "terminate", "wait"]


def test_smoke_launch_stops_child_after_health(writable_root: Path) -> None:
    root = _complete_root(writable_root)
    events: list[str] = []
    process = FakeProcess(events)

    def start_process(command: list[str], cwd: Path) -> FakeProcess:
        events.append("start")
        return process

    def health_check(url: str, child: FakeProcess, timeout: float) -> None:
        events.append("healthy")

    result = launch_app(
        root=root,
        port=8767,
        smoke=True,
        start_process=start_process,
        health_check=health_check,
        open_browser=lambda url: events.append("browser") or True,
    )

    assert result == 0
    assert events == ["start", "healthy", "terminate", "wait"]


def test_windows_path_warning_starts_above_100_characters() -> None:
    assert windows_path_warning(Path("C:/short/ScanReady")) is None
    warning = windows_path_warning(Path("C:/" + "nested/" * 15 + "ScanReady"))
    assert warning is not None
    assert "shorter than 100 characters" in warning


def test_validate_installation_rejects_an_incomplete_extraction(writable_root: Path) -> None:
    (writable_root / "app.py").write_text("placeholder\n", encoding="utf-8")
    with pytest.raises(LaunchError, match="extract the complete ScanReady folder"):
        validate_installation(writable_root)


def test_runtime_reuse_requires_current_lock_hash(writable_root: Path) -> None:
    root = _complete_root(writable_root)
    ready = root / ".runtime" / "ready.txt"
    ready.parent.mkdir()
    ready.write_text("old-hash\n", encoding="utf-8")
    assert runtime_is_ready(root) is False

    ready.write_text(requirements_sha256(root / "requirements-lock.txt") + "\n", encoding="utf-8")
    assert runtime_is_ready(root) is True


def test_cli_routes_launch_options_to_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    def fake_launch_app(**options: object) -> int:
        received.update(options)
        return 0

    monkeypatch.setattr(cli, "launch_app", fake_launch_app, raising=False)

    result = cli.main(["launch", "--no-browser", "--smoke", "--port", "9123"])

    assert result == 0
    assert received == {"no_browser": True, "smoke": True, "port": 9123}


def test_cli_reports_launch_failure_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_launch(**options: object) -> int:
        raise LaunchError("health check timed out")

    monkeypatch.setattr(cli, "launch_app", fail_launch)

    result = cli.main(["launch", "--smoke"])

    assert result == 2
    assert capsys.readouterr().err == "ScanReady could not launch: health check timed out\n"


def test_dependency_lock_covers_every_direct_requirement() -> None:
    root = Path(__file__).resolve().parents[1]
    direct = {
        line.strip().lower()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    locked = {
        line.strip().lower()
        for line in (root / "requirements-lock.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert direct <= locked
    assert not any(line.startswith(("-e ", "git+", "http://", "https://")) for line in locked)


@pytest.mark.skipif(os.name != "nt" or not shutil.which("cmd.exe"), reason="Requires native Windows Python and cmd.exe")
def test_windows_dry_run_reports_plan_without_creating_runtime(writable_root: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    for relative in ("app.py", "requirements-lock.txt", "scanready/__init__.py", "scanready/launch.py"):
        destination = writable_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    shutil.copy2(source_root / "ScanReady-Windows.bat", writable_root / "ScanReady-Windows.bat")

    environment = os.environ.copy()
    environment["SCANREADY_PYTHON"] = sys.executable
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(writable_root / "ScanReady-Windows.bat"), "--dry-run", "--offline-only", "--check"],
        cwd=writable_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "mode=check" in completed.stdout
    assert "offline-only=true" in completed.stdout
    assert f"bootstrap-python={sys.executable}" in completed.stdout
    assert not (writable_root / ".runtime").exists()


@pytest.mark.skipif(os.name != "nt" or not shutil.which("cmd.exe"), reason="Requires native Windows Python and cmd.exe")
def test_windows_prefers_bundled_runtime_without_system_python(writable_root: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    for relative in (
        "app.py",
        "requirements-lock.txt",
        "scanready/__init__.py",
        "scanready/launch.py",
        "packaging/runtime.json",
    ):
        destination = writable_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    shutil.copy2(source_root / "ScanReady-Windows.bat", writable_root / "ScanReady-Windows.bat")
    runtime_manifest = __import__("json").loads(
        (writable_root / "packaging" / "runtime.json").read_text(encoding="utf-8")
    )
    archive_relative = runtime_manifest["platforms"]["windows-x64"]["archive"]["path"]
    archive = writable_root / archive_relative
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"test archive placeholder")
    (writable_root / "vendor" / "wheels" / "windows-x64").mkdir(parents=True)
    environment = os.environ.copy()
    environment["SCANREADY_PYTHON"] = str(writable_root / "missing-system-python.exe")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(writable_root / "ScanReady-Windows.bat"), "--dry-run", "--offline-only"],
        cwd=writable_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "runtime-source=bundled-archive" in completed.stdout
    assert "bootstrap-python=not-needed" in completed.stdout
    assert "action=extract-bundled-runtime" in completed.stdout
    assert not (writable_root / ".runtime").exists()


@pytest.mark.skipif(os.name != "nt" or not shutil.which("cmd.exe"), reason="Requires native Windows Python and cmd.exe")
def test_windows_entry_refuses_an_incomplete_extraction(writable_root: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = writable_root / "ScanReady-Windows.bat"
    shutil.copy2(root / "ScanReady-Windows.bat", script)

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(script), "--dry-run"],
        cwd=writable_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 2
    assert "extract the complete ScanReady folder" in completed.stdout


@pytest.mark.skipif(os.name != "nt" or not shutil.which("cmd.exe"), reason="Requires native Windows Python and cmd.exe")
def test_windows_dry_run_rebuilds_an_incompatible_partial_runtime(writable_root: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    for relative in ("app.py", "requirements-lock.txt", "scanready/__init__.py", "scanready/launch.py"):
        destination = writable_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    shutil.copy2(source_root / "ScanReady-Windows.bat", writable_root / "ScanReady-Windows.bat")
    runtime_python = writable_root / ".runtime" / "venv" / "Scripts" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_bytes(b"not a compatible Python runtime")
    (writable_root / ".runtime" / "ready.txt").write_text("stale\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["SCANREADY_PYTHON"] = sys.executable

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(writable_root / "ScanReady-Windows.bat"), "--dry-run"],
        cwd=writable_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "runtime-compatible=false" in completed.stdout
    assert "action=rebuild-runtime" in completed.stdout


@pytest.mark.skipif(os.name != "nt" or not shutil.which("cmd.exe"), reason="Requires native Windows Python and cmd.exe")
def test_windows_reuses_ready_runtime_without_a_bootstrap_python(writable_root: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    for relative in ("app.py", "requirements-lock.txt", "scanready/__init__.py", "scanready/launch.py"):
        destination = writable_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    shutil.copy2(source_root / "ScanReady-Windows.bat", writable_root / "ScanReady-Windows.bat")

    scripts = writable_root / ".runtime" / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(sys.executable, scripts / "python.exe")
    runtime_dll = Path(sys.executable).with_name("python311.dll")
    if runtime_dll.is_file():
        shutil.copy2(runtime_dll, scripts / runtime_dll.name)
    (writable_root / ".runtime" / "venv" / "pyvenv.cfg").write_text(
        f"home = {Path(sys.executable).parent}\n"
        "include-system-site-packages = false\n"
        f"version = {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n",
        encoding="utf-8",
    )
    lock_bytes = (writable_root / "requirements-lock.txt").read_bytes()
    ready_id = (
        hashlib.sha256(lock_bytes).hexdigest()
        + ":0dc3a404d4d6501f3b8f99d58354410a0b1699dd5c21070778a594db02070dd3\n"
    )
    (writable_root / ".runtime" / "ready.txt").write_text(ready_id, encoding="utf-8")
    environment = os.environ.copy()
    environment["SCANREADY_PYTHON"] = str(writable_root / "missing-bootstrap-python.exe")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(writable_root / "ScanReady-Windows.bat"), "--dry-run"],
        cwd=writable_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "runtime-ready=true" in completed.stdout
    assert "action=reuse-runtime" in completed.stdout


@pytest.mark.parametrize("script_name", ["ScanReady-macOS.command", "ScanReady-Linux.sh", "setup/install_and_run.sh"])
def test_unix_entry_script_has_valid_bash_syntax(script_name: str) -> None:
    bash = shutil.which("bash")
    if not bash or "windowsapps" in bash.lower():
        pytest.skip("Bash is unavailable")
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [bash, "-n", str(root / script_name)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if "no installed distributions" in (completed.stdout + completed.stderr).lower():
        pytest.skip("Windows Bash shim has no installed distribution")
    assert completed.returncode == 0, completed.stderr



@pytest.mark.skipif(os.name == "nt", reason="Requires a native Unix shell")
@pytest.mark.parametrize(
    ("system", "machine", "platform"),
    [("Linux", "x86_64", "linux-x86_64"), ("Darwin", "arm64", "macos-arm64"), ("Darwin", "x86_64", "macos-x86_64")],
)
def test_unix_dry_run_selects_platform_without_changing_runtime(
    tmp_path: Path, system: str, machine: str, platform: str
) -> None:
    source = Path(__file__).resolve().parents[1]
    for relative in ("app.py", "requirements-lock.txt", "scanready/__init__.py", "scanready/launch.py", "setup/install_and_run.sh"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system};; -m) echo {machine};; *) exit 1;; esac\n')
    uname.chmod(0o755)
    environment = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}
    completed = subprocess.run(
        ["bash", str(tmp_path / "setup/install_and_run.sh"), "--dry-run", "--offline-only", "--check"],
        env=environment, capture_output=True, text=True, timeout=15, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"platform={platform}" in completed.stdout
    assert "mode=check" in completed.stdout
    assert "offline-only=true" in completed.stdout
    assert not (tmp_path / ".runtime").exists()


def test_macos_browser_uses_system_open_with_complete_url(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(launch.sys, "platform", "darwin")
    monkeypatch.setattr(launch.subprocess, "run", lambda args, **kwargs: calls.append(args) or launch.subprocess.CompletedProcess(args, 0))
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: calls.append(["webbrowser", url]) or True)
    launch.open_browser_default("http://127.0.0.1:8501")
    assert calls == [["open", "http://127.0.0.1:8501"]]


def test_browser_falls_back_to_webbrowser_when_system_open_fails(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(launch.sys, "platform", "darwin")
    monkeypatch.setattr(launch.subprocess, "run", lambda args, **kwargs: launch.subprocess.CompletedProcess(args, 1))
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: calls.append(url) or True)
    launch.open_browser_default("http://127.0.0.1:8501")
    assert calls == ["http://127.0.0.1:8501"]
