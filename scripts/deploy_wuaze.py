"""Deploy CyberPulse static reader portal to InfinityFree (.wuaze.com) via FTP."""

from __future__ import annotations

import argparse
from ftplib import FTP, error_perm
import getpass
import os
from pathlib import Path
import sys

DEFAULT_HOST = "ftpupload.net"
DEFAULT_USER = "if0_43340192"
DEFAULT_REMOTE_ROOT = "/htdocs"
LOCAL_DIST_DIR = Path("dist")


def ensure_remote_dir(ftp: FTP, remote_path: str) -> None:
    """Ensure remote directory path exists on FTP server."""
    parts = [p for p in remote_path.strip("/").split("/") if p]
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        try:
            ftp.cwd(current)
        except error_perm:
            try:
                ftp.mkd(current)
                ftp.cwd(current)
            except error_perm:
                pass


def upload_portal(
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    password: str = "",
    dist_dir: Path = LOCAL_DIST_DIR,
    remote_root: str = DEFAULT_REMOTE_ROOT,
) -> int:
    """Recursively upload all files from dist_dir to FTP server."""
    if not dist_dir.exists():
        print(f"Lỗi: Thư mục nguồn '{dist_dir}' không tồn tại. Hãy chạy 'cyberpulse export portal' trước.")
        return 1

    files_to_upload: list[tuple[Path, str]] = []
    for file_path in sorted(dist_dir.glob("**/*")):
        if file_path.is_file():
            rel = file_path.relative_to(dist_dir).as_posix()
            remote_file = f"{remote_root.rstrip('/')}/{rel}"
            files_to_upload.append((file_path, remote_file))

    if not files_to_upload:
        print(f"Lỗi: Không tìm thấy tệp nào trong '{dist_dir}'.")
        return 1

    print(f"[*] Đang kết nối tới máy chủ FTP {host}...")
    try:
        ftp = FTP(host=host, timeout=30)
        ftp.login(user=user, passwd=password)
        ftp.set_pasv(True)
        ftp.encoding = "utf-8"
        print(f"[+] Đăng nhập thành công với tài khoản: {user}")
    except Exception as e:
        print(f"[-] Lỗi kết nối FTP: {e}")
        return 2

    # Remove default InfinityFree index2.html if it exists to avoid conflicts
    try:
        ftp.cwd(remote_root)
        files = ftp.nlst()
        if "index2.html" in files:
            ftp.delete(f"{remote_root}/index2.html")
            print("[+] Đã xóa tệp mặc định index2.html trên hosting.")
    except Exception:
        pass

    print(f"[*] Bắt đầu tải lên {len(files_to_upload)} tệp tin...")
    success_count = 0

    for idx, (local_path, remote_file) in enumerate(files_to_upload, 1):
        remote_dir = "/".join(remote_file.split("/")[:-1])
        ensure_remote_dir(ftp, remote_dir)

        size_kb = local_path.stat().st_size / 1024
        print(f"  [{idx}/{len(files_to_upload)}] {local_path.relative_to(dist_dir).as_posix()} ({size_kb:.1f} KB)...", end=" ", flush=True)

        try:
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_file}", f)
            print("Xong!")
            success_count += 1
        except Exception as err:
            print(f"LỖI: {err}")

    try:
        ftp.quit()
    except Exception:
        pass

    print(f"\n[+] Tải lên hoàn tất: {success_count}/{len(files_to_upload)} tệp tin.")
    print("=" * 60)
    print("🌐 Trang web của bạn đã online tại:")
    print("   👉 http://cyberpulseDA.wuaze.com/")
    print("   👉 https://cyberpulseDA.wuaze.com/")
    print("=" * 60)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload CyberPulse Portal to InfinityFree via FTP")
    parser.add_argument("--host", default=DEFAULT_HOST, help="FTP Hostname (default: ftpupload.net)")
    parser.add_argument("--user", default=DEFAULT_USER, help="FTP Username (default: if0_43340192)")
    parser.add_argument("--password", default="", help="FTP Password")
    parser.add_argument("--dist-dir", type=Path, default=LOCAL_DIST_DIR, help="Local dist directory")
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_ROOT, help="Remote htdocs directory")
    args = parser.parse_args()

    pwd = args.password or os.environ.get("FTP_PASSWORD", "")
    if not pwd:
        try:
            pwd = getpass.getpass("Nhập mật khẩu FTP (InfinityFree Password): ")
        except (KeyboardInterrupt, EOFError):
            print("\nĐã hủy.")
            sys.exit(1)

    code = upload_portal(
        host=args.host,
        user=args.user,
        password=pwd,
        dist_dir=args.dist_dir,
        remote_root=args.remote_dir,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
