import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from media_service import generate_image, image_data_url, multimodal_content


class MediaServiceTests(unittest.TestCase):
    def test_image_data_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            path.write_bytes(b"png")
            self.assertEqual(image_data_url(path), "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\npng").decode("ascii"))

    def test_multimodal_content_contains_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jpg"
            path.write_bytes(b"jpg")
            content = multimodal_content("describe", [path])
            self.assertEqual(content[0], {"type": "text", "text": "describe"})
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_generate_image_saves_b64_response(self):
        session = Mock()
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\nfake-image").decode("ascii")}]
        }
        session.post.return_value = response

        import media_service
        old = media_service.requests
        media_service.requests = session
        try:
            with tempfile.TemporaryDirectory() as tmp:
                provider = {"base_url": "https://example.com/v1", "api_key": "key", "model": "image-model"}
                result = generate_image(provider, "a test image", tmp)
                self.assertEqual(result[0].read_bytes(), b"\x89PNG\r\n\x1a\nfake-image")
                self.assertEqual(session.post.call_count, 1)
        finally:
            media_service.requests = old


if __name__ == "__main__":
    unittest.main()
