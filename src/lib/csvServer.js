import fs from "node:fs";
import path from "node:path";
import Papa from "papaparse";

const DATA_DIR = path.join(process.cwd(), "data");

// 서버 컴포넌트/로더에서만 사용. papaparse로 CSV를 파싱해 객체 배열로 반환한다.
export function readCsv(filename) {
  const raw = fs.readFileSync(path.join(DATA_DIR, filename), "utf-8");
  const { data } = Papa.parse(raw, {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
  });
  return data;
}

export function readJson(filename) {
  const raw = fs.readFileSync(path.join(DATA_DIR, filename), "utf-8");
  return JSON.parse(raw);
}
