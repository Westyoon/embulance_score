// 상관계수(r)를 그대로 보여주지 않고 5단계 강도 라벨 + 퍼센트로 변환한다.
// 빨강 계열 그라데이션 — 연할수록 약함, 진할수록(적갈색) 강함.
export const CORR_LEVELS = [
  { label: "매우 약함", min: 0, color: "#fca5a5" },
  { label: "약함", min: 0.2, color: "#f87171" },
  { label: "보통", min: 0.4, color: "#ef4444" },
  { label: "강함", min: 0.6, color: "#b91c1c" },
  { label: "매우 강함", min: 0.8, color: "#7f1d1d" },
];

export function corrLevel(r) {
  const a = Math.abs(r);
  let lvl = CORR_LEVELS[0];
  CORR_LEVELS.forEach((l) => {
    if (a >= l.min) lvl = l;
  });
  return lvl;
}

// 종합위험도와 각 구성요소 간 상관관계를 설명하는 한 줄 해설.
// correlation_matrix.csv 값 자체는 팀 리포트가 확인한 값(접근성 0.80,
// 의료진부족 0.67, 병상포화도 0.45, 인구대비병상 0.10)과 일치하며,
// 이 해설 문장도 그 해석을 그대로 따른다.
export const CORR_INSIGHTS = {
  "접근성점수": "응급의료센터 접근이 어려운 지역일수록 종합위험도도 함께 높아지는 경향이 네 요인 중 가장 뚜렷합니다.",
  "의료진부족점수": "응급의학과 전문의가 부족한 지역일수록 위험도가 높아지는 경향이 뚜렷합니다.",
  "병상포화도점수": "병상이 포화된 지역은 위험도가 다소 높아지지만, 접근성·의료진만큼 강하게 일치하지는 않습니다.",
  "인구대비병상점수": "인구 대비 병상 수만으로는 지역 간 위험도 차이가 잘 설명되지 않습니다.",
};
