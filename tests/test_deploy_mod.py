"""scripts/deploy-mod.ps1 against a fake install: a temporary USERPROFILE whose
installed_plugins.json points at a copy, a committed plugin/ folder as the
source, and a stub `claude` first on the PATH, so the script's comparison runs
without touching the real plugin cache. Skipped without pwsh or git."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-mod.ps1"
PWSH = shutil.which("pwsh")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(PWSH is None or GIT is None, reason="pwsh or git not on PATH")


def _install(tmp_path: Path) -> tuple[Path, Path, dict]:
	source = tmp_path / "source"
	plugin = source / "plugin"
	(plugin / ".claude-plugin").mkdir(parents=True)
	(plugin / "hooks").mkdir()
	(plugin / ".claude-plugin" / "plugin.json").write_bytes(b'{ "name": "switchboard" }\n')
	(plugin / "hooks" / "switchboard.ts").write_bytes(b"export const register = () => {}\n")
	git = [GIT, "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
	subprocess.run([GIT, "init", "-q", str(source)], check=True)
	subprocess.run([*git, "add", "-A"], check=True)
	subprocess.run([*git, "commit", "-q", "-m", "plugin"], check=True)

	copy = tmp_path / "cache" / "switchboard" / "switchboard" / "abc123"
	shutil.copytree(plugin, copy)

	profile = tmp_path / "profile"
	plugins = profile / ".claude" / "plugins"
	plugins.mkdir(parents=True)
	(plugins / "installed_plugins.json").write_text(
		json.dumps({"plugins": {"switchboard@switchboard": [{"installPath": str(copy)}]}}), encoding="utf-8",
	)

	bin_dir = tmp_path / "bin"
	bin_dir.mkdir()
	(bin_dir / "claude.cmd").write_bytes(b"@exit /b 0\r\n")
	env = {**os.environ, "USERPROFILE": str(profile), "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
	return source, copy, env


def _deploy(source: Path, env: dict) -> subprocess.CompletedProcess:
	return subprocess.run(
		[PWSH, "-NoProfile", "-File", str(SCRIPT), "-Source", str(source)],
		capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120,
	)


def test_a_copy_with_the_cli_bookkeeping_matches(tmp_path):
	# Each session that loads a copy leaves .in_use/<pid>, and a superseded copy
	# gets .orphaned_at: Claude Code's own files, not drift.
	source, copy, env = _install(tmp_path)
	(copy / ".in_use").mkdir()
	(copy / ".in_use" / "20284").write_bytes(b"x" * 48)
	(copy / ".orphaned_at").write_bytes(b"1760000000000")
	result = _deploy(source, env)
	assert result.returncode == 0, result.stdout + result.stderr
	assert "matches" in result.stdout


def test_a_copy_with_an_extra_file_does_not_match(tmp_path):
	source, copy, env = _install(tmp_path)
	(copy / ".env").write_bytes(b"SECRET=1\n")
	result = _deploy(source, env)
	assert result.returncode == 1
	assert "extra in the copy: .env" in result.stdout + result.stderr
