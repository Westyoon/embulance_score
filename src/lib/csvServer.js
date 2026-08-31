import fs from "node:fs";
import path from "node:path";
import Papa from "papaparse";

export const DATA_DIR = process.env.PIPELINE_DATA_DIR
  ? path.resolve(process.env.PIPELINE_DATA_DIR)
  : path.join(process.cwd(), "data");

export const BOUNDARY_FILE = process.env.BOUNDARY_FILE
  ? path.resolve(process.env.BOUNDARY_FILE)
  : path.join(process.cwd(), "src", "data", "koreaGeo.json");

// 서버 컴포넌트/로더에서만 사용. papaparse로 CSV를 파싱해 객체 배열로 반환한다.
export function readCsv(filename) {
  const raw = fs.readFileSync(path.join(DATA_DIR, filename), "utf-8");
  // Git/Windows 도구가 CRLF 파일의 일부 행만 LF로 바꿔도 Papa Parse의 자동
  // 개행 감지가 행을 합치지 않도록 입력을 먼저 LF 하나로 정규화한다.
  const normalized = raw.replace(/\r\n?/g, "\n");
  const { data, errors } = Papa.parse(normalized, {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
    newline: "\n",
  });
  if (errors.length > 0) {
    const preview = errors.slice(0, 3).map((error) => (
      `${error.code} at row ${error.row ?? "?"}: ${error.message}`
    )).join("; ");
    throw new Error(`CSV parse failed (${filename}): ${preview}`);
  }
  return data;
}

export function readJson(filename) {
  const raw = fs.readFileSync(path.join(DATA_DIR, filename), "utf-8");
  return JSON.parse(raw);
}

export function readBoundaryJson() {
  const raw = fs.readFileSync(BOUNDARY_FILE, "utf-8");
  return JSON.parse(raw);
}
