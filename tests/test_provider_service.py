import unittest
from unittest.mock import Mock

from provider_service import (
    ProviderConfigError,
    auth_headers,
    candidate_base_urls,
    discover_models,
    extract_models,
    normalize_base_url,
)


class ProviderServiceTests(unittest.TestCase):
    def test_normalize_removes_resource_suffixes(self):
        self.assertEqual(
            normalize_base_url("https://example.com/v1/models"),
            "https://example.com/v1",
        )
        self.assertEqual(
            normalize_base_url("https://example.com/v1/chat/completions/"),
            "https://example.com/v1",
        )

    def test_candidate_urls_support_host_without_v1(self):
        self.assertEqual(
            candidate_base_urls("https://example.com"),
            ["https://example.com", "https://example.com/v1"],
        )

    def test_extract_models_accepts_openai_and_simple_shapes(self):
        self.assertEqual(
            extract_models({"data": [{"id": "a"}, {"id": "a"}, {"id": "b"}]}),
            ["a", "b"],
        )
        self.assertEqual(extract_models({"models": ["a", "b"]}), ["a", "b"])

    def test_discovery_falls_back_to_v1(self):
        session = Mock()
        first = Mock()
        first.raise_for_status.side_effect = Exception("404")
        second = Mock()
        second.raise_for_status.return_value = None
        second.json.return_value = {"data": [{"id": "qwen"}]}
        session.get.side_effect = [first, second]

        result = discover_models("https://example.com", "secret", session=session)

        self.assertEqual(result.models, ["qwen"])
        self.assertEqual(result.base_url, "https://example.com/v1")
        self.assertEqual(session.get.call_args_list[1].kwargs["headers"], {"Authorization": "Bearer secret"})

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(ProviderConfigError):
            normalize_base_url("example.com/v1")

    def test_empty_models_are_rejected(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": []}
        session.get.return_value = response

        with self.assertRaises(ProviderConfigError):
            discover_models("https://example.com/v1", session=session)

    def test_auth_headers_can_be_empty(self):
        self.assertEqual(auth_headers(""), {})


if __name__ == "__main__":
    unittest.main()
