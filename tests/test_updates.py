from waitlab import updates
from waitlab.updates import version_tuple


def test_semantic_version_comparison_shape():
    assert version_tuple("v0.5.10") > version_tuple("0.5.2")


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
