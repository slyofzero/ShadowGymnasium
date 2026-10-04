#!/usr/bin/env python3
"""
vm_edit.py - Create, edit, or modify files on the VM.

Usage:
    # 1. Overwrite or create file with content
    python vm_edit.py --path README.md --write --content "# New Title\nContent..."
    python vm_edit.py --path test.txt --write --from-local ./local_file.txt

    # 2. Replace a specific block of text (like replace_file_content)
    python vm_edit.py --path docker-compose.yml --replace --target "image: redroid/redroid:11.0.0" --replacement "image: redroid/redroid:12.0.0"

    # 3. Replace within a line range
    python vm_edit.py --path docker-compose.yml --replace --target "version: '3'" --replacement "version: '3.8'" --start-line 1 --end-line 5

    # 4. Append content
    python vm_edit.py --path .env --append --content "\nREDROID_PORT=5555"
"""

import argparse
import sys
from pathlib import Path

from vm_client import normalize_remote_path, replace_file_content, write_file


def main():
    parser = argparse.ArgumentParser(description="Create, edit, or modify files on the VM.")
    parser.add_argument("--path", "-p", required=True, help="Remote file path on the VM.")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", "-w", action="store_true", help="Write/overwrite entire file content.")
    group.add_argument("--replace", "-r", action="store_true", help="Replace specific content block within file.")
    group.add_argument("--append", "-a", action="store_true", help="Append content to end of file.")

    # Content inputs
    parser.add_argument("--content", "-c", help="Text content for --write or --append.")
    parser.add_argument("--from-local", "-f", help="Local file path whose content to write to remote file.")
    parser.add_argument("--target", "-t", help="Target text to be replaced (for --replace).")
    parser.add_argument("--replacement", "-m", help="Replacement text (for --replace).")
    parser.add_argument("--start-line", type=int, help="Optional start line range for --replace (1-indexed).")
    parser.add_argument("--end-line", type=int, help="Optional end line range for --replace (1-indexed).")
    parser.add_argument("--allow-multiple", action="store_true", help="Allow multiple occurrences for --replace.")

    args = parser.parse_args()
    norm_path = normalize_remote_path(args.path)

    try:
        if args.write or args.append:
            if args.from_local:
                content = Path(args.from_local).read_text(encoding="utf-8")
            elif args.content is not None:
                content = args.content
            else:
                # Read from stdin
                content = sys.stdin.read()

            bytes_written = write_file(norm_path, content, append=args.append)
            action = "Appended to" if args.append else "Wrote"
            print(f"{action} {norm_path} ({bytes_written} characters).")
            return

        if args.replace:
            if args.target is None or args.replacement is None:
                sys.stderr.write("Error: --replace requires both --target and --replacement arguments.\n")
                sys.exit(1)

            success, msg = replace_file_content(
                remote_path=norm_path,
                target_content=args.target,
                replacement_content=args.replacement,
                start_line=args.start_line,
                end_line=args.end_line,
                allow_multiple=args.allow_multiple,
            )
            if not success:
                sys.stderr.write(f"Replacement failed: {msg}\n")
                sys.exit(1)
            print(f"Successfully modified {norm_path}: {msg}")

    except Exception as e:
        sys.stderr.write(f"Error editing {norm_path}: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
