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
    """Ensure remote directory path exists on FTP server and change into it."""
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


def discover_target_roots(ftp: FTP, preferred_root: str = DEFAULT_REMOTE_ROOT) -> list[str]:
    """Discover actual web root directories on InfinityFree.

    InfinityFree accounts may store files in:
    - /htdocs (default root for main domain)
    - /cyberpulseDA.wuaze.com/htdocs (domain-specific root)
    We detect all valid roots to ensure the website is published correctly.
    """
    roots: list[str] = []
    try:
        ftp.cwd("/")
        items = ftp.nlst()
        print(f"[*] Thu muc goc tren may chu FTP: {items}")

        for item in items:
            clean = item.strip("/")
            if "wuaze.com" in clean.lower() or "cyberpulse" in clean.lower():
                candidate = f"/{clean}/htdocs"
                try:
                    ftp.cwd(candidate)
                    if candidate not in roots:
                        roots.append(candidate)
                except Exception:
                    pass

        try:
            ftp.cwd("/htdocs")
            if "/htdocs" not in roots:
                roots.append("/htdocs")
        except Exception:
            pass
    except Exception as err:
        print(f"[-] Khong the quet thu muc goc: {err}")

    if not roots:
        roots = [preferred_root]

    print(f"[+] Thu muc dich trien khai: {roots}")
    return roots


def upload_portal(
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    password: str = "",
    dist_dir: Path = LOCAL_DIST_DIR,
    remote_root: str = DEFAULT_REMOTE_ROOT,
    rebuild: bool = True,
) -> int:
    """Recursively upload all files from dist_dir to FTP server, auto-building if needed."""
    if rebuild or not dist_dir.exists() or not any(dist_dir.iterdir()):
        print(f"[*] Dang tu dong bien dich CyberPulse Reader portal vao '{dist_dir}'...")
        try:
            from cyberpulse.portal import build_public_portal
            build_res = build_public_portal(output_dir=Path("output"), dist_dir=dist_dir)
            print(f"[+] Bien dich hoan tat: {build_res.editions_count} so bao, tao {len(build_res.files_created)} tep.")
        except Exception as e:
            print(f"[-] Khong the tu dong bien dich portal: {e}")
            if not dist_dir.exists():
                print(f"Loi: Thu muc '{dist_dir}' khong ton tai. Hay chay 'cyberpulse export portal' truoc.")
                return 1

    local_files: list[tuple[Path, str]] = []
    for file_path in sorted(dist_dir.glob("**/*")):
        if file_path.is_file():
            rel = file_path.relative_to(dist_dir).as_posix()
            local_files.append((file_path, rel))

    if not local_files:
        print(f"Loi: Khong tim thay tep nao trong '{dist_dir}'.")
        return 1

    print(f"[*] Dang ket noi toi may chu FTP {host}...")
    try:
        ftp = FTP(host=host, timeout=30)
        ftp.login(user=user, passwd=password)
        ftp.set_pasv(True)
        ftp.encoding = "utf-8"
        print(f"[+] Dang nhap thanh cong voi tai khoan: {user}")
    except Exception as e:
        print(f"[-] Loi ket noi FTP: {e}")
        return 2

    # Auto-discover target root directories (e.g. /htdocs and /cyberpulseDA.wuaze.com/htdocs)
    target_roots = discover_target_roots(ftp, preferred_root=remote_root)

    total_uploaded = 0
    for target_root in target_roots:
        print(f"\n[*] Dang trien khai toi thu muc: {target_root}")
        # Remove default InfinityFree index2.html/index2.htm to prevent holding page
        try:
            ftp.cwd(target_root)
            remote_items = ftp.nlst()
            for holding_page in ["index2.html", "index2.htm", "DEFAULT.html"]:
                if holding_page in remote_items:
                    try:
                        ftp.delete(holding_page)
                        print(f"  [+] Da xoa tep mac dinh {holding_page} tren hosting.")
                    except Exception:
                        pass
        except Exception:
            pass

        success_count = 0
        for idx, (local_path, rel_path) in enumerate(local_files, 1):
            rel_parts = rel_path.split("/")
            filename = rel_parts[-1]
            sub_dirs = rel_parts[:-1]

            if sub_dirs:
                target_dir = f"{target_root.rstrip('/')}/{'/'.join(sub_dirs)}"
            else:
                target_dir = target_root

            ensure_remote_dir(ftp, target_dir)

            size_kb = local_path.stat().st_size / 1024
            print(f"  [{idx}/{len(local_files)}] {rel_path} ({size_kb:.1f} KB)...", end=" ", flush=True)

            try:
                with open(local_path, "rb") as f:
                    ftp.storbinary(f"STOR {filename}", f)
                print("OK")
                success_count += 1
            except Exception as err:
                print(f"LOI: {err}")

        print(f"[+] Hoan tat tai len {success_count}/{len(local_files)} tep vao {target_root}.")
        total_uploaded += success_count

    try:
        ftp.quit()
    except Exception:
        pass

    print("=" * 60)
    print("🌐 Trang web cua ban da online tai:")
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
            pwd = getpass.getpass("Nhap mat khau FTP (InfinityFree Password): ")
        except (KeyboardInterrupt, EOFError):
            print("\nDa huy.")
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
