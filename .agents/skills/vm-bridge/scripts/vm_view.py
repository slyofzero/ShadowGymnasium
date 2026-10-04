#!/usr/bin/env python3
"""
vm_view.py - View remote file contents or list directory entries on the VM.

Usage:
    python vm_view.py README.md
    python vm_view.py rl_env/shadow_fight_env.py --start-line 1 --end-line 60
    python vm_view.py . --list
    python vm_view.py ~/ShadowGymnasium/docker-compose.yml
"""

import argparse
import datetime
import sys

from vm_client import list_dir, normalize_remote_path, read_file


def main():
    parser = argparse.ArgumentParser(description="View remote files or list directories on the VM.")
    parser.add_argument(
        "path", nargs="?", default=".", help="Remote path to view or list (relative to ~/ShadowGymnasium or absolute)."
    )
    parser.add_argument("--start-line", type=int, help="Start line number (1-indexed).")
    parser.add_argument("--end-line", type=int, help="End line number (1-indexed).")
    parser.add_argument("--list", "-l", action="store_true", help="List directory entries instead of reading file.")
    parser.add_argument("--number", "-n", action="store_true", help="Show line numbers.")
    args = parser.parse_args()

    try:
        norm_path = normalize_remote_path(args.path)

        if args.list:
            items = list_dir(norm_path)
            # Sort: directories first, then alphabetically
            items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            print(f"Directory listing for {norm_path} ({len(items)} items):")
            print(f"{'TYPE':<6} {'SIZE':>10}  {'MODIFIED':<19}  {'NAME'}")
            print("-" * 60)
            for item in items:
                t = "DIR" if item["is_dir"] else "FILE"
                sz = f"{item['size']:,} B" if not item["is_dir"] else "-"
                dt = (
                    datetime.datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
                    if item["mtime"]
                    else "-"
                )
                print(f"{t:<6} {sz:>10}  {dt:<19}  {item['name']}")
            return

        content = read_file(norm_path, start_line=args.start_line, end_line=args.end_line)
        if args.number:
            lines = content.splitlines(keepends=True)
            start_num = args.start_line if args.start_line else 1
            for i, line in enumerate(lines, start=start_num):
                sys.stdout.write(f"{i:>5}: {line}")
            sys.stdout.flush()
        else:
            sys.stdout.write(content)
            sys.stdout.flush()

    except Exception as e:
        sys.stderr.write(f"Error viewing remote path '{args.path}': {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
