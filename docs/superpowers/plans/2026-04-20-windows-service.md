# Windows Service Installation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `python -m server` in an NSSM-managed Windows service named `switchboard` that starts automatically at logon and survives VS Code being closed.

**Architecture:** Three PowerShell scripts in `scripts/` handle install, uninstall, and restart (with a pytest gate). NSSM sets the working directory to `C:\Work\Switchboard` so the existing `.env` fallback in `config.py` (`Path.cwd() / ".env"`) resolves correctly — no need to embed secrets in the registry via `AppEnvironmentExtra`. NSSM's stdout/stderr redirection captures uvicorn console output alongside the existing JSONL audit log.

**Tech Stack:** NSSM 2.24+, PowerShell 5.1, existing Python venv at `C:\Work\Switchboard\.venv\Scripts\python.exe`

---

## Task 1: Acquire and place NSSM

**Files:**
- No repo files changed — this is a one-time workstation setup step.

- [ ] **Step 1: Download NSSM**

  Browse to `https://nssm.cc/download` and download the latest stable zip (currently `nssm-2.24.zip`).

- [ ] **Step 2: Extract and place `nssm.exe`**

  Run in an elevated PowerShell (right-click → "Run as Administrator"):

  ```powershell
  New-Item -ItemType Directory -Force -Path "C:\Tools\nssm"
  # Extract the zip you downloaded; copy the win64 binary:
  Copy-Item "C:\Users\JohnAnthony\Downloads\nssm-2.24\win64\nssm.exe" "C:\Tools\nssm\nssm.exe"
  ```

- [ ] **Step 3: Add `C:\Tools\nssm` to the system PATH**

  Run in an elevated PowerShell:

  ```powershell
  $current = [Environment]::GetEnvironmentVariable("Path", "Machine")
  if ($current -notlike "*C:\Tools\nssm*") {
      [Environment]::SetEnvironmentVariable("Path", "$current;C:\Tools\nssm", "Machine")
      Write-Host "PATH updated. Close and reopen PowerShell for the change to take effect."
  } else {
      Write-Host "C:\Tools\nssm already on PATH — nothing to do."
  }
  ```

- [ ] **Step 4: Verify**

  Open a new (non-elevated) PowerShell and run:

  ```powershell
  nssm version
  ```

  Expected output: a line containing `NSSM service installer` and a version number. Any other output (e.g., "command not found") means the PATH change did not take effect — close and reopen PowerShell, then retry.

---

## Task 2: Write `scripts/install-service.ps1`

**Files:**
- Create: `scripts/install-service.ps1`

- [ ] **Step 1: Create the scripts directory and write the file**

  Create `scripts/install-service.ps1` with this exact content:

  ```powershell
  #Requires -RunAsAdministrator
  param()
  $ErrorActionPreference = "Stop"

  $ServiceName = "switchboard"
  $Python      = "C:\Work\Switchboard\.venv\Scripts\python.exe"
  $AppDir      = "C:\Work\Switchboard"
  $LogDir      = "$AppDir\logs"

  if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
  	Write-Error "nssm not found on PATH. See Task 1 in docs/superpowers/plans/2026-04-20-windows-service.md."
  	exit 1
  }

  if (-not (Test-Path $Python)) {
  	Write-Error "Python venv not found at $Python. Run: cd $AppDir && pip install -e '.[dev]'"
  	exit 1
  }

  $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($null -ne $existing) {
  	Write-Error "Service '$ServiceName' already exists. Run scripts\uninstall-service.ps1 first."
  	exit 1
  }

  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

  nssm install   $ServiceName $Python "-m" "server"
  nssm set       $ServiceName AppDirectory  $AppDir
  nssm set       $ServiceName AppStdout     "$LogDir\nssm-stdout.log"
  nssm set       $ServiceName AppStderr     "$LogDir\nssm-stderr.log"
  nssm set       $ServiceName AppRotateFiles   1
  nssm set       $ServiceName AppRotateBytes   5242880
  nssm set       $ServiceName AppRotateOnline  1
  nssm set       $ServiceName Description "Switchboard MCP gateway for Claude Code agents"
  nssm set       $ServiceName Start SERVICE_AUTO_START

  Write-Host "Starting $ServiceName..."
  nssm start $ServiceName
  Start-Sleep -Seconds 3
  nssm status $ServiceName
  Write-Host "Done. MCP endpoint: http://localhost:9876/sse"
  ```

- [ ] **Step 2: Verify syntax**

  Run in any (non-elevated) PowerShell from the repo root:

  ```powershell
  powershell -NoProfile -Command "& { . 'scripts\install-service.ps1' -WhatIf }" 2>&1
  ```

  Expected: Either "This script requires administrator privileges" (the `#Requires` guard fires) or a parse error listing. A parse error means fix the script. The admin guard firing is correct — it means syntax is valid.

  > Alternatively: `Get-Command -Syntax scripts\install-service.ps1` — if it returns the param block without errors, syntax is clean.

---

## Task 3: Write `scripts/uninstall-service.ps1`

**Files:**
- Create: `scripts/uninstall-service.ps1`

- [ ] **Step 1: Write the file**

  Create `scripts/uninstall-service.ps1`:

  ```powershell
  #Requires -RunAsAdministrator
  param()
  $ErrorActionPreference = "Stop"

  $ServiceName = "switchboard"

  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($null -eq $svc) {
  	Write-Host "Service '$ServiceName' not found — nothing to remove."
  	exit 0
  }

  Write-Host "Stopping $ServiceName..."
  nssm stop $ServiceName
  Write-Host "Removing $ServiceName..."
  nssm remove $ServiceName confirm
  Write-Host "Done."
  ```

- [ ] **Step 2: Verify syntax** (same method as Task 2 Step 2)

  ```powershell
  powershell -NoProfile -NonInteractive -Command "[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'scripts\uninstall-service.ps1'), [ref]`$null, [ref]`$errors); `$errors"
  ```

  Expected: empty output (no parse errors).

---

## Task 4: Write `scripts/restart-service.ps1`

**Files:**
- Create: `scripts/restart-service.ps1`

- [ ] **Step 1: Write the file**

  Create `scripts/restart-service.ps1`:

  ```powershell
  #Requires -RunAsAdministrator
  param()
  $ErrorActionPreference = "Stop"

  $ServiceName = "switchboard"
  $AppDir      = "C:\Work\Switchboard"

  Write-Host "--- Stopping $ServiceName ---"
  nssm stop $ServiceName

  Write-Host "--- Running pytest gate ---"
  Push-Location $AppDir
  try {
  	& ".venv\Scripts\python.exe" -m pytest -q
  	if ($LASTEXITCODE -ne 0) {
  		Write-Error "Tests failed — $ServiceName NOT restarted. Fix the failures and re-run this script."
  		exit 1
  	}
  } finally {
  	Pop-Location
  }

  Write-Host "--- Starting $ServiceName ---"
  nssm start $ServiceName
  Start-Sleep -Seconds 3
  nssm status $ServiceName
  Write-Host "Done. MCP endpoint: http://localhost:9876/sse"
  ```

- [ ] **Step 2: Verify syntax**

  ```powershell
  powershell -NoProfile -NonInteractive -Command "[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'scripts\restart-service.ps1'), [ref]`$null, [ref]`$errors); `$errors"
  ```

  Expected: empty output.

---

## Task 5: Install the service and verify end-to-end

> **Admin PowerShell required for all steps in this task.**

- [ ] **Step 1: Stop the manually-running server if it is currently up**

  Check whether port 9876 is in use:

  ```powershell
  Test-NetConnection -ComputerName localhost -Port 9876 -WarningAction SilentlyContinue | Select-Object TcpTestSucceeded
  ```

  If `TcpTestSucceeded: True`, the server is already running from a previous manual `python -m server`. Stop it (Ctrl+C in that terminal, or kill via Task Manager).

- [ ] **Step 2: Run the install script**

  From an elevated PowerShell, repo root:

  ```powershell
  .\scripts\install-service.ps1
  ```

  Expected last two lines of output:
  ```
  SERVICE_RUNNING
  Done. MCP endpoint: http://localhost:9876/sse
  ```

  If the output shows `SERVICE_START_PENDING` instead of `SERVICE_RUNNING`, wait 5 more seconds and run:
  ```powershell
  nssm status switchboard
  ```
  until it shows `SERVICE_RUNNING`.

  If the output shows `SERVICE_STOPPED` or an error, check the NSSM stderr log:
  ```powershell
  Get-Content C:\Work\Switchboard\logs\nssm-stderr.log -Tail 30
  ```

- [ ] **Step 3: Verify the MCP SSE endpoint is reachable**

  ```powershell
  try {
      $r = Invoke-WebRequest -Uri "http://localhost:9876/sse" -Method Get -TimeoutSec 5 -ErrorAction Stop
      Write-Host "HTTP $($r.StatusCode) — endpoint reachable"
  } catch [System.Net.WebException] {
      if ($_.Exception.Response) {
          Write-Host "HTTP $([int]$_.Exception.Response.StatusCode) — endpoint reachable (non-2xx is fine for SSE)"
      } else {
          Write-Error "Connection refused — service is not listening on 9876"
      }
  }
  ```

  Expected: any HTTP response (200, 4xx, 5xx all mean the server is up and listening). "Connection refused" means the service failed to start — check the stderr log.

- [ ] **Step 4: Verify service survives logout / reboot**

  Either:
  - **Reboot test:** `Restart-Computer` → log back in → `nssm status switchboard` → expect `SERVICE_RUNNING`
  - **Quick test (no reboot):** Stop and start manually to confirm the auto-start flag is correct:
    ```powershell
    nssm stop switchboard
    nssm status switchboard    # expect SERVICE_STOPPED
    nssm start switchboard
    nssm status switchboard    # expect SERVICE_RUNNING
    ```

- [ ] **Step 5: Verify restart script with pytest gate**

  From an elevated PowerShell, repo root:

  ```powershell
  .\scripts\restart-service.ps1
  ```

  Expected output (abbreviated):
  ```
  --- Stopping switchboard ---
  --- Running pytest gate ---
  44 passed in X.XXs
  --- Starting switchboard ---
  SERVICE_RUNNING
  Done. MCP endpoint: http://localhost:9876/sse
  ```

  The service should be `SERVICE_RUNNING` at the end. If tests fail (e.g., intentionally break one to test the gate), the script should exit before restarting, leaving the service stopped.

---

## Task 6: Update docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CLAUDE-JOURNAL.md`

- [ ] **Step 1: Add a "Service management" section to `CLAUDE.md`**

  Append the following block to the "Running locally" section (after the `python -m server` snippet):

  ```markdown
  ## Service management (Windows service via NSSM)

  The server normally runs as a Windows service so it starts automatically and survives VS Code being closed.

  ```powershell
  # Install (one-time, requires admin PowerShell):
  .\scripts\install-service.ps1

  # Check status:
  nssm status switchboard

  # Restart after code changes (stops service, runs pytest gate, restarts):
  .\scripts\restart-service.ps1        # requires admin PowerShell

  # Remove the service:
  .\scripts\uninstall-service.ps1      # requires admin PowerShell
  ```

  Logs: `logs\switchboard.jsonl` (JSONL audit), `logs\nssm-stdout.log` / `nssm-stderr.log` (uvicorn console output).

  NSSM sets the working directory to `C:\Work\Switchboard`, so `config.py`'s `.env` fallback resolves correctly — no need to register secrets in the service registry.
  ```

- [ ] **Step 2: Append a CLAUDE-JOURNAL.md entry**

  Append to `CLAUDE-JOURNAL.md`:

  ```markdown
  ---

  ## 2026-04-20 — NSSM Windows service install

  ### What changed

  - Created `scripts/install-service.ps1`, `scripts/uninstall-service.ps1`, `scripts/restart-service.ps1`.
  - `install-service.ps1` installs a `SERVICE_AUTO_START` Windows service named `switchboard` via NSSM, pointing at `C:\Work\Switchboard\.venv\Scripts\python.exe -m server` with `AppDirectory=C:\Work\Switchboard`. NSSM stdout/stderr redirected to `logs\nssm-stdout.log` / `nssm-stderr.log` with 5 MB online rotation.
  - `restart-service.ps1` provides a stop → `pytest -q` gate → start workflow; exits before restarting if tests fail.
  - Env vars still sourced from `.env` via `config.py`'s dotenv fallback (not from NSSM `AppEnvironmentExtra`) — secrets stay in `.env`, not the registry.
  - Added "Service management" section to `CLAUDE.md`.
  - Verified: service starts, MCP SSE endpoint reachable at `http://localhost:9876/sse`, service survives stop/start cycle.

  ### Files touched

  - `scripts/install-service.ps1` (new)
  - `scripts/uninstall-service.ps1` (new)
  - `scripts/restart-service.ps1` (new)
  - `CLAUDE.md` (service management section added)
  - `CLAUDE-JOURNAL.md` (this entry)

  ### Open follow-ups

  - ForceReply (one-line Telegram UX fix) — next up per backlog.
  - Never-stop-asking skill update — SKILL.md edit, can be done in the same session as ForceReply.
  ```
