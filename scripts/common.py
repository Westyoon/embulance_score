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


def api_key() -> str:
    value = os.getenv("DATA_GO_KR_API_KEY", "").strip()
    if not value:
        raise RuntimeError("환경변수 또는 .env에 DATA_GO_KR_API_KEY를 설정하세요.")
    return unquote(value)


def request_xml(endpoint: str, params: dict, timeout: int = 45) -> ET.Element:
    response = requests.get(
        f"{API_BASE}/{endpoint}",
        params={"serviceKey": api_key(), **params},
        timeout=timeout,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    code = root.findtext(".//resultCode")
    message = root.findtext(".//resultMsg")
    if code != "00":
        raise RuntimeError(f"공공데이터 API 오류: {code} {message}")
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

