#!/usr/bin/env python3
"""
vm_sync.py - Transfer files or directories between local host and the VM.

Usage:
    python vm_sync.py push ./local_script.py scripts/local_script.py
    python vm_sync.py pull docker-compose.yml ./docker-compose.yml
    python vm_sync.py push ./rl_env/ ~/ShadowGymnasium/rl_env/ --recursive
"""

import argparse
import os
import sys
from pathlib import Path

from vm_client import _ensure_remote_dir, get_sftp_client, normalize_remote_path


def push_file(sftp, local_file: Path, remote_file: str):
    parent = str(Path(remote_file).parent).replace("\\", "/")
    _ensure_remote_dir(sftp, parent)
    sftp.put(str(local_file), remote_file)
    print(f"Pushed: {local_file} -> {remote_file}")


def pull_file(sftp, remote_file: str, local_file: Path):
    local_file.parent.mkdir(parents=True, exist_ok=True)
    sftp.get(remote_file, str(local_file))
    print(f"Pulled: {remote_file} -> {local_file}")


def push_dir(sftp, local_dir: Path, remote_dir: str):
    for root, _dirs, files in os.walk(local_dir):
        rel = Path(root).relative_to(local_dir)
        target_dir = f"{remote_dir}/{rel.as_posix()}".rstrip("/")
        _ensure_remote_dir(sftp, target_dir)
        for f in files:
            lp = Path(root) / f
            rp = f"{target_dir}/{f}"
            sftp.put(str(lp), rp)
            print(f"Pushed: {lp} -> {rp}")


def main():
    parser = argparse.ArgumentParser(description="Transfer files/directories to/from VM via SFTP.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    push_parser = subparsers.add_parser("push", help="Upload local file/directory to VM.")
    push_parser.add_argument("local_path", help="Local file or directory path.")
    push_parser.add_argument("remote_path", help="Remote destination path.")
    push_parser.add_argument("--recursive", "-r", action="store_true", help="Recursive upload for directories.")

    pull_parser = subparsers.add_parser("pull", help="Download remote file from VM.")
    pull_parser.add_argument("remote_path", help="Remote file path on VM.")
    pull_parser.add_argument("local_path", help="Local destination file path.")

    args = parser.parse_args()

    sftp, ssh = get_sftp_client()
    try:
        if args.action == "push":
            local = Path(args.local_path).resolve()
            remote = normalize_remote_path(args.remote_path)
            if local.is_dir():
                if not args.recursive:
                    sys.stderr.write("Error: Local path is a directory. Use --recursive to push directories.\n")
                    sys.exit(1)
                push_dir(sftp, local, remote)
            else:
                push_file(sftp, local, remote)

        elif args.action == "pull":
            remote = normalize_remote_path(args.remote_path)
            local = Path(args.local_path).resolve()
            pull_file(sftp, remote, local)

    except Exception as e:
        sys.stderr.write(f"Transfer failed: {e}\n")
        sys.exit(1)
    finally:
        sftp.close()
        if ssh:
            ssh.close()


if __name__ == "__main__":
    main()
