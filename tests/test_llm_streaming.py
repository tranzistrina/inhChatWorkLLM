import json
import unittest

from llm_streaming import ndjson, stream_completion


PROVIDER = {"base_url": "http://localhost:9000/v1", "model": "test", "api_key": ""}


class FakeResponse:
    def __init__(self, lines=(), status=200, content_type="text/event-stream", payload=None):
        self.lines = lines
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP " + str(self.status_code))

    def iter_lines(self):
        return iter(self.lines)

    def json(self):
        return self.payload


class FakeTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class StreamingTests(unittest.TestCase):
    def test_russian_tokens_arrive_individually(self):
        lines = [
            b'data: ' + json.dumps({"choices": [{"delta": {"content": "Привет"}}]}, ensure_ascii=False).encode("utf-8"),
            b'data: ' + json.dumps({"choices": [{"delta": {"content": ", мир!"}}]}, ensure_ascii=False).encode("utf-8"),
            b'data: [DONE]',
        ]
        transport = FakeTransport([FakeResponse(lines)])
        result = stream_completion(PROVIDER, [{"role": "user", "content": "test"}], transport)
        self.assertEqual(next(result), "Привет")
        self.assertEqual(next(result), ", мир!")
        with self.assertRaises(StopIteration):
            next(result)
        self.assertTrue(transport.calls[0][1]["json"]["stream"])
        self.assertTrue(transport.calls[0][1]["stream"])

    def test_fallback_for_non_stream_provider(self):
        response = FakeResponse(
            content_type="application/json",
            payload={"choices": [{"message": {"content": "Готово"}}]},
        )
        self.assertEqual(list(stream_completion(PROVIDER, [], FakeTransport([response]))), ["Готово"])

    def test_404_retries_with_v1(self):
        p = dict(PROVIDER, base_url="http://localhost:9000")
        transport = FakeTransport([
            FakeResponse(status=404),
            FakeResponse([b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]']),
        ])
        self.assertEqual(list(stream_completion(p, [], transport)), ["ok"])
        self.assertTrue(transport.calls[1][0].endswith("/v1/chat/completions"))

    def test_error_interrupts_without_success(self):
        response = FakeResponse([b'data: {"error":{"message":"overloaded"}}'])
        with self.assertRaisesRegex(RuntimeError, "overloaded"):
            list(stream_completion(PROVIDER, [], FakeTransport([response])))

    def test_ndjson_unicode(self):
        self.assertEqual(json.loads(ndjson("delta", text="Привет")), {"type": "delta", "text": "Привет"})


if __name__ == "__main__":
    unittest.main()
