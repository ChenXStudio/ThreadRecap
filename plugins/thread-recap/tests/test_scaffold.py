import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_plugin_manifest_declares_thread_recap_components() -> None:
    manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")

    assert manifest["name"] == "thread-recap"
    assert manifest["interface"]["displayName"] == "ThreadRecap"
    assert manifest["skills"] == "./skills/"
    assert "hooks" not in manifest
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()
    assert (PLUGIN_ROOT / "skills" / "thread-recap" / "SKILL.md").is_file()


def test_repo_marketplace_points_to_local_thread_recap_plugin() -> None:
    marketplace = load_json(REPO_ROOT / ".agents" / "plugins" / "marketplace.json")
    entry = next(plugin for plugin in marketplace["plugins"] if plugin["name"] == "thread-recap")

    assert entry == {
        "name": "thread-recap",
        "source": {
            "source": "local",
            "path": "./plugins/thread-recap",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }
