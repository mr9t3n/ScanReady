from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


def open_browser_default(url: str) -> None:
    """Open the app URL; macOS uses the system `open` command so Safari receives the full address."""
    if sys.platform == "darwin":
        try:
            if subprocess.run(["open", url], check=False, timeout=10).returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    webbrowser.open(url)


class LaunchError(RuntimeError):
    """Raised when ScanReady cannot start safely."""


class ChildProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


StartProcess = Callable[[list[str], Path], ChildProcess]
HealthCheck = Callable[[str, ChildProcess, float], None]
OpenBrowser = Callable[[str], bool]


REQUIRED_INSTALLATION_PATHS = (
    Path("app.py"),
    Path("requirements-lock.txt"),
    Path("scanready") / "__init__.py",
)


def select_port(requested: int | None = None) -> int:
    if requested is not None and not 1 <= requested <= 65535:
        raise LaunchError("The requested port must be between 1 and 65535.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind(("127.0.0.1", requested or 0))
        except OSError as exc:
            raise LaunchError(f"Port {requested} is already in use.") from exc
        return int(candidate.getsockname()[1])


def validate_installation(root: Path) -> None:
    missing = [str(path) for path in REQUIRED_INSTALLATION_PATHS if not (root / path).is_file()]
    if missing:
        details = ", ".join(missing)
        raise LaunchError(
            "ScanReady appears incomplete. Please extract the complete ScanReady folder "
            f"before running it. Missing: {details}"
        )


def windows_path_warning(root: Path) -> str | None:
    if len(str(root.resolve())) <= 100:
        return None
    return (
        "ScanReady is in a long folder path. For reliable setup on Windows, "
        "extract it to a path shorter than 100 characters, such as C:\\ScanReady."
    )


def requirements_sha256(lock_path: Path) -> str:
    if not lock_path.is_file():
        raise LaunchError(f"Dependency lock file is missing: {lock_path}")
    digest = hashlib.sha256()
    with lock_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_is_ready(root: Path) -> bool:
    ready_path = root / ".runtime" / "ready.txt"
    if not ready_path.is_file():
        return False
    try:
        recorded = ready_path.read_text(encoding="utf-8").strip()
        expected = requirements_sha256(root / "requirements-lock.txt")
    except (OSError, UnicodeError, LaunchError):
        return False
    return recorded == expected


def wait_for_health(url: str, process: ChildProcess, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "health endpoint did not respond"
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise LaunchError(f"ScanReady stopped before becoming healthy (exit code {returncode}).")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
                last_error = f"health endpoint returned HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise LaunchError(f"ScanReady did not become healthy within {timeout:g} seconds: {last_error}")


def _start_process(command: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command, cwd=cwd)


def _stop_process(process: ChildProcess) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def launch_app(
    *,
    root: Path | None = None,
    port: int | None = None,
    no_browser: bool = False,
    smoke: bool = False,
    timeout: float = 45.0,
    start_process: StartProcess = _start_process,
    health_check: HealthCheck = wait_for_health,
    open_browser: OpenBrowser = open_browser_default,
) -> int:
    app_root = (root or Path(__file__).resolve().parents[1]).resolve()
    validate_installation(app_root)
    chosen_port = select_port(port)
    app_url = f"http://127.0.0.1:{chosen_port}"
    health_url = f"{app_url}/_stcore/health"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_root / "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(chosen_port),
        "--server.headless",
        "true",
    ]
    process = start_process(command, app_root)
    try:
        health_check(health_url, process, timeout)
        print(f"ScanReady is healthy at {app_url}")
        if smoke:
            _stop_process(process)
            return 0
        if not no_browser:
            open_browser(app_url)
        return process.wait()
    except KeyboardInterrupt:
        _stop_process(process)
        return 0
    except BaseException:
        _stop_process(process)
        raise

