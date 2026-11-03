"""Unit tests for scripts/deploy_wuaze.py."""

from __future__ import annotations

from ftplib import error_perm, error_temp
from pathlib import Path
import socket
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure scripts folder is importable
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import deploy_wuaze  # noqa: E402


def test_sanitize_log() -> None:
    secret = "secret_vpanel_pass_123"
    msg = f"FTP 530 Login failed for user if0_43340192 with pass {secret}"
    sanitized = deploy_wuaze.sanitize_log(msg, secret)
    assert secret not in sanitized
    assert "********" in sanitized

    # Empty secret should return unchanged message
    assert deploy_wuaze.sanitize_log("normal message", "") == "normal message"


def test_ensure_dist_ready_already_exists(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html>CyberPulse</html>", encoding="utf-8")

    with patch("cyberpulse.portal.build_public_portal") as mock_build:
        ready = deploy_wuaze.ensure_dist_ready(dist_dir=dist_dir, force_rebuild=False)
        assert ready is True
        mock_build.assert_not_called()


def test_ensure_dist_ready_auto_build(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    mock_res = MagicMock()
    mock_res.editions_count = 5
    mock_res.files_created = ["index.html", "archive.html"]

    with patch("cyberpulse.portal.build_public_portal", return_value=mock_res) as mock_build:
        ready = deploy_wuaze.ensure_dist_ready(dist_dir=dist_dir, output_dir=output_dir, force_rebuild=False)
        assert ready is True
        mock_build.assert_called_once_with(output_dir=output_dir, dist_dir=dist_dir)


def test_ensure_dist_ready_build_failure(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    output_dir = tmp_path / "output"

    with patch("cyberpulse.portal.build_public_portal", side_effect=RuntimeError("Build error")):
        ready = deploy_wuaze.ensure_dist_ready(dist_dir=dist_dir, output_dir=output_dir, force_rebuild=False)
        assert ready is False


def test_connect_ftp_plain_success() -> None:
    mock_ftp = MagicMock()
    with patch("deploy_wuaze.FTP", return_value=mock_ftp) as mock_ftp_cls:
        client, code, err = deploy_wuaze.connect_ftp(
            host="ftpupload.net",
            user="if0_test",
            password="pwd",
            use_tls=False,
            max_retries=1,
        )
        assert client is mock_ftp
        assert code == deploy_wuaze.EXIT_SUCCESS
        assert err == ""
        mock_ftp_cls.assert_called_once_with(timeout=30)
        mock_ftp.connect.assert_called_once_with(host="ftpupload.net", port=21, timeout=30)
        mock_ftp.login.assert_called_once_with(user="if0_test", passwd="pwd")
        mock_ftp.set_pasv.assert_called_once_with(True)
        assert mock_ftp.encoding == "utf-8"


def test_connect_ftp_tls_success() -> None:
    mock_tls = MagicMock()
    with patch("deploy_wuaze.FTP_TLS", return_value=mock_tls) as mock_tls_cls:
        client, code, err = deploy_wuaze.connect_ftp(
            host="ftpupload.net",
            user="if0_test",
            password="pwd",
            use_tls=True,
            max_retries=1,
        )
        assert client is mock_tls
        assert code == deploy_wuaze.EXIT_SUCCESS
        mock_tls_cls.assert_called_once_with(timeout=30)
        mock_tls.prot_p.assert_called_once()
        mock_tls.set_pasv.assert_called_once_with(True)


def test_connect_ftp_auth_failure_530() -> None:
    mock_ftp = MagicMock()
    mock_ftp.login.side_effect = error_perm("530 Login authentication failed")

    with patch("deploy_wuaze.FTP", return_value=mock_ftp):
        client, code, err = deploy_wuaze.connect_ftp(
            host="ftpupload.net",
            user="if0_test",
            password="wrong_password",
            use_tls=False,
            max_retries=3,
        )
        assert client is None
        assert code == deploy_wuaze.EXIT_AUTH_FAILED
        assert "530" in err
        # Auth errors should terminate immediately without repeated retries
        assert mock_ftp.login.call_count == 1


def test_connect_ftp_tls_fallback_plain() -> None:
    mock_tls = MagicMock()
    mock_tls.login.side_effect = error_perm("500 AUTH TLS not understood")

    mock_plain = MagicMock()

    def ftp_factory(timeout: int = 30) -> MagicMock:
        return mock_plain

    with patch("deploy_wuaze.FTP_TLS", return_value=mock_tls), patch("deploy_wuaze.FTP", side_effect=ftp_factory):
        client, code, err = deploy_wuaze.connect_ftp(
            host="ftpupload.net",
            user="if0_test",
            password="pwd",
            use_tls=True,
            fallback_plain=True,
            max_retries=2,
        )
        assert client is mock_plain
        assert code == deploy_wuaze.EXIT_SUCCESS


def test_connect_ftp_transient_retry_success() -> None:
    mock_ftp = MagicMock()
    # Fail on first attempt with timeout, succeed on second attempt
    mock_ftp.connect.side_effect = [socket.timeout("Connection timed out"), None]

    with patch("deploy_wuaze.FTP", return_value=mock_ftp), patch("time.sleep") as mock_sleep:
        client, code, err = deploy_wuaze.connect_ftp(
            host="ftpupload.net",
            user="if0_test",
            password="pwd",
            max_retries=3,
            initial_delay=1.0,
            backoff_factor=2.0,
        )
        assert client is mock_ftp
        assert code == deploy_wuaze.EXIT_SUCCESS
        assert mock_ftp.connect.call_count == 2
        mock_sleep.assert_called_once_with(1.0)


def test_connect_ftp_retry_exhausted() -> None:
    mock_ftp = MagicMock()
    mock_ftp.connect.side_effect = socket.timeout("Timed out")

    with patch("deploy_wuaze.FTP", return_value=mock_ftp), patch("time.sleep"):
        client, code, err = deploy_wuaze.connect_ftp(
            host="ftpupload.net",
            user="if0_test",
            password="pwd",
            max_retries=3,
            initial_delay=0.1,
        )
        assert client is None
        assert code == deploy_wuaze.EXIT_CONNECTION_ERROR
        assert mock_ftp.connect.call_count == 3


def test_ensure_remote_dir() -> None:
    mock_ftp = MagicMock()
    cwd_calls: list[str] = []

    def mock_cwd(path: str) -> None:
        if path == "/htdocs/articles" and "/htdocs/articles" not in cwd_calls:
            # Simulate first attempt failing because dir does not exist yet
            cwd_calls.append(path)
            raise error_perm("550 Directory not found")
        cwd_calls.append(path)

    mock_ftp.cwd.side_effect = mock_cwd

    deploy_wuaze.ensure_remote_dir(mock_ftp, "/htdocs/articles/2026")
    mock_ftp.mkd.assert_called()


def test_discover_target_roots() -> None:
    mock_ftp = MagicMock()
    mock_ftp.nlst.return_value = ["cyberpulseDA.wuaze.com", "logs", "htdocs"]

    # cwd should succeed for the candidates
    mock_ftp.cwd.return_value = None

    roots = deploy_wuaze.discover_target_roots(mock_ftp)
    assert "/cyberpulseDA.wuaze.com/htdocs" in roots
    assert "/htdocs" in roots


def test_upload_file_with_retry_success(tmp_path: Path) -> None:
    test_file = tmp_path / "test.html"
    test_file.write_text("hello", encoding="utf-8")

    mock_ftp = MagicMock()
    success = deploy_wuaze.upload_file_with_retry(mock_ftp, test_file, "test.html", max_retries=2)
    assert success is True
    mock_ftp.storbinary.assert_called_once()


def test_upload_file_with_retry_recover(tmp_path: Path) -> None:
    test_file = tmp_path / "test.html"
    test_file.write_text("hello", encoding="utf-8")

    mock_ftp = MagicMock()
    mock_ftp.storbinary.side_effect = [ConnectionResetError("Reset"), None]

    with patch("time.sleep"):
        success = deploy_wuaze.upload_file_with_retry(mock_ftp, test_file, "test.html", max_retries=2)
        assert success is True
        assert mock_ftp.storbinary.call_count == 2


def test_upload_portal_empty_dist(tmp_path: Path) -> None:
    empty_dist = tmp_path / "empty_dist"
    empty_dist.mkdir()

    with patch("deploy_wuaze.ensure_dist_ready", return_value=False):
        code = deploy_wuaze.upload_portal(dist_dir=empty_dist)
        assert code == deploy_wuaze.EXIT_LOCAL_ERROR


def test_upload_portal_full_flow(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<h1>CyberPulse</h1>", encoding="utf-8")
    sub = dist_dir / "assets"
    sub.mkdir()
    (sub / "style.css").write_text("body {}", encoding="utf-8")

    mock_ftp = MagicMock()
    mock_ftp.nlst.return_value = ["index2.html", "other.html"]

    with patch("deploy_wuaze.ensure_dist_ready", return_value=True), \
         patch("deploy_wuaze.connect_ftp", return_value=(mock_ftp, deploy_wuaze.EXIT_SUCCESS, "")), \
         patch("deploy_wuaze.discover_target_roots", return_value=["/htdocs"]), \
         patch("deploy_wuaze.upload_file_with_retry", return_value=True) as mock_upload_file:

        code = deploy_wuaze.upload_portal(dist_dir=dist_dir)
        assert code == deploy_wuaze.EXIT_SUCCESS
        # Check that holding page index2.html was deleted
        mock_ftp.delete.assert_called_with("index2.html")
        # Check that 2 files were uploaded
        assert mock_upload_file.call_count == 2
        mock_ftp.quit.assert_called_once()


def test_main_cli_with_env_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FTP_PASSWORD", "env_secret_123")

    with patch("sys.argv", ["deploy_wuaze.py", "--dist-dir", "dist"]), \
         patch("deploy_wuaze.upload_portal", return_value=0) as mock_upload, \
         patch("sys.exit") as mock_exit:

        deploy_wuaze.main()
        mock_upload.assert_called_once()
        args, kwargs = mock_upload.call_args
        assert kwargs["password"] == "env_secret_123"
        mock_exit.assert_called_once_with(0)


def test_main_cli_missing_password_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FTP_PASSWORD", raising=False)

    with patch("sys.argv", ["deploy_wuaze.py"]), \
         patch("sys.stdin.isatty", return_value=False), \
         patch("sys.exit") as mock_exit:

        deploy_wuaze.main()
        mock_exit.assert_called_once_with(deploy_wuaze.EXIT_AUTH_FAILED)
