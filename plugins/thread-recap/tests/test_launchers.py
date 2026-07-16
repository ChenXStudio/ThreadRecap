import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = PLUGIN_ROOT / "hooks"


def _make_fake_plugin(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "installed plugin 插件"
    data = tmp_path / "writable data 数据"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "hook_entry.py").write_text(
        """\
import os
import sys

payload = sys.stdin.read()
sys.stdout.write("OUT:" + payload)
sys.stderr.write("ERR:" + payload)
raise SystemExit(int(os.environ.get("HOOK_EXIT_CODE", "0")))
""",
        encoding="utf-8",
    )
    return root, data


def _write_shims(tmp_path: Path, candidates: set[str]) -> tuple[Path, Path]:
    shim_dir = tmp_path / "python shims"
    shim_dir.mkdir()
    driver = tmp_path / "shim_driver.py"
    driver.write_text(
        """\
import json
import os
import subprocess
import sys

label, *args = sys.argv[1:]
with open(os.environ["SHIM_LOG"], "a", encoding="utf-8") as handle:
    handle.write(label + "\\n")
if args[:1] == ["-3"]:
    args = args[1:]
if args[:1] == ["-c"]:
    allowed = json.loads(os.environ["SHIM_VALID"])
    raise SystemExit(0 if allowed.get(label, False) else 1)
raise SystemExit(subprocess.call([os.environ["TEST_REAL_PYTHON"], *args]))
""",
        encoding="utf-8",
    )

    if os.name == "nt":
        (shim_dir / "doskey.cmd").write_text("@exit /b 0\r\n", encoding="utf-8")
        for label in ("py", "python3", "python"):
            if label not in candidates:
                continue
            (shim_dir / f"{label}.cmd").write_text(
                f'@echo off\r\n"%TEST_REAL_PYTHON%" "%SHIM_DRIVER%" {label} %*\r\n',
                encoding="utf-8",
            )
    else:
        mkdir_command = shutil.which("mkdir")
        assert mkdir_command is not None
        mkdir_shim = shim_dir / "mkdir"
        mkdir_shim.write_text(
            f'#!/bin/sh\nexec "{mkdir_command}" "$@"\n',
            encoding="utf-8",
        )
        mkdir_shim.chmod(0o755)
        for label in ("python3", "python"):
            if label not in candidates:
                continue
            path = shim_dir / label
            path.write_text(
                f'#!/bin/sh\nexec "$TEST_REAL_PYTHON" "$SHIM_DRIVER" {label} "$@"\n',
                encoding="utf-8",
            )
            path.chmod(0o755)

    return shim_dir, driver


def _launcher_command() -> list[str]:
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            pytest.skip("PowerShell is unavailable")
        return [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HOOKS_DIR / "run-hook.ps1"),
        ]

    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable")
    return [shell, str(HOOKS_DIR / "run-hook.sh")]


def _run_launcher(
    tmp_path: Path,
    *,
    valid: dict[str, bool],
    payload: str = '{"hello":"world"}',
    exit_code: int = 0,
    omit: str | None = None,
    candidates: set[str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    root, data = _make_fake_plugin(tmp_path)
    selected_candidates = set(valid) if candidates is None else candidates
    shim_dir, driver = _write_shims(tmp_path, selected_candidates)
    shim_log = tmp_path / "shim.log"
    unrelated_cwd = tmp_path / "unrelated cwd"
    unrelated_cwd.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "PLUGIN_ROOT": str(root) + os.sep,
            "PLUGIN_DATA": str(data) + os.sep,
            "HOOK_EXIT_CODE": str(exit_code),
            "SHIM_DRIVER": str(driver),
            "SHIM_LOG": str(shim_log),
            "SHIM_VALID": json.dumps(valid),
            "TEST_REAL_PYTHON": sys.executable,
            "PATH": str(shim_dir),
        }
    )
    if os.name == "nt":
        env["PATHEXT"] = ".CMD"
    if omit is not None:
        env.pop(omit, None)

    completed = subprocess.run(
        _launcher_command(),
        input=payload,
        text=True,
        capture_output=True,
        cwd=unrelated_cwd,
        env=env,
        check=False,
    )
    return completed, data, shim_log


@pytest.mark.parametrize(
    ("valid", "selected"),
    (
        [
            ({"py": True}, "py"),
            ({"python3": True}, "python3"),
            ({"python": True}, "python"),
        ]
        if os.name == "nt"
        else [
            ({"python3": True}, "python3"),
            ({"python3": False, "python": True}, "python"),
        ]
    ),
)
def test_launcher_selects_python_310_and_forwards_process_contract(
    tmp_path: Path, valid: dict[str, bool], selected: str
) -> None:
    completed, data, shim_log = _run_launcher(tmp_path, valid=valid)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == 'OUT:{"hello":"world"}'
    assert 'ERR:{"hello":"world"}' in completed.stderr
    assert shim_log.read_text(encoding="utf-8").splitlines()[-1] == selected
    assert (data / "worker.log").is_file()


def test_launcher_rejects_python_older_than_310(tmp_path: Path) -> None:
    completed, data, _ = _run_launcher(
        tmp_path,
        valid={"py": False, "python3": False, "python": False},
    )

    assert completed.returncode != 0
    assert "Python 3.10" in completed.stderr
    assert "OUT:" not in completed.stdout
    assert "Python 3.10" in (data / "worker.log").read_text(encoding="utf-8")


def test_launcher_rejects_missing_python(tmp_path: Path) -> None:
    completed, data, _ = _run_launcher(tmp_path, valid={}, candidates=set())

    assert completed.returncode != 0
    assert "Python 3.10" in completed.stderr
    assert "Python 3.10" in (data / "worker.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("missing", ["PLUGIN_ROOT", "PLUGIN_DATA"])
def test_launcher_requires_plugin_environment_without_home_fallback(
    tmp_path: Path, missing: str
) -> None:
    fake_home = tmp_path / "must remain empty"
    fake_home.mkdir()
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        completed, data, _ = _run_launcher(tmp_path, valid={"py": True}, omit=missing)
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

    assert completed.returncode != 0
    assert missing in completed.stderr
    assert list(fake_home.iterdir()) == []
    if missing == "PLUGIN_ROOT":
        assert missing in (data / "worker.log").read_text(encoding="utf-8")


def test_launcher_forwards_hook_failure_and_logs_it(tmp_path: Path) -> None:
    completed, data, _ = _run_launcher(tmp_path, valid={"py": True, "python3": True}, exit_code=23)

    assert completed.returncode == 23
    assert completed.stdout == 'OUT:{"hello":"world"}'
    assert 'ERR:{"hello":"world"}' in completed.stderr
    log = (data / "worker.log").read_text(encoding="utf-8")
    assert "23" in log


def test_launcher_rejects_uncreatable_data_directory(tmp_path: Path) -> None:
    root, data = _make_fake_plugin(tmp_path)
    data.write_text("not a directory", encoding="utf-8")
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["PLUGIN_DATA"] = str(data)

    completed = subprocess.run(
        _launcher_command(),
        input="{}",
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )

    assert completed.returncode != 0
    assert "PLUGIN_DATA" in completed.stderr
    assert "writ" in completed.stderr.casefold()
