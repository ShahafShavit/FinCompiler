"""
Stop anything listening on the finance control HTTP port, then start ``python -m web_control``.

Use from VS Code (or CLI) so the same command both starts the dashboard and replaces a prior instance.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _repo_venv_python(repo: Path) -> Path | None:
    """``install.ps1`` creates ``.venv``; some setups use ``venv``."""
    if sys.platform == "win32":
        for name in (".venv", "venv"):
            candidate = repo / name / "Scripts" / "python.exe"
            if candidate.is_file():
                return candidate
    else:
        for name in (".venv", "venv"):
            candidate = repo / name / "bin" / "python"
            if candidate.is_file():
                return candidate
    return None


def python_for_web_control_child(repo: Path) -> str:
    """
    Interpreter used to run ``python -m web_control``.

    Prefers ``FINANCE_PYTHON_EXE``, an active ``VIRTUAL_ENV``, then a repo ``.venv`` / ``venv``,
    so the server sees the same packages as ``install.ps1`` even when the script was launched with
    a different global ``python``.
    """
    override = (os.environ.get("FINANCE_PYTHON_EXE") or "").strip()
    if override:
        return override

    venv_root = (os.environ.get("VIRTUAL_ENV") or "").strip()
    if venv_root:
        if sys.platform == "win32":
            from_venv = Path(venv_root) / "Scripts" / "python.exe"
        else:
            from_venv = Path(venv_root) / "bin" / "python"
        if from_venv.is_file():
            return str(from_venv.resolve())

    repo_venv = _repo_venv_python(repo)
    if repo_venv is not None:
        return str(repo_venv.resolve())

    return sys.executable


def _control_port() -> int:
    import config  # noqa: E402 — after sys.path

    return int(getattr(config, "control_http_port", 8780))


def _kill_listeners_windows(port: int) -> None:
    ps = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue; "
        "if ($null -ne $c) { "
        "$c | ForEach-Object { "
        "Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        cwd=str(_ROOT),
        capture_output=True,
        timeout=30,
    )


def _kill_listeners_posix(port: int) -> None:
    try:
        r = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        r = subprocess.run(
            ["fuser", "-k", f"{port}/tcp"],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return

    if r.returncode != 0:
        return
    for line in r.stdout.strip().splitlines():
        pid_s = line.strip()
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(0.2)
    for line in r.stdout.strip().splitlines():
        pid_s = line.strip()
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def kill_listeners_on_control_port(port: int) -> None:
    if sys.platform == "win32":
        _kill_listeners_windows(port)
    else:
        _kill_listeners_posix(port)


def main() -> int:
    port = _control_port()
    py = python_for_web_control_child(_ROOT)
    print(f"Finance control: freeing port {port} (if in use), then starting web_control…", flush=True)
    print(f"Using Python: {py}", flush=True)
    kill_listeners_on_control_port(port)
    time.sleep(0.3)
    os.chdir(_ROOT)
    rc = subprocess.run([py, "-m", "web_control"]).returncode
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
