"""Integration tests for Flask NDJSON streaming and history persistence."""
import json
import unittest
from unittest.mock import patch

import server


def parse_events(response):
    return [json.loads(line) for line in response.data.decode("utf-8").splitlines() if line]


class ChatStreamTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "676769"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/providers", json={
            "name": "stream-test-provider",
            "base_url": "http://127.0.0.1:9999/v1",
            "model": "fake",
        })
        # Fresh test databases and previous runs may use this name already.
        if response.status_code == 409:
            existing = self.client.get("/api/providers").get_json()
            self.pid = next(p["id"] for p in existing if p["name"] == "stream-test-provider")
        else:
            self.assertEqual(response.status_code, 200)
            self.pid = response.get_json()["id"]
        response = self.client.post("/api/chats", json={"provider_id": self.pid})
        self.assertEqual(response.status_code, 200)
        self.cid = response.get_json()["id"]

    def test_stream_sends_deltas_then_saves_one_complete_answer(self):
        with patch.object(server, "stream_completion", return_value=iter(["При", "вет!"])):
            response = self.client.post(
                f"/api/chats/{self.cid}/message?stream=1",
                data={"content": "Привет", "provider_id": self.pid},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("application/x-ndjson", response.content_type)
            events = parse_events(response)
        self.assertEqual([e["type"] for e in events], ["delta", "delta", "done"])
        self.assertEqual("".join(e["text"] for e in events[:2]), "Привет!")
        saved = self.client.get(f"/api/chats/{self.cid}").get_json()
        self.assertEqual(saved["messages"][-1]["content"], "Привет!")
        self.assertEqual(len(saved["messages"]), 2)

    def test_failure_does_not_persist_partial_completion(self):
        def broken(*args):
            yield "часть"
            raise RuntimeError("upstream disconnected")
        with patch.object(server, "stream_completion", side_effect=broken):
            response = self.client.post(
                f"/api/chats/{self.cid}/message?stream=1",
                data={"content": "Привет", "provider_id": self.pid},
            )
            events = parse_events(response)
        self.assertEqual([e["type"] for e in events], ["delta", "error"])
        self.assertIn("upstream disconnected", events[1]["error"])
        saved = self.client.get(f"/api/chats/{self.cid}").get_json()
        self.assertEqual(saved["messages"], [])


if __name__ == "__main__":
    unittest.main()
