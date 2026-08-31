"""WaitLAB desktop companion."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import tomllib


def _read_project_version() -> str:
    """Read the project version in source and bundled applications alike."""

    project_files = (
        Path(__file__).resolve().parents[1] / "pyproject.toml",
        Path(__file__).resolve().parent / "pyproject.toml",
    )
    for project_file in project_files:
        try:
            with project_file.open("rb") as stream:
                project = tomllib.load(stream)
            value = project.get("project", {}).get("version")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (OSError, tomllib.TOMLDecodeError):
            continue
    try:
        return package_version("waitlab")
    except PackageNotFoundError:
        # This fallback is only for unusual source layouts where neither the
        # project file nor installed package metadata is available.
        return "0.5.17"


__version__ = _read_project_version()
