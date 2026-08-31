import tomllib
from pathlib import Path

import pytest

from waitlab import updates
from waitlab.updates import (
    ReleaseInfo,
    cleanup_download_directory,
    describe_update_error,
    version_tuple,
)


def test_runtime_version_comes_from_project_metadata():
    project = tomllib.loads(
        (Path(updates.__file__).resolve().parents[1] / "pyproject.toml")
        .read_text(encoding="utf-8")
    )

    from waitlab import __version__

    assert __version__ == project["project"]["version"]


def test_semantic_version_comparison_shape():
    assert version_tuple("v0.5.10") > version_tuple("0.5.2")


def test_semantic_version_handles_prerelease_labels():
    assert version_tuple("0.5.6") > version_tuple("0.5.6-rc.1")
    assert version_tuple("0.5.6-rc.2") > version_tuple("0.5.6-rc.1")


def test_update_error_describes_windows_timeout():
    message = describe_update_error(OSError("[WinError 10060] connection timed out"))

    assert "GitHub" in message
    assert "超时" in message
    assert "重试" in message


def test_update_error_does_not_expose_empty_exception():
    assert describe_update_error(RuntimeError()) == "更新失败，请稍后再试。"


def test_update_error_for_untrusted_installer_is_readable():
    message = describe_update_error(ValueError("Release installer URL is not trusted"))

    assert message == "更新文件格式或下载地址不可信。未安装本次更新。"


def test_large_download_retries_after_transient_timeout(monkeypatch, tmp_path):
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            if not self._read_once:
                return b""
            self._read_once = False
            return b"installer-bytes"

        _read_once = True

    def fake_urlopen(_request, timeout):
        nonlocal calls
        assert timeout == 30
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary timeout")
        return Response()

    monkeypatch.setattr(updates, "urlopen", fake_urlopen)
    monkeypatch.setattr(updates.time, "sleep", lambda _seconds: None)
    target = tmp_path / "WaitLAB-Setup-test.exe"

    updates._download_to_file("https://example.invalid/installer", target, timeout=30, attempts=2)

    assert calls == 2
    assert target.read_bytes() == b"installer-bytes"


def test_download_rejects_payload_over_configured_limit(monkeypatch, tmp_path):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            if self._sent:
                return b""
            self._sent = True
            return b"123456"

        _sent = False

    monkeypatch.setattr(updates, "MAX_INSTALLER_BYTES", 5)
    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response())
    target = tmp_path / "WaitLAB-Setup-too-large.exe"

    with pytest.raises(ValueError, match="大小限制"):
        updates._download_to_file("https://example.invalid/installer", target, timeout=30)

    assert not target.exists()


def test_cleanup_ignores_directories_outside_updater_namespace(tmp_path):
    target = tmp_path / "installer.exe"
    target.write_bytes(b"installer")

    cleanup_download_directory(target, delay_seconds=0)

    assert target.exists()


def test_release_assets_must_use_github_https_urls(monkeypatch):
    payload = {
        "tag_name": "v9.9.9",
        "assets": [
            {
                "name": "WaitLAB-Setup-9.9.9.exe",
                "browser_download_url": "https://evil.example/installer.exe",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://github.com/qza6677/WaitLAB/releases/download/v9.9.9/SHA256SUMS.txt",
            },
        ],
    }
    monkeypatch.setattr(updates, "_request_bytes", lambda *_args, **_kwargs: __import__("json").dumps(payload).encode())

    with pytest.raises(ValueError, match="not trusted"):
        updates.fetch_latest_release("0.5.13")


def test_verified_download_rejects_non_executable_payload(monkeypatch):
    release = ReleaseInfo(
        "9.9.9",
        "https://github.com/qza6677/WaitLAB/releases/download/v9.9.9/WaitLAB-Setup-9.9.9.exe",
        "https://github.com/qza6677/WaitLAB/releases/download/v9.9.9/SHA256SUMS.txt",
        "https://github.com/qza6677/WaitLAB/releases/tag/v9.9.9",
    )

    def fake_download(_url, target, **_kwargs):
        target.write_bytes(b"<!doctype html>")

    monkeypatch.setattr(updates, "_download_to_file", fake_download)
    with pytest.raises(ValueError, match="Windows"):
        updates.download_verified_installer(release)
