import argparse
import csv
import io
from html.parser import HTMLParser
from pathlib import Path
import re

import requests

from common import DATA_DIR

MOIS_PAGE_URL = "https://jumin.mois.go.kr/ageStatMonth.do"
MOIS_DOWNLOAD_URL = "https://jumin.mois.go.kr/downloadCsvAge.do"


class DownloadFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_download_form = False
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.in_download_form = attributes.get("id") == "formXlsDown"
        elif self.in_download_form and tag == "input" and attributes.get("name"):
            self.values[attributes["name"]] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_download_form:
            self.in_download_form = False


def latest_published_period(session: requests.Session) -> tuple[str, dict[str, str]]:
    response = session.get(MOIS_PAGE_URL, timeout=45)
    response.raise_for_status()
    parser = DownloadFormParser()
    parser.feed(response.text)
    year = parser.values.get("searchYearStart", "")
    month = parser.values.get("searchMonthStart", "")
    if not (year.isdigit() and len(year) == 4 and month.isdigit() and len(month) == 2):
        raise RuntimeError("행정안전부 주민등록 인구의 최신 공표 연월을 확인하지 못했습니다.")
    return f"{year}{month}", parser.values


def download_population(period: str | None = None) -> Path:
    with requests.Session() as session:
        session.headers["User-Agent"] = "embulance-score-pipeline/1.0"
        latest_period, form_values = latest_published_period(session)
        selected_period = period or latest_period
        if len(selected_period) != 6 or not selected_period.isdigit():
            raise ValueError("--period는 YYYYMM 형식이어야 합니다.")
        if selected_period > latest_period:
            raise ValueError(f"{selected_period} 자료는 아직 공표되지 않았습니다. 최신={latest_period}")

        year, month = selected_period[:4], selected_period[4:]
        payload = {
            **form_values,
            "sltOrgType": "1",
            "sltOrgLvl1": "A",
            "sltOrgLvl2": "",
            "gender": "gender",
            "sum": "sum",
            "searchYearStart": year,
            "searchMonthStart": month,
            "searchYearEnd": year,
            "searchMonthEnd": month,
            "sltOrderType": "1",
            "sltOrderValue": "ASC",
            "sltArgTypes": "10",
            "sltArgTypeA": "0",
            "sltArgTypeB": "100",
            "category": "month",
            "state": "2",
        }
        response = session.post(
            MOIS_DOWNLOAD_URL,
            params={"searchYearMonth": "month", "xlsStats": "2"},
            data=payload,
            timeout=60,
        )
        response.raise_for_status()
        try:
            decoded = response.content.decode("cp949")
        except UnicodeDecodeError as exc:
            raise RuntimeError("행정안전부 CSV 인코딩이 예상한 CP949가 아닙니다.") from exc
        reader = csv.reader(io.StringIO(decoded))
        header = next(reader, [])
        expected_header_period = f"{year}년{month}월"
        header_periods = {
            match.group(1)
            for column in header
            if (match := re.match(r"^(\d{4}년\d{2}월)_", column))
        }
        rows = list(reader)
        administrative_codes = [
            match.group(1)
            for row in rows
            if row and (match := re.search(r"\((\d{10})\)\s*$", row[0]))
        ]
        if (
            not header
            or header[0] != "행정구역"
            or header_periods != {expected_header_period}
            or len(response.content) < 10_000
            or len(administrative_codes) < 250
            or len(set(administrative_codes)) != len(administrative_codes)
        ):
            raise RuntimeError("행정안전부 주민등록 인구 CSV 응답을 검증하지 못했습니다.")

        output = DATA_DIR / f"mois_population_{selected_period}.csv"
        temporary = output.with_suffix(".tmp")
        temporary.write_bytes(response.content)
        temporary.replace(output)
        return output


def main() -> None:
    parser = argparse.ArgumentParser(description="행정안전부 최신 시군구 주민등록 인구 수집")
    parser.add_argument("--period", help="수집할 연월(YYYYMM). 생략하면 최신 공표 연월")
    args = parser.parse_args()
    output = download_population(args.period)
    print(f"Saved MOIS population source: {output}")


if __name__ == "__main__":
    main()
