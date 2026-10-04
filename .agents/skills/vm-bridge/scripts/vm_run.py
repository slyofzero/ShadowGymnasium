#!/usr/bin/env python3
"""
vm_run.py - Execute arbitrary shell commands on the VM.

Usage:
    python vm_run.py "uname -a"
    python vm_run.py "docker ps" --sudo
    python vm_run.py "pytest" --cwd ~/ShadowGymnasium/rl_env
"""

import argparse
import sys

from vm_client import run_command


def main():
    parser = argparse.ArgumentParser(description="Execute shell command on the ShadowGymnasium VM.")
    parser.add_argument("command", help="The command string to execute.")
    parser.add_argument("--cwd", help="Remote working directory (defaults to ~/ShadowGymnasium).")
    parser.add_argument("--sudo", action="store_true", help="Execute command with root privileges via sudo.")
    parser.add_argument("--timeout", type=int, default=120, help="Command timeout in seconds (default: 120s).")
    args = parser.parse_args()

    try:
        code, stdout, stderr = run_command(
            cmd=args.command,
            cwd=args.cwd,
            sudo=args.sudo,
            timeout=args.timeout,
        )
        if stdout:
            sys.stdout.write(stdout)
            sys.stdout.flush()
        if stderr:
            sys.stderr.write(stderr)
            sys.stderr.flush()
        sys.exit(code)
    except Exception as e:
        sys.stderr.write(f"Error executing remote command: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
