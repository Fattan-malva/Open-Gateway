import os
import unittest
from unittest.mock import patch

from gateway import media


class FakeResponse:
    def __init__(self, status_code=402, payload=None, headers=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("bukan json")
        return self._payload


class PollinationsErrorTests(unittest.TestCase):
    def test_free_limit_body_reports_retry_seconds(self):
        response = FakeResponse(
            payload={
                "status": 402,
                "success": False,
                "error": {
                    "code": "FREE_LIMIT",
                    "message": "Free limit reached: 1 image every 45 seconds per IP. Retry after 44s",
                },
            }
        )
        message = media._pollinations_error(response)
        self.assertIn("44 detik", message)
        self.assertIn("Batas gambar gratis", message)

    def test_free_limit_header_is_used_when_body_is_empty(self):
        response = FakeResponse(status_code=402, headers={"retry-after": "22"})
        self.assertIn("22 detik", media._pollinations_error(response))

    def test_busy_service_is_reported(self):
        response = FakeResponse(status_code=503)
        self.assertIn("sibuk", media._pollinations_error(response))

    def test_other_failure_keeps_status_code(self):
        response = FakeResponse(status_code=500)
        self.assertIn("HTTP 500", media._pollinations_error(response))


class PollinationsTokenTests(unittest.TestCase):
    def test_token_is_read_from_environment(self):
        with patch.dict(os.environ, {"OG_POLLINATIONS_TOKEN": "abc"}, clear=False):
            self.assertEqual(media.pollinations_token(), "abc")

    def test_fallback_environment_variable(self):
        with patch.dict(
            os.environ,
            {"OG_POLLINATIONS_TOKEN": "", "POLLINATIONS_TOKEN": "xyz"},
            clear=False,
        ):
            self.assertEqual(media.pollinations_token(), "xyz")

    def test_missing_token_is_empty(self):
        with patch.dict(
            os.environ,
            {"OG_POLLINATIONS_TOKEN": "", "POLLINATIONS_TOKEN": ""},
            clear=False,
        ):
            self.assertEqual(media.pollinations_token(), "")


class NormalizeSizeTests(unittest.TestCase):
    def test_known_size(self):
        self.assertEqual(media.normalize_size("1792x1024"), "1792x1024")

    def test_unknown_size_falls_back_to_default(self):
        self.assertEqual(media.normalize_size("999x999"), media.DEFAULT_IMAGE_SIZE)

    def test_speech_cleaning_drops_code_and_links(self):
        text = media.clean_for_speech("Halo ```print(1)``` lihat https://contoh.test ya")
        self.assertNotIn("print", text)
        self.assertNotIn("https://", text)


if __name__ == "__main__":
    unittest.main()
