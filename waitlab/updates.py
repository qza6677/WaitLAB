from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

RELEASE_API = "https://api.github.com/repos/qza6677/WaitLAB/releases/latest"


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.strip().lstrip("v").split("."))


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    installer_url: str
    checksums_url: str
    page_url: str


def fetch_latest_release(current_version: str, timeout: int = 5) -> ReleaseInfo | None:
    request = Request(RELEASE_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "WaitLAB"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    version = str(payload.get("tag_name", "")).lstrip("v")
    if not version or version_tuple(version) <= version_tuple(current_version):
        return None
    assets = {asset["name"]: asset["browser_download_url"] for asset in payload.get("assets", [])}
    installer_name = f"WaitLAB-Setup-{version}.exe"
    if installer_name not in assets or "SHA256SUMS.txt" not in assets:
        raise ValueError("Release 缺少安装包或 SHA256SUMS.txt")
    return ReleaseInfo(version, assets[installer_name], assets["SHA256SUMS.txt"], str(payload.get("html_url", "")))


def download_verified_installer(release: ReleaseInfo) -> Path:
    target = Path(tempfile.mkdtemp(prefix="waitlab-update-")) / f"WaitLAB-Setup-{release.version}.exe"
    with urlopen(release.installer_url, timeout=60) as response:
        target.write_bytes(response.read())
    with urlopen(release.checksums_url, timeout=10) as response:
        manifest = response.read().decode("ascii")
    expected = next((line.split()[0].upper() for line in manifest.splitlines() if target.name in line), None)
    actual = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    if not expected or actual != expected:
        target.unlink(missing_ok=True)
        raise ValueError("更新包 SHA-256 校验失败")
    return target


def launch_installer(path: Path) -> None:
    subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])
