from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import part3_collect_hira_doctors as hira


class HiraHttpRetryTests(unittest.TestCase):
    def test_catalog_retries_transient_empty_success_response(self) -> None:
        catalog_row = {"ykiho": "A" * 80, "yadmNm": "테스트병원"}

        with (
            patch.object(
                hira,
                "fetch_catalog_page",
                side_effect=[([], 0), ([catalog_row], 1)],
            ) as fetch_page,
            patch.object(hira.time, "sleep") as sleep,
        ):
            result = hira.fetch_hira_catalog("local-test-key", workers=1, page_size=1000)

        self.assertEqual(result, [catalog_row])
        self.assertEqual(fetch_page.call_count, 2)
        sleep.assert_called_once_with(0.75)

    def test_catalog_rejects_repeated_empty_success_responses(self) -> None:
        with (
            patch.object(hira, "fetch_catalog_page", return_value=([], 0)) as fetch_page,
            patch.object(hira.time, "sleep") as sleep,
        ):
            with self.assertRaises(RuntimeError):
                hira.fetch_hira_catalog("local-test-key", workers=1, page_size=1000)

        self.assertEqual(fetch_page.call_count, hira.REQUEST_ATTEMPTS)
        self.assertEqual(sleep.call_count, hira.REQUEST_ATTEMPTS - 1)

    def test_retries_retry_after_throttling_then_succeeds(self) -> None:
        throttled = Mock(status_code=429, headers={"Retry-After": "0"})
        success = Mock(status_code=200, headers={}, content=b"<response />")
        session = Mock()
        session.get.side_effect = [throttled, success]

        with (
            patch.object(hira, "_http_session", return_value=session),
            patch.object(hira.time, "sleep") as sleep,
        ):
            result = hira.hira_get(
                "https://example.invalid/hira",
                params={"serviceKey": "local-test-key"},
                attempts=2,
            )

        self.assertIs(result, success)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args.kwargs["timeout"], (10, 60))
        sleep.assert_called_once_with(0.0)
        throttled.close.assert_called_once_with()

    def test_retries_read_timeout_with_exponential_backoff_then_succeeds(self) -> None:
        success = Mock(status_code=200, headers={}, content=b"<response />")
        session = Mock()
        session.get.side_effect = [requests.ReadTimeout("sensitive transport details"), success]

        with (
            patch.object(hira, "_http_session", return_value=session),
            patch.object(hira.time, "sleep") as sleep,
        ):
            result = hira.hira_get(
                "https://example.invalid/hira",
                params={"serviceKey": "local-test-key"},
                attempts=2,
            )

        self.assertIs(result, success)
        self.assertEqual(session.get.call_count, 2)
        sleep.assert_called_once_with(0.75)

    def test_terminal_error_does_not_expose_key_url_or_response_body(self) -> None:
        denied = Mock(
            status_code=403,
            headers={},
            text="private-response-body",
            content=b"private-response-body",
        )
        session = Mock()
        session.get.return_value = denied

        with patch.object(hira, "_http_session", return_value=session):
            with self.assertRaises(hira.HiraRequestError) as raised:
                hira.hira_get(
                    "https://example.invalid/private-path",
                    params={"serviceKey": "private-api-key"},
                    attempts=1,
                )

        message = str(raised.exception)
        self.assertIn("HTTP 403", message)
        self.assertNotIn("private-api-key", message)
        self.assertNotIn("private-path", message)
        self.assertNotIn("private-response-body", message)


if __name__ == "__main__":
    unittest.main()
