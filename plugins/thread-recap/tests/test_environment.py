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
    developer_identifier = "".join(("chen", "wei", "yuan"))
    personal_home_markers = tuple(
        "/".join(("", directory, "")) for directory in ("Users", "home")
    )

    for path in TASK_2_FILES:
        source = path.read_text(encoding="utf-8")

        assert not re.search(r"(?i)[a-z]:[\\/]", source)
        assert all(marker not in source for marker in personal_home_markers)
        assert developer_identifier not in source.casefold()

    for name in ("run-hook.sh", "run-hook.ps1"):
        source = (HOOKS_DIR / name).read_text(encoding="utf-8")
        assert "PLUGIN_ROOT" in source
        assert "PLUGIN_DATA" in source
        assert "worker.log" in source
        assert "hook_entry.py" in source


def test_launchers_do_not_change_to_an_installation_directory() -> None:
    shell_source = (HOOKS_DIR / "run-hook.sh").read_text(encoding="utf-8")
    powershell_source = (HOOKS_DIR / "run-hook.ps1").read_text(encoding="utf-8")

    assert not re.search(r"(?m)^\s*cd(?:\s|$)", shell_source)
    assert "Set-Location" not in powershell_source
