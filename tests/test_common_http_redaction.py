from pathlib import Path
import sys
import traceback
import unittest
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import common


SECRET = "test-secret-service-key"
LEAKY_URL = f"https://example.invalid/api?serviceKey={SECRET}&pageNo=1"
LEAKY_BODY = "private upstream response body"


def rendered_exception(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


class RequestXmlRedactionTests(unittest.TestCase):
    def assert_redacted(self, error: BaseException) -> None:
        rendered = rendered_exception(error)
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn(LEAKY_URL, rendered)
        self.assertNotIn(LEAKY_BODY, rendered)

    def test_success_preserves_xml_return_contract(self) -> None:
        response = Mock(
            status_code=200,
            content=b"<response><resultCode>00</resultCode><resultMsg>OK</resultMsg></response>",
        )

        with (
            patch.object(common, "api_key", return_value=SECRET),
            patch.object(common.requests, "get", return_value=response) as request,
        ):
            root = common.request_xml("success-endpoint", {"pageNo": 1}, timeout=12)

        self.assertEqual(root.findtext(".//resultCode"), "00")
        request.assert_called_once_with(
            f"{common.API_BASE}/success-endpoint",
            params={"serviceKey": SECRET, "pageNo": 1},
            timeout=12,
        )
        response.close.assert_called_once()

    def test_http_error_reports_only_status_without_request_details(self) -> None:
        response = Mock(status_code=403, content=LEAKY_BODY.encode("utf-8"))

        with (
            patch.object(common, "api_key", return_value=SECRET),
            patch.object(common.requests, "get", return_value=response),
            self.assertRaises(RuntimeError) as caught,
        ):
            common.request_xml("leaky-endpoint", {"pageNo": 1})

        self.assertEqual(str(caught.exception), "공공데이터 API HTTP 오류: 403")
        self.assert_redacted(caught.exception)
        response.raise_for_status.assert_not_called()
        response.close.assert_called_once()

    def test_application_error_does_not_expose_upstream_message(self) -> None:
        response = Mock(
            status_code=200,
            content=(
                "<response><resultCode>99</resultCode>"
                f"<resultMsg>{SECRET} {LEAKY_BODY}</resultMsg></response>"
            ).encode("utf-8"),
        )

        with (
            patch.object(common, "api_key", return_value=SECRET),
            patch.object(common.requests, "get", return_value=response),
            self.assertRaises(RuntimeError) as caught,
        ):
            common.request_xml("application-error", {"pageNo": 1})

        self.assertEqual(str(caught.exception), "공공데이터 API 응답 오류(resultCode=99)")
        self.assert_redacted(caught.exception)
        response.close.assert_called_once()

    def test_malformed_xml_is_reported_without_payload(self) -> None:
        response = Mock(status_code=200, content=f"<broken>{SECRET}".encode("utf-8"))

        with (
            patch.object(common, "api_key", return_value=SECRET),
            patch.object(common.requests, "get", return_value=response),
            self.assertRaises(RuntimeError) as caught,
        ):
            common.request_xml("malformed", {"pageNo": 1})

        self.assertEqual(str(caught.exception), "공공데이터 API 응답 형식을 해석할 수 없습니다.")
        self.assert_redacted(caught.exception)
        response.close.assert_called_once()

    def test_connection_error_suppresses_sensitive_original_exception(self) -> None:
        transport_error = requests.ConnectionError(
            f"connection failed for {LEAKY_URL}: {LEAKY_BODY}"
        )

        with (
            patch.object(common, "api_key", return_value=SECRET),
            patch.object(common.requests, "get", side_effect=transport_error),
            self.assertRaises(RuntimeError) as caught,
        ):
            common.request_xml("leaky-endpoint", {"pageNo": 1})

        self.assertEqual(str(caught.exception), "공공데이터 API 연결 실패")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_redacted(caught.exception)

    def test_timeout_suppresses_sensitive_original_exception(self) -> None:
        transport_error = requests.ReadTimeout(
            f"request timed out for {LEAKY_URL}: {LEAKY_BODY}"
        )

        with (
            patch.object(common, "api_key", return_value=SECRET),
            patch.object(common.requests, "get", side_effect=transport_error),
            self.assertRaises(RuntimeError) as caught,
        ):
            common.request_xml("leaky-endpoint", {"pageNo": 1})

        self.assertEqual(str(caught.exception), "공공데이터 API 요청 시간 초과")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_redacted(caught.exception)


if __name__ == "__main__":
    unittest.main()
