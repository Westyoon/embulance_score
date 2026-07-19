import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT / ".env")

API_BASE = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"


def api_key() -> str:
    value = os.getenv("DATA_GO_KR_API_KEY", "").strip()
    if not value:
        raise RuntimeError(".env에 DATA_GO_KR_API_KEY를 설정하세요.")
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
    frame.to_csv(path, index=False, encoding="utf-8-sig")

