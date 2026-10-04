---
name: vm-bridge
description: Interacts with the remote Linux VM hosting the ShadowGymnasium environment via SSH/SFTP (127.0.0.1:2222, user zero). Use whenever running commands, viewing files, editing code, or synchronizing assets on the VM.
---

# VM Bridge Skill (ShadowGymnasium VM)

This skill provides a standardized, headless Python toolset to view files, edit files, execute commands, and synchronize files on the ShadowGymnasium Linux VM.

## Target VM Architecture & Connectivity
- **Host**: `127.0.0.1`
- **Port**: `2222`
- **User**: `zero`
- **Password**: `123` (Key authentication via `~/.ssh/id_ed25519` is also provisioned in `~/.ssh/authorized_keys`)
- **Default Repo Directory**: `/home/zero/ShadowGymnasium`
- **Environment**: Ubuntu Linux x86_64, Docker, Redroid container runtime

---

## 1. Toolset Overview

All scripts are located in `.agents/skills/vm-bridge/scripts/` and should be executed using the workspace Python virtual environment (`.venv\Scripts\python`):

| Script | Purpose | Common Flags |
| :--- | :--- | :--- |
| `vm_run.py` | Run shell commands on the VM | `--cwd <dir>`, `--sudo`, `--timeout <sec>` |
| `vm_view.py` | View remote file contents or list directories | `--start-line <N>`, `--end-line <N>`, `--list`, `--number` |
| `vm_edit.py` | Overwrite, append, or replace text blocks in files | `--write`, `--replace`, `--append`, `--content`, `--target`, `--replacement` |
| `vm_sync.py` | Transfer files or directories via SFTP | `push <local> <remote> [--recursive]`, `pull <remote> <local>` |

---

## 2. Command Execution (`vm_run.py`)

Run commands in the default working directory (`/home/zero/ShadowGymnasium`):
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_run.py "uname -a && docker --version"
```

### Specifying Working Directory:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_run.py "ls -la" --cwd /home/zero
```

### Running with Sudo Privileges:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_run.py "docker ps -a" --sudo
```

### Setting Timeout:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_run.py "docker build -t test ." --timeout 300
```

---

## 3. Viewing Files and Directories (`vm_view.py`)

### List Remote Directory:
```bash
# List repo root
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_view.py . --list

# List specific folder
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_view.py rl_env --list
```

### View Entire File:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_view.py docker-compose.yml
```

### View Sliced Line Range with Line Numbers:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_view.py docker-compose.yml --start-line 1 --end-line 25 --number
```

---

## 4. Editing Files on the VM (`vm_edit.py`)

### Block Replacement (Recommended for modifications):
Matches an exact block of text and replaces it, identical to `replace_file_content`:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_edit.py `
  --path docker-compose.yml `
  --replace `
  --target "image: sf2-harness:latest" `
  --replacement "image: sf2-harness:v2"
```

### Scoped Block Replacement (within line range):
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_edit.py `
  --path docker-compose.yml `
  --replace `
  --target "version: '3'" `
  --replacement "version: '3.8'" `
  --start-line 1 `
  --end-line 5
```

### Writing / Overwriting Entire File:
From string:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_edit.py `
  --path scripts/setup_redroid.sh `
  --write `
  --content "#!/usr/bin/env bash`necho Setting up Redroid..."
```

From local file:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_edit.py `
  --path scripts/setup_redroid.sh `
  --write `
  --from-local local_setup.sh
```

### Appending to File:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_edit.py `
  --path .env `
  --append `
  --content "`nREDROID_GPU=guest"
```

---

## 5. File Synchronization (`vm_sync.py`)

### Push Local File to VM:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_sync.py push ./local_script.py scripts/local_script.py
```

### Push Directory Recursively:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_sync.py push ./assets/ ~/ShadowGymnasium/modding/assets/ --recursive
```

### Pull Remote File from VM:
```bash
.venv\Scripts\python .agents/skills/vm-bridge/scripts/vm_sync.py pull docker-compose.yml ./docker-compose.yml
```

---

## 6. Python API Usage

The underlying `vm_client` module can also be imported directly in Python scripts:

```python
from vm_client import run_command, read_file, write_file, replace_file_content, list_dir

# Run a command
code, stdout, stderr = run_command("docker ps", sudo=True)

# Read file
content = read_file("docker-compose.yml", start_line=1, end_line=30)

# Replace block
success, msg = replace_file_content("docker-compose.yml", "old_text", "new_text")
```
