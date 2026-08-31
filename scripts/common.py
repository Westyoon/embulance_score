import os
import xml.etree.ElementTree as ET
import json
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PIPELINE_DATA_DIR", ROOT / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT / ".env")

API_BASE = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"


class PublicDataApiError(RuntimeError):
    """A redacted public-data API error with retry metadata."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        status_code: int | None = None,
        result_code: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.result_code = result_code
        self.retry_after = retry_after


def api_key() -> str:
    value = os.getenv("DATA_GO_KR_API_KEY", "").strip()
    if not value:
        raise RuntimeError("환경변수 또는 .env에 DATA_GO_KR_API_KEY를 설정하세요.")
    return unquote(value)


def request_xml(endpoint: str, params: dict, timeout: int = 45) -> ET.Element:
    try:
        response = requests.get(
            f"{API_BASE}/{endpoint}",
            params={"serviceKey": api_key(), **params},
            timeout=timeout,
        )
    except requests.Timeout:
        # requests 예외 문자열에는 serviceKey가 포함된 최종 URL이 들어갈 수 있다.
        # 원본 예외 체인도 숨겨 표준 traceback에 인증정보가 노출되지 않게 한다.
        raise PublicDataApiError(
            "공공데이터 API 요청 시간 초과",
            kind="timeout",
        ) from None
    except requests.ConnectionError:
        raise PublicDataApiError(
            "공공데이터 API 연결 실패",
            kind="connection",
        ) from None
    except requests.RequestException:
        raise PublicDataApiError(
            "공공데이터 API 요청 실패",
            kind="request",
        ) from None

    if not 200 <= response.status_code < 300:
        # raise_for_status()의 HTTPError에는 query string과 응답 본문이 포함될 수
        # 있으므로 상태코드만 새 예외에 전달한다.
        status_code = response.status_code
        retry_after = response.headers.get("Retry-After") if status_code == 429 else None
        if not isinstance(retry_after, str):
            retry_after = None
        response.close()
        raise PublicDataApiError(
            f"공공데이터 API HTTP 오류: {status_code}",
            kind="http",
            status_code=status_code,
            retry_after=retry_after,
        )

    content = response.content
    response.close()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise PublicDataApiError(
            "공공데이터 API 응답 형식을 해석할 수 없습니다.",
            kind="parse",
        ) from None
    raw_code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    if raw_code is None or not str(raw_code).strip():
        raise PublicDataApiError(
            "공공데이터 API 응답 형식을 해석할 수 없습니다.",
            kind="parse",
        )
    code = str(raw_code).strip()
    if code != "00":
        safe_code = "".join(character for character in code if character.isalnum() or character in "_-")[:20]
        if not safe_code:
            raise PublicDataApiError(
                "공공데이터 API 응답 형식을 해석할 수 없습니다.",
                kind="parse",
            )
        raise PublicDataApiError(
            f"공공데이터 API 응답 오류(resultCode={safe_code})",
            kind="result",
            result_code=safe_code,
        )
    return root


def xml_items(root: ET.Element) -> list[dict]:
    return [{child.tag: child.text for child in item} for item in root.findall(".//item")]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, dtype={"기관코드": "string", "시군구코드": "string"})


def save_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

