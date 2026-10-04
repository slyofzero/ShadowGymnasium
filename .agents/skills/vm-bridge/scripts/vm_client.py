"""
vm_client.py - Core SSH/SFTP Client Module for ShadowGymnasium VM

Handles connecting to the Linux VM at 127.0.0.1:2222 as user 'zero' with
passwordless key or password fallback. Provides primitives for remote command
execution, file viewing, file editing (including block replacement), and SFTP sync.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import paramiko

# Default VM Connection Parameters
VM_HOST = os.environ.get("VM_HOST", "127.0.0.1")
VM_PORT = int(os.environ.get("VM_PORT", "2222"))
VM_USER = os.environ.get("VM_USER", "zero")
VM_PASSWORD = os.environ.get("VM_PASSWORD", "123")
VM_DEFAULT_CWD = os.environ.get("VM_DEFAULT_CWD", "/home/zero/ShadowGymnasium")
DEFAULT_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"


def get_ssh_client(timeout: int = 10) -> paramiko.SSHClient:
    """Create and return an authenticated paramiko.SSHClient instance."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Try key first if it exists, then fallback to password
    key_file = str(DEFAULT_KEY_PATH) if DEFAULT_KEY_PATH.exists() else None

    try:
        if key_file:
            try:
                ssh.connect(
                    hostname=VM_HOST,
                    port=VM_PORT,
                    username=VM_USER,
                    key_filename=key_file,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                return ssh
            except Exception:
                # Fallback to password authentication
                pass

        ssh.connect(
            hostname=VM_HOST,
            port=VM_PORT,
            username=VM_USER,
            password=VM_PASSWORD,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        return ssh
    except Exception as e:
        raise ConnectionError(f"Failed to connect to VM at {VM_USER}@{VM_HOST}:{VM_PORT}: {e}") from e


def get_sftp_client(ssh: paramiko.SSHClient | None = None) -> tuple[paramiko.SFTPClient, paramiko.SSHClient | None]:
    """Return an SFTPClient. If an existing SSHClient is passed, returns (sftp, None).
    Otherwise creates a new SSHClient and returns (sftp, ssh) for closing."""
    owned_ssh = False
    if ssh is None:
        ssh = get_ssh_client()
        owned_ssh = True
    sftp = ssh.open_sftp()
    return sftp, (ssh if owned_ssh else None)


def normalize_remote_path(remote_path: str) -> str:
    """Convert relative paths to VM_DEFAULT_CWD relative paths, and expand ~ to /home/zero."""
    remote_path = remote_path.strip()
    if remote_path.startswith("~/"):
        return "/home/zero/" + remote_path[2:]
    if remote_path == "~":
        return "/home/zero"
    if not remote_path.startswith("/"):
        return f"{VM_DEFAULT_CWD}/{remote_path}"
    return remote_path


def run_command(
    cmd: str,
    cwd: str | None = None,
    sudo: bool = False,
    timeout: int = 120,
    ssh: paramiko.SSHClient | None = None,
) -> tuple[int, str, str]:
    """Execute a shell command on the VM.

    Args:
        cmd: Shell command string to execute.
        cwd: Working directory on VM. Defaults to VM_DEFAULT_CWD.
        sudo: Whether to prepend sudo and feed password if prompted.
        timeout: Maximum duration in seconds.
        ssh: Optional existing SSHClient connection.

    Returns:
        (exit_code, stdout_str, stderr_str)
    """
    should_close = False
    if ssh is None:
        ssh = get_ssh_client(timeout=10)
        should_close = True

    try:
        work_dir = cwd if cwd else VM_DEFAULT_CWD
        work_dir = normalize_remote_path(work_dir)

        full_cmd = f"cd {work_dir} && {cmd}"
        if sudo:
            # Wrap in sudo non-interactive with password piped via sudo -S
            full_cmd = f"echo '{VM_PASSWORD}' | sudo -S bash -c {repr(full_cmd)}"

        stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr.read().decode("utf-8", errors="replace")

        # Clean out sudo password prompts from stderr if present
        if sudo and "[sudo] password for" in err_text:
            lines = [line for line in err_text.splitlines() if not line.startswith("[sudo] password for")]
            err_text = "\n".join(lines)

        return exit_code, out_text, err_text
    finally:
        if should_close:
            ssh.close()


def read_file(
    remote_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    ssh: paramiko.SSHClient | None = None,
) -> str:
    """Read contents of a file on the VM, with optional 1-indexed line range slicing."""
    remote_path = normalize_remote_path(remote_path)
    sftp, owned_ssh = get_sftp_client(ssh)
    try:
        with sftp.file(remote_path, "r") as f:
            content = f.read().decode("utf-8", errors="replace")
    finally:
        sftp.close()
        if owned_ssh:
            owned_ssh.close()

    if start_line is None and end_line is None:
        return content

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    s = (start_line - 1) if (start_line and start_line > 0) else 0
    e = end_line if (end_line and end_line <= total_lines) else total_lines
    sliced = "".join(lines[s:e])
    return sliced


def write_file(
    remote_path: str,
    content: str,
    append: bool = False,
    ssh: paramiko.SSHClient | None = None,
) -> int:
    """Write or append content to a file on the VM. Creates parent directories if missing."""
    remote_path = normalize_remote_path(remote_path)
    sftp, owned_ssh = get_sftp_client(ssh)
    try:
        # Ensure parent directory exists
        parent_dir = str(Path(remote_path).parent).replace("\\", "/")
        _ensure_remote_dir(sftp, parent_dir)

        mode = "a" if append else "w"
        with sftp.file(remote_path, mode) as f:
            f.write(content.encode("utf-8"))
        return len(content)
    finally:
        sftp.close()
        if owned_ssh:
            owned_ssh.close()


def replace_file_content(
    remote_path: str,
    target_content: str,
    replacement_content: str,
    start_line: int | None = None,
    end_line: int | None = None,
    allow_multiple: bool = False,
    ssh: paramiko.SSHClient | None = None,
) -> tuple[bool, str]:
    """Replace target_content with replacement_content in a file on the VM.

    If start_line and end_line are provided, target_content is searched only within
    that 1-indexed range.
    """
    remote_path = normalize_remote_path(remote_path)
    sftp, owned_ssh = get_sftp_client(ssh)
    try:
        with sftp.file(remote_path, "r") as f:
            content = f.read().decode("utf-8", errors="replace")

        # Normalize line endings
        target_content_norm = target_content.replace("\r\n", "\n")
        replacement_content_norm = replacement_content.replace("\r\n", "\n")
        content_norm = content.replace("\r\n", "\n")

        if start_line is not None or end_line is not None:
            lines = content_norm.split("\n")
            total_lines = len(lines)
            s_idx = (start_line - 1) if (start_line and start_line > 0) else 0
            e_idx = end_line if (end_line and end_line <= total_lines) else total_lines

            target_block = "\n".join(lines[s_idx:e_idx])
            occurrences = target_block.count(target_content_norm)

            if occurrences == 0:
                return False, f"Target content not found in lines {start_line}..{end_line}"
            if occurrences > 1 and not allow_multiple:
                return (
                    False,
                    f"Found {occurrences} occurrences in lines {start_line}..{end_line}, set allow_multiple=True",
                )

            replaced_block = target_block.replace(
                target_content_norm, replacement_content_norm, -1 if allow_multiple else 1
            )
            new_lines = lines[:s_idx] + replaced_block.split("\n") + lines[e_idx:]
            new_content = "\n".join(new_lines)
        else:
            occurrences = content_norm.count(target_content_norm)
            if occurrences == 0:
                return False, f"Target content not found in file: {remote_path}"
            if occurrences > 1 and not allow_multiple:
                return False, f"Found {occurrences} occurrences in file, set allow_multiple=True"

            new_content = content_norm.replace(
                target_content_norm, replacement_content_norm, -1 if allow_multiple else 1
            )

        with sftp.file(remote_path, "w") as f:
            f.write(new_content.encode("utf-8"))

        return True, "Successfully replaced content"
    finally:
        sftp.close()
        if owned_ssh:
            owned_ssh.close()


def list_dir(remote_path: str, ssh: paramiko.SSHClient | None = None) -> list[dict[str, Any]]:
    """List directory contents on VM with permissions, size, and type."""
    remote_path = normalize_remote_path(remote_path)
    sftp, owned_ssh = get_sftp_client(ssh)
    try:
        attrs = sftp.listdir_attr(remote_path)
        items = []
        for attr in attrs:
            is_dir = bool(attr.st_mode and (attr.st_mode & 0o040000))
            items.append(
                {
                    "name": attr.filename,
                    "size": attr.st_size,
                    "is_dir": is_dir,
                    "mode": oct(attr.st_mode) if attr.st_mode else "",
                    "mtime": attr.st_mtime,
                }
            )
        return items
    finally:
        sftp.close()
        if owned_ssh:
            owned_ssh.close()


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Recursively ensure remote directory path exists."""
    parts = remote_dir.strip("/").split("/")
    current = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        current += part + "/"
        try:
            sftp.stat(current)
        except OSError:
            try:
                sftp.mkdir(current)
            except OSError:
                pass
