// 필터 pill, 범례처럼 자리가 좁은 UI에 쓰는 시도 축약 표기.
// 2026-07-01 출범한 전남광주통합특별시도 함께 표기한다.
export const SIDO_SHORT_LABELS = {
  "서울특별시": "서울",
  "부산광역시": "부산",
  "대구광역시": "대구",
  "인천광역시": "인천",
  "대전광역시": "대전",
  "울산광역시": "울산",
  "세종특별자치시": "세종",
  "경기도": "경기",
  "강원특별자치도": "강원",
  "충청북도": "충북",
  "충청남도": "충남",
  "전북특별자치도": "전북",
  "경상북도": "경북",
  "경상남도": "경남",
  "제주특별자치도": "제주",
  "전남광주통합특별시": "전남·광주",
};

export function splitRegionKey(key) {
  const idx = key.indexOf("|");
  if (idx === -1) return { sido: "", name: key };
  return { sido: key.slice(0, idx), name: key.slice(idx + 1) };
}

export function makeRegionKey(sido, name) {
  return `${sido}|${name}`;
}
