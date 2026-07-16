import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from spikes import capture_hook, probe_app_server


def test_redact_sensitive_fields_recursively_without_mutating_input() -> None:
    payload = {
        "session_id": "session-1",
        "authorization": "Bearer private",
        "apikey": "private",
        "nested": {
            "api_key": "private",
            "items": [
                {"accessToken": "private", "safe": "visible"},
                {"cookie_jar": "private", "client_secret": "private"},
            ],
        },
    }

    redacted = capture_hook.redact_sensitive(payload)

    assert redacted == {
        "session_id": "session-1",
        "authorization": "[REDACTED]",
        "apikey": "[REDACTED]",
        "nested": {
            "api_key": "[REDACTED]",
            "items": [
                {"accessToken": "[REDACTED]", "safe": "visible"},
                {"cookie_jar": "[REDACTED]", "client_secret": "[REDACTED]"},
            ],
        },
    }
    assert payload["authorization"] == "Bearer private"
    assert payload["nested"]["api_key"] == "private"


def test_append_jsonl_keeps_concurrent_records_separate(tmp_path: Path) -> None:
    path = tmp_path / "probe" / "hook-probe.jsonl"
    records = [{"sequence": index, "payload": "x" * 512} for index in range(80)]

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(lambda record: capture_hook.append_jsonl(path, record), records))

    lines = path.read_text(encoding="utf-8").splitlines()
    decoded = [json.loads(line) for line in lines]
    assert len(decoded) == len(records)
    assert sorted(record["sequence"] for record in decoded) == list(range(len(records)))


def test_stop_record_observes_transcript_already_on_disk(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"turn_context"}\n', encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    }

    record = capture_hook.build_record(payload, captured_at="2026-07-16T00:00:00Z")

    assert record["captured_at"] == "2026-07-16T00:00:00Z"
    assert record["transcript_snapshot"] == {
        "exists": True,
        "nonempty": True,
        "ends_with_newline": True,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"clientUserMessageId": "thread-recap-internal:probe-1"},
        {"prompt": probe_app_server.VISIBLE_TRIGGER_TEXT},
        {"nested": [{"text": probe_app_server.INTERNAL_TEXT_MARKER}]},
    ],
)
def test_internal_trigger_marker_is_recognized(payload: object) -> None:
    assert probe_app_server.is_internal_trigger(payload)


def test_normal_user_input_is_not_an_internal_trigger() -> None:
    assert not probe_app_server.is_internal_trigger({"prompt": "Summarize the repository."})


class _ScriptedReader(io.StringIO):
    def __init__(self, messages: list[dict[str, object]]) -> None:
        super().__init__("".join(json.dumps(message) + "\n" for message in messages))


def test_jsonl_client_pairs_response_ids_and_routes_notifications() -> None:
    reader = _ScriptedReader(
        [
            {"method": "turn/started", "params": {"turn": {"id": "turn-other"}}},
            {"id": 1, "result": {"userAgent": "codex"}},
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed", "items": []},
                },
            },
        ]
    )
    writer = io.StringIO()
    client = probe_app_server.JsonlRpcClient(reader, writer)

    result = client.request("initialize", {"clientInfo": {"name": "test"}}, timeout=1)
    completed = client.wait_for_turn_completed("thread-1", "turn-1", timeout=1)

    assert result == {"userAgent": "codex"}
    assert completed["turn"]["status"] == "completed"
    sent = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert sent == [
        {
            "method": "initialize",
            "id": 1,
            "params": {"clientInfo": {"name": "test"}},
        }
    ]


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.turn_id = "turn-1"

    def request(
        self, method: str, params: dict[str, object] | None = None, *, timeout: float
    ) -> dict[str, object]:
        self.calls.append((method, params))
        if method == "initialize":
            return {"userAgent": "codex-cli/0.144.2"}
        if method == "thread/list":
            return {
                "data": [
                    {
                        "id": "thread-1",
                        "sessionId": "session-1",
                        "ephemeral": False,
                    }
                ],
                "nextCursor": None,
            }
        if method == "thread/resume":
            return {"thread": {"id": "thread-1", "sessionId": "session-1"}}
        if method == "turn/start":
            return {"turn": {"id": self.turn_id, "status": "inProgress", "items": []}}
        if method == "thread/read":
            client_id = probe_app_server.INTERNAL_CLIENT_ID
            return {
                "thread": {
                    "id": "thread-1",
                    "turns": [
                        {
                            "id": self.turn_id,
                            "status": "completed",
                            "items": [
                                {
                                    "type": "userMessage",
                                    "id": "user-1",
                                    "clientId": client_id,
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": probe_app_server.VISIBLE_TRIGGER_TEXT,
                                        }
                                    ],
                                },
                                {
                                    "type": "agentMessage",
                                    "id": "agent-1",
                                    "text": "Probe acknowledged.",
                                },
                            ],
                        }
                    ],
                }
            }
        raise AssertionError(f"unexpected method: {method}")

    def notify(self, method: str, params: dict[str, object] | None = None) -> None:
        self.calls.append((method, params))

    def wait_for_turn_completed(
        self, thread_id: str, turn_id: str, *, timeout: float
    ) -> dict[str, object]:
        assert thread_id == "thread-1"
        assert turn_id == self.turn_id
        self.calls.append(("turn/completed", {"threadId": thread_id, "turnId": turn_id}))
        return {"threadId": thread_id, "turn": {"id": turn_id, "status": "completed"}}


def test_probe_uses_stable_thread_resume_and_turn_completion_flow() -> None:
    client = _FakeClient()

    report = probe_app_server.probe_existing_thread(
        client, session_id="session-1", timeout=1
    )

    assert report == {
        "initialized": True,
        "session_thread_mapping": True,
        "thread_id_matches_session_id": False,
        "non_ephemeral": True,
        "resumed_same_thread": True,
        "turn_completed": True,
        "internal_client_id_persisted": True,
        "result_in_marked_turn": True,
    }
    methods = [method for method, _ in client.calls]
    assert methods == [
        "initialize",
        "initialized",
        "thread/list",
        "thread/resume",
        "turn/start",
        "turn/completed",
        "thread/read",
    ]
    turn_params = next(params for method, params in client.calls if method == "turn/start")
    assert turn_params == {
        "threadId": "thread-1",
        "clientUserMessageId": probe_app_server.INTERNAL_CLIENT_ID,
        "input": [{"type": "text", "text": probe_app_server.VISIBLE_TRIGGER_TEXT}],
    }
