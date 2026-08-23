// HIRA/병원 실데이터가 아직 세부 항목(응급실 카테고리별 병상, 질환별
// 수용가능여부, 병원 단위 실거리)까지 연동되지 않은 부분을 채우는 결정론적
// (seeded) 자리채움 데이터 생성기. 이미 연동된 실데이터(병원 기본정보,
// 시군구 단위 병상 상태·포화율, 위험도 구성점수)는 이 파일을 거치지 않는다.
// 실데이터 연동 시 이 파일의 함수만 실제 API/CSV 집계로 교체하면 된다.

export function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) { h = (h << 5) - h + str.charCodeAt(i); h |= 0; }
  return h;
}
export function seededRandom(seed) {
  let t = (seed += 0x6d2b79f5);
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

// 버블차트용 지역별 응급실 수 / 의료진 수. hospital_master.csv를 지역 단위로
// 집계하는 로직으로 교체하기 전까지의 자리채움 — 의료진부족점수(docScore)가
// 높을수록(=부족할수록) 의료진 수가 적게 나오도록 최소한의 논리적 일관성만
// 맞췄다.
export function facilityCounts(regionKey, name, docScore) {
  const isCounty = name.endsWith("군");
  const rh = seededRandom(hashCode(regionKey + "hosp"));
  const hospitalCount = isCounty ? 1 + Math.round(rh * 3) : 3 + Math.round(rh * 9);
  const rd = seededRandom(hashCode(regionKey + "doc3"));
  const scarcity = (docScore ?? 0) / 100; // 0=충분, 1=매우 부족
  const maxDoctors = isCounty ? 12 : 30;
  const doctorCount = Math.max(0, Math.round(maxDoctors * (1 - scarcity) * (0.6 + rd * 0.6)));
  return { hospitalCount, doctorCount };
}

export const BED_ITEMS = [
  { key: "general", label: "일반 응급실" },
  { key: "pediatric", label: "소아" },
  { key: "severe", label: "중증/중환자" },
  { key: "surgery", label: "수술실" },
];
export const CAPA_DISEASES = ["심근경색", "뇌졸중", "중증외상", "소아응급"];
export const CAPA_COLOR = { "가능": "#22c55e", "제한": "#eab308", "불가": "#ef4444", "미확인": "#94a3b8" };

const STATUS_BED_BASE = { "여유": 0.55, "주의": 0.3, "포화": 0.08 };

// 병원 상세 팝업(HD-02 카테고리별 병상, HD-03 수용가능여부, HD-04 거리)용
// 자리채움. 병원 고유 기관코드로 시드를 고정해 같은 병원은 항상 같은 값이
// 나오게 한다. bed_status.csv가 "미갱신"(결측)으로 표시한 병원은 카테고리별
// 병상도 전부 "미확인"으로 둔다 — 0으로 표시하면 "병상이 없다"로 오해할 수
// 있기 때문.
export function enrichHospital(h) {
  const seed = hashCode((h.orgCode || h.name) + (h.region || ""));

  const beds = {};
  BED_ITEMS.forEach((b, i) => {
    if (h.status === "결측") { beds[b.key] = { label: b.label, total: null, avail: null }; return; }
    const total = 3 + Math.round(seededRandom(seed + i * 7 + 1) * 15);
    const base = STATUS_BED_BASE[h.status] ?? 0.3;
    const rate = Math.min(1, Math.max(0, base + (seededRandom(seed + i * 7 + 2) - 0.5) * 0.3));
    beds[b.key] = { label: b.label, total, avail: Math.round(total * rate) };
  });

  const capability = {};
  CAPA_DISEASES.forEach((d, i) => {
    if (h.status === "결측") { capability[d] = "미확인"; return; }
    const r = seededRandom(seed + i * 11 + 3);
    if (h.status === "포화") capability[d] = r < 0.5 ? "불가" : r < 0.85 ? "제한" : "가능";
    else if (h.status === "주의") capability[d] = r < 0.15 ? "불가" : r < 0.5 ? "제한" : "가능";
    else capability[d] = r < 0.06 ? "제한" : "가능";
  });

  const distanceKm = 1.5 + seededRandom(seed + 99) * 22;
  const etaMin = Math.round((distanceKm / 50) * 60); // 구급차 평균 50km/h 참고값 — 실경로 API 적용 전 MVP 방식

  return { ...h, beds, capability, distanceKm, etaMin };
}
