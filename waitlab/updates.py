from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RELEASE_API = "https://api.github.com/repos/qza6677/WaitLAB/releases/latest"
USER_AGENT = "WaitLAB"
RETRY_COUNT = 3
RETRY_DELAYS = (2, 4)
CHUNK_SIZE = 1024 * 1024


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.strip().lstrip("v").split("."))


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    installer_url: str
    checksums_url: str
    page_url: str


def _request_bytes(url: str, *, timeout: int, attempts: int = RETRY_COUNT) -> bytes:
    """Read a small GitHub response with retries for transient network errors."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            # Retry only transient server failures; a missing asset should fail fast.
            if exc.code < 500:
                raise
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    raise TimeoutError(
        f"连接 GitHub 超时或失败，已重试 {attempts} 次：{last_error}"
    ) from last_error


def _download_to_file(
    url: str,
    target: Path,
    *,
    timeout: int,
    attempts: int = RETRY_COUNT,
) -> None:
    """Stream a large download to disk and restart cleanly after a timeout."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        target.unlink(missing_ok=True)
        request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response, target.open("wb") as output:
                while chunk := response.read(CHUNK_SIZE):
                    output.write(chunk)
            return
        except HTTPError as exc:
            if exc.code < 500:
                raise
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    target.unlink(missing_ok=True)
    raise TimeoutError(
        f"下载安装包超时或失败，已重试 {attempts} 次：{last_error}"
    ) from last_error


def fetch_latest_release(current_version: str, timeout: int = 15) -> ReleaseInfo | None:
    payload = json.loads(_request_bytes(RELEASE_API, timeout=timeout).decode("utf-8"))
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
    try:
        _download_to_file(release.installer_url, target, timeout=300)
        manifest = _request_bytes(release.checksums_url, timeout=30).decode("ascii")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    expected = next((line.split()[0].upper() for line in manifest.splitlines() if target.name in line), None)
    actual = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    if not expected or actual != expected:
        target.unlink(missing_ok=True)
        raise ValueError("更新包 SHA-256 校验失败")
    return target


def launch_installer(path: Path) -> None:
    subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])
