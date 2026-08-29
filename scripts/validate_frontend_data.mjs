import { readCsv } from "../src/lib/csvServer.js";

const parsed = new Map();
for (const filename of [
  "hospital_master.csv",
  "bed_status.csv",
  "region_risk_final.csv",
  "cluster_result.csv",
  "cluster_profile.csv",
  "correlation_matrix.csv",
  "regression_result.csv",
]) {
  parsed.set(filename, readCsv(filename));
}

function uniqueValues(rows, field, filename) {
  const values = rows.map((row) => row[field]);
  if (values.some((value) => value == null || value === "") || new Set(values).size !== values.length) {
    throw new Error(`${filename}: ${field} 값이 비어 있거나 중복됩니다.`);
  }
  return new Set(values);
}

const hospitals = parsed.get("hospital_master.csv");
const beds = parsed.get("bed_status.csv");
const risks = parsed.get("region_risk_final.csv");
const hospitalCodes = uniqueValues(hospitals, "기관코드", "hospital_master.csv");
const bedCodes = uniqueValues(beds, "기관코드", "bed_status.csv");
const riskCodes = uniqueValues(risks, "시군구코드", "region_risk_final.csv");

if (hospitals.length !== 534 || beds.length !== 534 || risks.length !== 219) {
  throw new Error(
    `프론트 CSV 모집단 불일치: hospitals=${hospitals.length}, beds=${beds.length}, regions=${risks.length}`,
  );
}
if (hospitalCodes.size !== bedCodes.size || [...hospitalCodes].some((code) => !bedCodes.has(code))) {
  throw new Error("프론트 CSV의 병원 마스터와 병상 기관코드가 일치하지 않습니다.");
}
if (riskCodes.size !== 219) {
  throw new Error(`프론트 위험도 지역 키 불일치: ${riskCodes.size}`);
}

console.log(
  `Frontend CSV contract: hospitals=${hospitals.length}, beds=${beds.length}, regions=${risks.length}`,
);
