import io
import json
import sys
from pathlib import Path

import hook_entry


def test_hook_entry_persists_state_and_wakes_worker(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "plugin root"
    data = tmp_path / "plugin data"
    root.mkdir()
    called: list[tuple[Path, Path]] = []
    monkeypatch.setenv("PLUGIN_ROOT", str(root))
    monkeypatch.setenv("PLUGIN_DATA", str(data))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "session-1",
                    "turn_id": "turn-1",
                    "prompt": "继续",
                }
            )
        ),
    )
    monkeypatch.setattr(
        hook_entry, "spawn_detached_worker", lambda a, b: called.append((a, b))
    )

    assert hook_entry.main() == 0
    assert called == [(root, data)]
    assert (data / "state.db").is_file()


def test_hook_entry_rejects_invalid_json_without_spawning(tmp_path: Path, monkeypatch) -> None:
    root, data = tmp_path / "root", tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("PLUGIN_ROOT", str(root))
    monkeypatch.setenv("PLUGIN_DATA", str(data))
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
    monkeypatch.setattr(
        hook_entry,
        "spawn_detached_worker",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    assert hook_entry.main() == 2
    assert "hook_error" in (data / "hook.log").read_text(encoding="utf-8")
