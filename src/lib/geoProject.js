// d3-geo 없이 위경도를 직접 SVG 좌표로 투영한다 (등장방형 근사 + 위도 보정).
export function computeBounds(geo) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      const [x, y] = c;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    } else {
      c.forEach(walk);
    }
  };
  geo.features.forEach((f) => walk(f.geometry.coordinates));
  return { minX, minY, maxX, maxY };
}

export function makeProjector(bounds, W, H, pad) {
  const { minX, minY, maxX, maxY } = bounds;
  const midLat = (minY + maxY) / 2;
  const k = Math.cos((midLat * Math.PI) / 180); // 위도에 따른 동서 방향 보정
  const geoW = (maxX - minX) * k;
  const geoH = maxY - minY;
  const scale = Math.min((W - pad * 2) / geoW, (H - pad * 2) / geoH);
  const offX = (W - geoW * scale) / 2;
  const offY = (H - geoH * scale) / 2;
  return (lon, lat) => [
    (lon - minX) * k * scale + offX,
    H - ((lat - minY) * scale + offY),
  ];
}

function ringPath(ring, project) {
  return (
    ring
      .map(([lon, lat], i) => {
        const [x, y] = project(lon, lat);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ") + "Z"
  );
}

export function geometryPath(geom, project) {
  if (!geom) return "";
  if (geom.type === "Polygon") return geom.coordinates.map((r) => ringPath(r, project)).join(" ");
  if (geom.type === "MultiPolygon")
    return geom.coordinates.map((poly) => poly.map((r) => ringPath(r, project)).join(" ")).join(" ");
  return "";
}

// 하이라이트 핀을 찍기 위한 정점 평균 기반 근사 중심점(무게중심은 아님, 꼭짓점 평균).
export function centroidOf(geom, project) {
  let sx = 0, sy = 0, n = 0;
  const addRing = (ring) => ring.forEach(([lon, lat]) => { const [x, y] = project(lon, lat); sx += x; sy += y; n++; });
  if (geom?.type === "Polygon") geom.coordinates.forEach(addRing);
  else if (geom?.type === "MultiPolygon") geom.coordinates.forEach((poly) => poly.forEach(addRing));
  return n ? [sx / n, sy / n] : [0, 0];
}
