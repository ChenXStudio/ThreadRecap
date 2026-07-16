import io
import json

from thread_recap.app_server import AppServerClient, ThreadSnapshot


def test_thread_snapshot_uses_explicit_turn_status_and_completion_timestamp() -> None:
    snapshot = ThreadSnapshot.from_thread(
        {
            "id": "thread-1",
            "turns": [
                {"id": "turn-1", "status": "completed", "completedAt": 123, "items": []}
            ],
        }
    )

    assert snapshot.thread_id == "thread-1"
    assert snapshot.turn("turn-1").status == "completed"
    assert snapshot.turn("turn-1").completed_at == 123.0


def test_latest_normal_turn_excludes_thread_recap_client_ids() -> None:
    snapshot = ThreadSnapshot.from_thread(
        {
            "id": "thread-1",
            "turns": [
                {
                    "id": "turn-1",
                    "status": "completed",
                    "items": [{"type": "userMessage", "clientId": None}],
                },
                {
                    "id": "turn-2",
                    "status": "completed",
                    "items": [
                        {
                            "type": "userMessage",
                            "clientId": "thread-recap-internal:1:turn-1",
                        }
                    ],
                },
            ],
        }
    )

    assert snapshot.latest_normal_turn_id == "turn-1"
    assert snapshot.recap_turn("turn-1", 1).turn_id == "turn-2"


def test_jsonl_client_routes_notifications_while_pairing_responses() -> None:
    reader = io.StringIO(
        "".join(
            json.dumps(item) + "\n"
            for item in [
                {"method": "turn/started", "params": {"turn": {"id": "other"}}},
                {"id": 1, "result": {"ok": True}},
            ]
        )
    )
    writer = io.StringIO()
    client = AppServerClient(reader, writer)

    assert client.request("initialize", {}, timeout=1) == {"ok": True}
