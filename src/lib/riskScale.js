// 위험도 색상 기준 (0~20 진초록 / 20~35 연초록 / 35~50 노랑 / 50~65 주황 / 65~ 빨강).
// 지도, 랭킹 차트 등 위험도 색상이 쓰이는 모든 화면에서 이 기준표를 그대로 가져다 쓴다.
export const RISK_LEVELS = [
  { label: "매우낮음", range: "0 ~ 20점", color: "#15803d", max: 20 },
  { label: "낮음", range: "20 ~ 35점", color: "#4ade80", max: 35 },
  { label: "보통", range: "35 ~ 50점", color: "#fbbf24", max: 50 },
  { label: "높음", range: "50 ~ 65점", color: "#f97316", max: 65 },
  { label: "매우높음", range: "65점 초과", color: "#dc2626", max: Infinity },
];

export const MISSING_COLOR = "#cbd5e1";

export function riskColor(v) {
  if (v == null || Number.isNaN(v)) return MISSING_COLOR;
  return (RISK_LEVELS.find((l) => v <= l.max) || RISK_LEVELS[RISK_LEVELS.length - 1]).color;
}

export function riskLabel(v) {
  if (v == null || Number.isNaN(v)) return "미산출";
  return (RISK_LEVELS.find((l) => v <= l.max) || RISK_LEVELS[RISK_LEVELS.length - 1]).label;
}

// RISK_LEVELS의 "낮음"(#4ade80)·"보통"(#fbbf24)은 흰 배경 위 작은 텍스트로
// 쓰면 대비가 낮아 잘 안 보인다. 지도 채우기·배지 배경·범례 스와치 등
// "면적"으로 쓰는 곳은 riskColor()를 그대로 쓰고, 숫자·라벨처럼 "텍스트"로
// 쓰는 곳만 이 함수로 대비가 확보된 색을 쓴다. 등급 구간(RISK_LEVELS) 자체는
// 건드리지 않는다.
const RISK_TEXT_OVERRIDE = { "낮음": "#15803d", "보통": "#b45309" };
export function riskTextColor(v) {
  if (v == null || Number.isNaN(v)) return "#64748b";
  const lvl = RISK_LEVELS.find((l) => v <= l.max) || RISK_LEVELS[RISK_LEVELS.length - 1];
  return RISK_TEXT_OVERRIDE[lvl.label] ?? lvl.color;
}

export const bedStatusColor = { "여유": "#22c55e", "주의": "#eab308", "포화": "#ef4444", "결측": "#64748b" };

// 클러스터 라벨은 KMeans가 매긴 0/1 인덱스가 실행마다 뒤바뀔 수 있으므로,
// cluster_profile.csv의 평균 위험도를 보고 "복합취약형(위험도가 더 높은 쪽)" /
// "양호형(위험도가 더 낮은 쪽)"을 동적으로 배정한다. 실제 배정은
// loadDashboardData.js에서 이루어짐 (아래는 라벨/색상 정의만 보관).
export const CLUSTER_LABELS = {
  vulnerable: { label: "접근성·의료진 복합취약형", color: "#fb7185" },
  moderate: { label: "접근성 양호·병상부담형", color: "#38bdf8" },
};
