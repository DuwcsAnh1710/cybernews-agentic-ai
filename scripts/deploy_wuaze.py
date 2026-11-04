"""Deploy CyberPulse static reader portal to InfinityFree (.wuaze.com) via FTP."""

from __future__ import annotations

import argparse
from ftplib import FTP, FTP_TLS, error_perm, error_temp
import getpass
import os
from pathlib import Path
import socket
import sys
import time

DEFAULT_HOST = "ftpupload.net"
DEFAULT_USER = "if0_43340192"
DEFAULT_REMOTE_ROOT = "/htdocs"
LOCAL_DIST_DIR = Path("dist")
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
BACKOFF_FACTOR = 2.0

# Standard exit codes
EXIT_SUCCESS = 0
EXIT_LOCAL_ERROR = 1
EXIT_AUTH_FAILED = 2
EXIT_CONNECTION_ERROR = 3
EXIT_UPLOAD_ERROR = 4

# Prohibited file types per InfinityFree terms of service (strict ban on hosting video & audio files)
FORBIDDEN_MEDIA_EXTENSIONS: set[str] = {
    ".mp4",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".flac",
    ".aac",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",
    ".wmv",
    ".m4v",
}


def is_forbidden_media_file(file_path: Path) -> bool:
    """Check if file is a video or audio binary prohibited on InfinityFree hosting."""
    return file_path.suffix.lower() in FORBIDDEN_MEDIA_EXTENSIONS


def sanitize_log(msg: str, secret: str = "") -> str:
    """Mask sensitive secrets from log strings."""
    if not secret or not isinstance(msg, str):
        return msg
    return msg.replace(secret, "********")


def ensure_dist_ready(
    dist_dir: Path = LOCAL_DIST_DIR,
    output_dir: Path = Path("output"),
    force_rebuild: bool = False,
) -> bool:
    """Ensure local portal dist directory is compiled with HTML files."""
    has_files = dist_dir.exists() and any(dist_dir.iterdir()) and (dist_dir / "index.html").exists()
    if force_rebuild or not has_files:
        print(f"[*] Dang tu dong bien dich CyberPulse Reader portal vao '{dist_dir}'...")
        try:
            from cyberpulse.portal import build_public_portal
            build_res = build_public_portal(output_dir=output_dir, dist_dir=dist_dir)
            print(f"[+] Bien dich hoan tat: {build_res.editions_count} so bao, tao {len(build_res.files_created)} tep.")
            return True
        except Exception as e:
            print(f"[-] Khong the tu dong bien dich portal: {e}")
            return False
    return True


def ensure_remote_dir(ftp: FTP, remote_path: str) -> None:
    """Ensure remote directory path exists on FTP server and change into it."""
    parts = [p for p in remote_path.strip("/").split("/") if p]
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        try:
            ftp.cwd(current)
        except (error_perm, error_temp):
            try:
                ftp.mkd(current)
                ftp.cwd(current)
            except (error_perm, error_temp):
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


def connect_ftp(
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    password: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    use_tls: bool = False,
    fallback_plain: bool = True,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = INITIAL_BACKOFF,
    backoff_factor: float = BACKOFF_FACTOR,
) -> tuple[FTP | None, int, str]:
    """Connect and authenticate to FTP server with retries and exponential backoff.

    Returns:
        tuple of (ftp_instance, exit_code, error_message)
    """
    delay = initial_delay
    last_err = ""

    for attempt in range(1, max_retries + 1):
        ftp_client: FTP | None = None
        try:
            protocol_name = "FTPS (FTP_TLS)" if use_tls else "FTP (Plain)"
            print(f"[*] [Attempt {attempt}/{max_retries}] Ket noi toi {host} qua {protocol_name} (User: {user})...")

            if use_tls:
                ftp_client = FTP_TLS(timeout=timeout)
            else:
                ftp_client = FTP(timeout=timeout)

            ftp_client.connect(host=host, port=21, timeout=timeout)
            ftp_client.login(user=user, passwd=password)

            if use_tls and hasattr(ftp_client, "prot_p"):
                ftp_client.prot_p()

            ftp_client.set_pasv(True)
            ftp_client.encoding = "utf-8"

            print(f"[+] Dang nhap thanh cong voi tai khoan: {user} (Passive Mode)")
            return ftp_client, EXIT_SUCCESS, ""

        except error_perm as perm_err:
            raw_msg = str(perm_err)
            sanitized = sanitize_log(raw_msg, password)
            last_err = sanitized

            if "530" in raw_msg or "authentication failed" in raw_msg.lower() or "login incorrect" in raw_msg.lower():
                print(f"[-] [CYBERPULSE INTEL] Loi xac thuc FTP 530 (Login authentication failed): {sanitized}")
                print("[-] Huong dan kiem tra:")
                print("    * Dang nhap https://app.infinityfree.com -> chon hosting account -> xem muc 'FTP Details'.")
                print("    * Mat khau FTP la 'Account Password' / 'vPanel Password' (KHONG PHAI mat khau web InfinityFree).")
                print("    * Neu chay tren GitHub Actions: Them/cap nhat secret 'FTP_PASSWORD' tai Settings -> Secrets and variables -> Actions.")
                if ftp_client:
                    try:
                        ftp_client.close()
                    except Exception:
                        pass
                return None, EXIT_AUTH_FAILED, sanitized

            if use_tls and fallback_plain and attempt == 1:
                print(f"[!] May chu tu choi FTPS ({sanitized}). Chuyen sang FTP thuan (Plain FTP)...")
                use_tls = False
                continue

        except (socket.timeout, TimeoutError, ConnectionRefusedError, ConnectionResetError, error_temp, OSError, EOFError) as net_err:
            raw_msg = str(net_err)
            sanitized = sanitize_log(raw_msg, password)
            last_err = sanitized
            print(f"[!] Su co mang ({attempt}/{max_retries}): {sanitized}")

        except Exception as generic_err:
            raw_msg = str(generic_err)
            sanitized = sanitize_log(raw_msg, password)
            last_err = sanitized
            print(f"[!] Loi khong xac dinh ({attempt}/{max_retries}): {sanitized}")

        if ftp_client:
            try:
                ftp_client.close()
            except Exception:
                pass

        if attempt < max_retries:
            print(f"[*] Thu lai sau {delay:.1f} giay...")
            time.sleep(delay)
            delay *= backoff_factor

    print(f"[-] Khong the ket noi toi FTP server sau {max_retries} lan thu. Loi cuoi: {last_err}")
    return None, EXIT_CONNECTION_ERROR, last_err


def upload_file_with_retry(
    ftp: FTP,
    local_path: Path,
    filename: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = 1.0,
) -> bool:
    """Upload a single file via FTP with retry mechanism and Zero Media safety check."""
    if is_forbidden_media_file(local_path):
        print(f"LOI: Tu choi tai len {filename} (Vi pham chinh sach Zero Media Storage cua InfinityFree)")
        return False

    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f)
            return True
        except (socket.timeout, TimeoutError, ConnectionResetError, error_temp, OSError, EOFError) as err:
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2.0
            else:
                print(f"LOI (sau {max_retries} lan thu): {err}")
                return False
        except Exception as err:
            print(f"LOI: {err}")
            return False
    return False


def cleanup_remote_prohibited_media(ftp: FTP, target_root: str) -> int:
    """Scan and purge prohibited video/audio files from remote host to satisfy InfinityFree Zero Media Policy.

    Deletes any leftover .mp3, .mp4 or other media binaries that caused or could cause domain suspension.
    """
    cleaned_count = 0
    dirs_to_check = [target_root, f"{target_root.rstrip('/')}/media"]
    for d in dirs_to_check:
        try:
            ftp.cwd(d)
            items = ftp.nlst()
            for item in items:
                clean_name = item.strip("/").split("/")[-1]
                ext = Path(clean_name).suffix.lower()
                if ext in FORBIDDEN_MEDIA_EXTENSIONS:
                    try:
                        ftp.delete(clean_name)
                        print(f"  [+] [CYBERPULSE INTEL] Da xoa tep media vi pham tren InfinityFree: {d}/{clean_name}")
                        cleaned_count += 1
                    except Exception as del_err:
                        print(f"  [-] Khong the xoa tep media {clean_name}: {del_err}")
        except Exception:
            pass
    return cleaned_count


def upload_portal(
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    password: str = "",
    dist_dir: Path = LOCAL_DIST_DIR,
    remote_root: str = DEFAULT_REMOTE_ROOT,
    rebuild: bool = False,
    use_tls: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> int:
    """Recursively upload all files from dist_dir to FTP server, auto-building if needed."""
    # Step 1: Ensure dist is ready
    if not ensure_dist_ready(dist_dir=dist_dir, force_rebuild=rebuild):
        print(f"[-] Loi: Thu muc '{dist_dir}' khong hop le hoac khong the bien dich.")
        return EXIT_LOCAL_ERROR

    local_files: list[tuple[Path, str]] = []
    skipped_media_files: list[tuple[Path, str]] = []
    for file_path in sorted(dist_dir.glob("**/*")):
        if file_path.is_file():
            rel = file_path.relative_to(dist_dir).as_posix()
            if is_forbidden_media_file(file_path):
                skipped_media_files.append((file_path, rel))
            else:
                local_files.append((file_path, rel))

    if skipped_media_files:
        total_skipped_mb = sum(p.stat().st_size for p, _ in skipped_media_files) / (1024 * 1024)
        print(f"[*] [CYBERPULSE INTEL] InfinityFree Zero Media Policy:")
        print(f"    - Da bo qua {len(skipped_media_files)} tep media ({total_skipped_mb:.2f} MB) khoi danh sach upload len host.")
        print(f"    - Toan bo video & audio duoc phan phoi tu CDN GitHub Pages de dam bao an toan tai khoan.")
        for sm_path, sm_rel in skipped_media_files:
            sm_size_kb = sm_path.stat().st_size / 1024
            print(f"    * [SKIPPED] {sm_rel} ({sm_size_kb:.1f} KB)")

    if not local_files:
        print(f"[-] Loi: Khong tim thay tep nao hop le de tai len trong '{dist_dir}'.")
        return EXIT_LOCAL_ERROR

    # Step 2: Connect with retries
    ftp, exit_code, _ = connect_ftp(
        host=host,
        user=user,
        password=password,
        timeout=timeout,
        use_tls=use_tls,
        max_retries=max_retries,
    )
    if exit_code != EXIT_SUCCESS or ftp is None:
        return exit_code

    # Step 3: Discover target roots
    target_roots = discover_target_roots(ftp, preferred_root=remote_root)

    total_uploaded = 0
    total_failed = 0

    try:
        for target_root in target_roots:
            print(f"\n[*] Dang trien khai toi thu muc: {target_root}")
            # Purge any leftover prohibited media files from hosting
            cleaned_media = cleanup_remote_prohibited_media(ftp, target_root)
            if cleaned_media > 0:
                print(f"  [+] [CYBERPULSE INTEL] Da don dep {cleaned_media} tep media vi pham tren hosting.")

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

                if upload_file_with_retry(ftp, local_path, filename, max_retries=max_retries):
                    print("OK")
                    success_count += 1
                else:
                    total_failed += 1

            print(f"[+] Hoan tat tai len {success_count}/{len(local_files)} tep vao {target_root}.")
            total_uploaded += success_count

    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass

    if total_failed > 0:
        print(f"[-] Canh bao: Co {total_failed} tep khong tai len thanh cong.")
        return EXIT_UPLOAD_ERROR

    print("=" * 60)
    print("[CYBERPULSE INTEL] Deployment hoan tat thanh cong!")
    print("Website da online tai:")
    print("  * http://cyberpulseDA.wuaze.com/")
    print("  * https://cyberpulseDA.wuaze.com/")
    print("=" * 60)
    return EXIT_SUCCESS


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload CyberPulse Portal to InfinityFree via FTP")
    parser.add_argument("--host", default=os.environ.get("FTP_HOST", DEFAULT_HOST), help="FTP Hostname (default: ftpupload.net)")
    parser.add_argument("--user", default=os.environ.get("FTP_USER", DEFAULT_USER), help="FTP Username (default: if0_43340192)")
    parser.add_argument("--password", default="", help="FTP Password")
    parser.add_argument("--dist-dir", type=Path, default=LOCAL_DIST_DIR, help="Local dist directory")
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_ROOT, help="Remote htdocs directory")
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild portal from output before upload")
    parser.add_argument("--tls", action="store_true", default=os.environ.get("FTP_USE_TLS", "").lower() in ("1", "true", "yes"), help="Use FTPS (FTP_TLS)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="FTP socket timeout in seconds (default: 30)")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Max connection/upload retries (default: 3)")
    args = parser.parse_args()

    pwd = args.password or os.environ.get("FTP_PASSWORD", "")
    if not pwd:
        if sys.stdin.isatty():
            try:
                pwd = getpass.getpass("Nhap mat khau FTP (InfinityFree vPanel Password): ")
            except (KeyboardInterrupt, EOFError):
                print("\nDa huy.")
                sys.exit(EXIT_LOCAL_ERROR)
                return
        else:
            print("[-] [CYBERPULSE INTEL] Loi: Mat khau FTP chua duoc cung cap.")
            print("    Hay truyen qua tham so --password hoac bien moi truong FTP_PASSWORD.")
            sys.exit(EXIT_AUTH_FAILED)
            return

    code = upload_portal(
        host=args.host,
        user=args.user,
        password=pwd,
        dist_dir=args.dist_dir,
        remote_root=args.remote_dir,
        rebuild=args.rebuild,
        use_tls=args.tls,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
