import json
import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = PLUGIN_ROOT / "hooks"
TASK_2_FILES = (
    HOOKS_DIR / "hooks.json",
    HOOKS_DIR / "run-hook.ps1",
    HOOKS_DIR / "run-hook.sh",
    Path(__file__),
    Path(__file__).with_name("test_launchers.py"),
)


def test_hooks_register_supported_synchronous_launchers() -> None:
    with (HOOKS_DIR / "hooks.json").open(encoding="utf-8") as handle:
        config = json.load(handle)

    hooks = config["hooks"]
    assert set(hooks) == {"UserPromptSubmit", "Stop", "SessionStart"}

    for event_name, groups in hooks.items():
        assert len(groups) == 1
        group = groups[0]
        if event_name == "SessionStart":
            assert group["matcher"] == "startup|resume"
        else:
            assert "matcher" not in group

        assert len(group["hooks"]) == 1
        handler = group["hooks"][0]
        assert handler["type"] == "command"
        assert handler["timeout"] == 10
        assert handler["command"] == '"$PLUGIN_ROOT/hooks/run-hook.sh"'
        assert "$env:PLUGIN_ROOT" in handler["commandWindows"]
        assert "run-hook.ps1" in handler["commandWindows"]
        assert "async" not in handler


def test_task_2_files_are_environment_relative_and_machine_agnostic() -> None:
    machine_path_patterns = (
        re.compile(r"(?i)[a-z]:[\\/]"),
        re.compile(r"(?i)/(?:Users|home)/[^/\\\s]+"),
    )

    for path in TASK_2_FILES:
        source = path.read_text(encoding="utf-8")
        assert all(pattern.search(source) is None for pattern in machine_path_patterns)

    for name in ("run-hook.sh", "run-hook.ps1"):
        source = (HOOKS_DIR / name).read_text(encoding="utf-8")
        assert "PLUGIN_ROOT" in source
        assert "PLUGIN_DATA" in source
        assert "worker.log" in source
        assert "hook_entry.py" in source
        fallback_patterns = (
            re.compile(r"Path[.]home\("),
            re.compile(r"expanduser\("),
            re.compile(r"\$HOME\b"),
            re.compile(r"\$env:USERPROFILE\b", re.IGNORECASE),
            re.compile(r"GetFolderPath\(", re.IGNORECASE),
        )
        assert all(pattern.search(source) is None for pattern in fallback_patterns)


def test_launchers_do_not_change_to_an_installation_directory() -> None:
    shell_source = (HOOKS_DIR / "run-hook.sh").read_text(encoding="utf-8")
    powershell_source = (HOOKS_DIR / "run-hook.ps1").read_text(encoding="utf-8")

    assert not re.search(r"(?m)^\s*cd(?:\s|$)", shell_source)
    assert "Set-Location" not in powershell_source


def test_all_distributed_runtime_files_remain_machine_agnostic() -> None:
    patterns = (
        re.compile(r"(?im)(?:^|[\"'\s(])[a-z]:[\\/]"),
        re.compile(r"(?i)/(?:Users|home)/[^/\\\s]+"),
        re.compile(r"(?i)chenweiyuan"),
    )
    suffixes = {".json", ".md", ".ps1", ".py", ".sh", ".toml"}

    for path in PLUGIN_ROOT.rglob("*"):
        if (
            not path.is_file()
            or path.suffix not in suffixes
            or "__pycache__" in path.parts
            or "tests" in path.parts
        ):
            continue
        source = path.read_text(encoding="utf-8")
        assert all(pattern.search(source) is None for pattern in patterns), path
