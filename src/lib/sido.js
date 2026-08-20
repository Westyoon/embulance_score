// region_risk_final.csv 등 파이프라인 산출물은 "시도명|시군구명" 형태의
// 16개 시도 버킷을 사용한다 (전라남도+광주광역시가 "전남광주통합특별시"
// 하나로 합쳐진 파이프라인 고유 표기 포함). 반면 지도에 쓰는 KOSTAT 2013
// 단순화 경계(southkorea/southkorea-maps)는 예전 방식의 2자리 코드 접두사를
// 쓴다. 두 체계를 잇기 위한 매핑 테이블.
export const SIDO_GEO_PREFIXES = {
  "서울특별시": ["11"],
  "부산광역시": ["21"],
  "대구광역시": ["22"],
  "인천광역시": ["23"],
  "대전광역시": ["25"],
  "울산광역시": ["26"],
  "세종특별자치시": ["29"],
  "경기도": ["31"],
  "강원특별자치도": ["32"],
  "충청북도": ["33"],
  "충청남도": ["34"],
  "전북특별자치도": ["35"],
  "경상북도": ["37"],
  "경상남도": ["38"],
  "제주특별자치도": ["39"],
  // 파이프라인이 전라남도(36)와 광주광역시(24)를 하나의 버킷으로 합쳐 놓음
  "전남광주통합특별시": ["24", "36"],
};

export function splitRegionKey(key) {
  const idx = key.indexOf("|");
  if (idx === -1) return { sido: "", name: key };
  return { sido: key.slice(0, idx), name: key.slice(idx + 1) };
}

export function makeRegionKey(sido, name) {
  return `${sido}|${name}`;
}
