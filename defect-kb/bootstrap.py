#!/usr/bin/env python3
"""Zero-dependency bootstrap: ensures venv + deps exist before running cli.py.

This script uses ONLY the Python standard library so it can run even when
no third-party packages (pyyaml, pydantic, chromadb, etc.) are installed.

Usage (replaces direct `python cli.py` calls):
    python bootstrap.py init --install-skills
    python bootstrap.py govern --json '...' --platform ios
    python bootstrap.py search --query "..." --embedding-provider local
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_CONFIG_NAME = "defect-kb.yaml"
_DATA_DIR_NAME = "defect-kb-data"
_VENV_DIR_NAME = ".venv"


def _find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for defect-kb.yaml. Returns None if not found."""
    current = start or Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / _CONFIG_NAME).exists():
            return parent
    return None


def _extract_project_root_arg(argv: list[str]) -> str | None:
    """Parse --project-root from argv without argparse (which may import deps)."""
    for i, arg in enumerate(argv):
        if arg == "--project-root" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--project-root="):
            return arg.split("=", 1)[1]
    return None


def _resolve_data_dir(argv: list[str]) -> Path:
    """Determine the defect-kb-data directory for venv placement."""
    explicit_root = _extract_project_root_arg(argv)

    if explicit_root:
        return Path(explicit_root) / _DATA_DIR_NAME

    found = _find_project_root()
    if found:
        return found / _DATA_DIR_NAME

    return Path.cwd() / _DATA_DIR_NAME


def _venv_python(venv_dir: Path) -> Path:
    """Return the python executable path inside a venv, platform-aware."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python3"


def _ensure_venv(data_dir: Path, req_file: Path) -> Path:
    """Create venv and install deps if missing. Return the venv python path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = data_dir / _VENV_DIR_NAME
    python_exe = _venv_python(venv_dir)
    stamp = venv_dir / ".deps-installed"

    if python_exe.exists() and stamp.exists():
        return python_exe

    if not python_exe.exists():
        print(f"[bootstrap] Creating virtual environment at {venv_dir} ...")
        subprocess.check_call(
            [sys.executable, "-m", "venv", str(venv_dir)],
            stdout=subprocess.DEVNULL,
        )

    print(f"[bootstrap] Installing dependencies from {req_file.name} ...")
    subprocess.check_call(
        [str(python_exe), "-m", "pip", "install", "-q", "--upgrade", "pip"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        [str(python_exe), "-m", "pip", "install", "-q", "-r", str(req_file)],
    )

    stamp.write_text(f"installed from {req_file.name}\n")
    print("[bootstrap] Dependencies installed successfully.")
    return python_exe


def main() -> None:
    cli_py = Path(__file__).resolve().parent / "cli.py"
    req_file = Path(__file__).resolve().parent / "requirements.txt"

    if not cli_py.exists():
        print(f"Error: cli.py not found at {cli_py}", file=sys.stderr)
        sys.exit(1)

    data_dir = _resolve_data_dir(sys.argv[1:])
    venv_python = _ensure_venv(data_dir, req_file)

    os.execv(str(venv_python), [str(venv_python), str(cli_py), *sys.argv[1:]])


if __name__ == "__main__":
    main()
