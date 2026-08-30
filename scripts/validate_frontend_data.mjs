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
  "accessibility_score.csv",
  "kakao_route_accessibility.csv",
  "kakao_hospital_routes.csv",
  "region_route_origins.csv",
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
const accessibility = parsed.get("accessibility_score.csv");
const accessibilityRoutes = parsed.get("kakao_route_accessibility.csv");
const hospitalRoutes = parsed.get("kakao_hospital_routes.csv");
const routeOrigins = parsed.get("region_route_origins.csv");
const hospitalCodes = uniqueValues(hospitals, "기관코드", "hospital_master.csv");
const bedCodes = uniqueValues(beds, "기관코드", "bed_status.csv");
const riskCodes = uniqueValues(risks, "시군구코드", "region_risk_final.csv");
const accessibilityCodes = uniqueValues(accessibility, "시군구코드", "accessibility_score.csv");
const accessibilityRouteCodes = uniqueValues(accessibilityRoutes, "시군구코드", "kakao_route_accessibility.csv");
const hospitalRouteCodes = uniqueValues(hospitalRoutes, "기관코드", "kakao_hospital_routes.csv");
const routeOriginCodes = uniqueValues(routeOrigins, "시군구코드", "region_route_origins.csv");

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

const sameSet = (left, right) => left.size === right.size && [...left].every((value) => right.has(value));
const successStatuses = new Set(["성공", "성공:출도착5m이내"]);
const missing = (value) => value == null || value === "";
const finiteNonnegative = (value) => !missing(value) && Number.isFinite(Number(value)) && Number(value) >= 0;

if (
  accessibility.length !== 219
  || accessibilityRoutes.length !== 219
  || routeOrigins.length !== 219
  || !sameSet(riskCodes, accessibilityCodes)
  || !sameSet(riskCodes, accessibilityRouteCodes)
  || !sameSet(riskCodes, routeOriginCodes)
) {
  throw new Error("프론트 카카오 접근성 CSV가 219개 위험도 지역과 일치하지 않습니다.");
}
if (hospitalRoutes.length !== 534 || !sameSet(hospitalCodes, hospitalRouteCodes)) {
  throw new Error("프론트 카카오 병원 경로 CSV가 NEMC 534기관과 일치하지 않습니다.");
}
for (const [filename, rows] of [
  ["kakao_route_accessibility.csv", accessibilityRoutes],
  ["kakao_hospital_routes.csv", hospitalRoutes],
]) {
  for (const row of rows) {
    if (
      !finiteNonnegative(row["직선거리_km"])
      || row["경로우선순위"] !== "DISTANCE"
      || row["경계버전"] !== 20260701
      || !/^[0-9a-f]{64}$/.test(String(row["경로요청키"] ?? ""))
    ) {
      throw new Error(`${filename}: 프론트에서 사용할 수 없는 카카오 경로 행이 있습니다.`);
    }
    const success = successStatuses.has(row["경로상태"]);
    if (
      (success && (
        !finiteNonnegative(row["도로거리_km"])
        || !finiteNonnegative(row["예상시간_분"])
        || ![0, 104].includes(Number(row["경로결과코드"]))
      ))
      || (!success && (
        !missing(row["도로거리_km"])
        || !missing(row["예상시간_분"])
      ))
    ) {
      throw new Error(`${filename}: 성공 여부와 카카오 도로거리 값이 일치하지 않습니다.`);
    }
    if (
      success
      && (Number(row["도로거리_km"]) === 0 || Number(row["예상시간_분"]) === 0)
      && Number(row["경로결과코드"]) !== 104
    ) {
      throw new Error(`${filename}: result_code=104가 아닌 0 거리·시간이 있습니다.`);
    }
  }
}
if (accessibilityRoutes.some((row) => (
  !successStatuses.has(row["경로상태"])
      || !finiteNonnegative(row["도로거리_km"])
      || !finiteNonnegative(row["예상시간_분"])
))) {
  throw new Error("kakao_route_accessibility.csv는 219개 지역 모두 성공 경로여야 합니다.");
}
const hospitalRouteSuccesses = hospitalRoutes.filter((row) => successStatuses.has(row["경로상태"])).length;
if (hospitalRouteSuccesses < Math.ceil(hospitalRoutes.length * 0.95)) {
  throw new Error(
    `kakao_hospital_routes.csv 성공률이 95% 미만입니다: ${hospitalRouteSuccesses}/${hospitalRoutes.length}`,
  );
}
if (accessibility.some((row) => (
  row["거리기준"] !== "카카오자동차최단거리경로"
  || !finiteNonnegative(row["도로거리_km"])
  || !finiteNonnegative(row["예상시간_분"])
  || !finiteNonnegative(row["접근성점수"])
))) {
  throw new Error("accessibility_score.csv의 카카오 거리·점수 계약이 올바르지 않습니다.");
}

const regressionVariables = new Set(parsed.get("regression_result.csv").map((row) => row["변수명"]));
if (!regressionVariables.has("도로거리_km") || regressionVariables.has("직선거리_km")) {
  throw new Error("프론트 회귀 데이터가 카카오 도로거리를 사용하지 않습니다.");
}

console.log(
  `Frontend CSV contract: hospitals=${hospitals.length}, beds=${beds.length}, regions=${risks.length}, Kakao routes=${hospitalRoutes.length + accessibilityRoutes.length}`,
);
