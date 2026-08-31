from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sys
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

RELEASE_API = "https://api.github.com/repos/qza6677/WaitLAB/releases/latest"
USER_AGENT = "WaitLAB"
RETRY_COUNT = 3
RETRY_DELAYS = (2, 4)
CHUNK_SIZE = 1024 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
TRUSTED_RELEASE_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def _validate_release_url(value: str, *, label: str) -> str:
    """Accept only HTTPS URLs served by GitHub's release infrastructure."""

    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or (
        host not in TRUSTED_RELEASE_HOSTS and not host.endswith(".githubusercontent.com")
    ):
        raise ValueError(f"Release {label} URL is not trusted")
    return value


def version_tuple(value: str) -> tuple[object, ...]:
    """Return a comparable key for stable and pre-release SemVer labels."""

    normalized = value.strip().lstrip("v")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", normalized)
    if match is None:
        raise ValueError(f"无效的版本号：{value}")
    major, minor, patch, prerelease = match.groups()
    if prerelease is None:
        return (int(major), int(minor), int(patch), 1, ())
    parts: list[tuple[int, object]] = []
    for part in prerelease.split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part.casefold()))
    return (int(major), int(minor), int(patch), 0, tuple(parts))


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    installer_url: str
    checksums_url: str
    page_url: str


def describe_update_error(error: BaseException) -> str:
    """Turn low-level updater failures into short, actionable UI messages."""
    text = str(error).strip()
    lowered = text.lower()
    if "10060" in text or "timed out" in lowered or "timeout" in lowered:
        return "无法连接 GitHub，网络请求超时；已自动重试，请稍后再试。"
    if "sha-256" in lowered or "sha256" in lowered:
        return "更新包校验失败，未安装本次更新。"
    if "not trusted" in lowered or ("windows" in lowered and "installer" in lowered):
        return "更新文件格式或下载地址不可信。未安装本次更新。"
    if "403" in text or "429" in text:
        return "GitHub 暂时限制了请求，请稍后再试。"
    if not text:
        return "更新失败，请稍后再试。"
    return f"更新失败：{text}"


def _request_bytes(url: str, *, timeout: int, attempts: int = RETRY_COUNT) -> bytes:
    """Read a small GitHub response with retries for transient network errors."""
    attempts = max(1, int(attempts))
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
    attempts = max(1, int(attempts))
    last_error: Exception | None = None
    for attempt in range(attempts):
        target.unlink(missing_ok=True)
        request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response, target.open("wb") as output:
                written = 0
                while chunk := response.read(CHUNK_SIZE):
                    written += len(chunk)
                    if written > MAX_INSTALLER_BYTES:
                        raise ValueError("更新包超过允许的大小限制")
                    output.write(chunk)
            return
        except HTTPError as exc:
            if exc.code < 500:
                raise
            last_error = exc
        except ValueError:
            target.unlink(missing_ok=True)
            raise
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    target.unlink(missing_ok=True)
    raise TimeoutError(
        f"下载安装包超时或失败，已重试 {attempts} 次：{last_error}"
    ) from last_error


def fetch_latest_release(current_version: str, timeout: int = 15) -> ReleaseInfo | None:
    try:
        payload = json.loads(_request_bytes(RELEASE_API, timeout=timeout).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub 返回了无效的更新响应") from exc
    if not isinstance(payload, dict):
        raise ValueError("GitHub 返回了无效的更新响应")
    version = str(payload.get("tag_name", "")).lstrip("v")
    if not version or version_tuple(version) <= version_tuple(current_version):
        return None
    raw_assets = payload.get("assets", [])
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub 返回了无效的更新资源列表")
    assets = {
        asset["name"]: asset["browser_download_url"]
        for asset in raw_assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and isinstance(asset.get("browser_download_url"), str)
    }
    installer_name = f"WaitLAB-Setup-{version}.exe"
    if installer_name not in assets or "SHA256SUMS.txt" not in assets:
        raise ValueError("Release 缺少安装包或 SHA256SUMS.txt")
    return ReleaseInfo(
        version,
        _validate_release_url(assets[installer_name], label="installer"),
        _validate_release_url(assets["SHA256SUMS.txt"], label="checksums"),
        str(payload.get("html_url", "")),
    )


def _validate_installer_file(path: Path) -> None:
    """Reject an HTML/error response masquerading as an installer."""

    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("下载的更新文件不是有效的 Windows 安装程序")


def download_verified_installer(release: ReleaseInfo) -> Path:
    _validate_release_url(release.installer_url, label="installer")
    _validate_release_url(release.checksums_url, label="checksums")
    target = Path(tempfile.mkdtemp(prefix="waitlab-update-")) / f"WaitLAB-Setup-{release.version}.exe"
    try:
        _download_to_file(release.installer_url, target, timeout=300)
        _validate_installer_file(target)
        manifest = _request_bytes(release.checksums_url, timeout=30).decode("ascii")
    except Exception:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise
    expected = None
    for line in manifest.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        filename = fields[-1].lstrip("*")
        if Path(filename).name == target.name:
            expected = fields[0].upper()
            break
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    actual = digest.hexdigest().upper()
    if not expected or actual != expected:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise ValueError("更新包 SHA-256 校验失败")
    return target


def launch_installer(path: Path) -> None:
    subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])


def cleanup_download_directory(path: Path, delay_seconds: float = 3.0) -> None:
    """Remove an updater's temporary directory after the installer exits."""

    parent = path.resolve().parent
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        parent.relative_to(temp_root)
    except ValueError:
        # Only remove directories created by ``download_verified_installer``.
        return
    if not parent.name.startswith("waitlab-update-"):
        return

    delay = max(0.0, float(delay_seconds))

    if sys.platform == "win32":
        # The application exits immediately after launching the installer, so
        # an in-process daemon thread is not reliable.  Detach a hidden cmd
        # process that waits for the installer to release the file and then
        # removes the directory.
        seconds = int(math.ceil(delay))
        wait_command = (
            f'timeout /t {seconds} /nobreak >nul & '
            f'rmdir /s /q "{parent}"'
        )
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        creation_flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", wait_command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creation_flags,
            )
            return
        except (OSError, ValueError):
            # Fall back to the thread below if cmd.exe cannot be started.
            pass

    def worker() -> None:
        # Give Windows time to spawn the installer before removing its parent.
        time.sleep(delay)
        for _ in range(10):
            try:
                shutil.rmtree(parent, ignore_errors=False)
                return
            except OSError:
                time.sleep(1.0)

    Thread(target=worker, daemon=True, name="waitlab-update-cleanup").start()
