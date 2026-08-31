"use client";
import { X, MapPin, Building2, ChevronRight } from "lucide-react";
import { riskColor, riskTextColor, riskLabel, bedStatusColor, MISSING_COLOR } from "@/lib/riskScale";
import { cardStyle, mutedText } from "./shared";

const COMPONENTS = [
  { key: "bed", name: "병상포화도", weight: "35%" },
  { key: "access", name: "접근성", weight: "30%" },
  { key: "popBed", name: "인구대비병상", weight: "20%" },
  { key: "doc", name: "의료진부족", weight: "15%" },
];

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function BedCoverageDisclosure({ region }) {
  const bedDataHospitals = isFiniteNumber(region.bedDataHospitals)
    ? region.bedDataHospitals
    : 0;
  const totalHospitals = isFiniteNumber(region.totalHospitals)
    ? region.totalHospitals
    : (region.hospitals?.length ?? 0);
  return (
    <div style={{ fontSize: 10.5, ...mutedText, marginTop: 8 }}>
      병상 API 반영 기관 {bedDataHospitals} / {totalHospitals}
      {isFiniteNumber(region.bedDataCoverage)
        ? ` (${Math.round(region.bedDataCoverage * 100)}%)`
        : ""}
      {region.bedDataQuality ? ` · ${region.bedDataQuality}` : ""}
      <br />병상 구성점수는 해당 시점에 유효하게 보고한 기관 기준
    </div>
  );
}

export default function RegionPopup({ region, onClose, onSelectHospital }) {
  if (!region) return null;

  if (region.missing) {
    const missingComponents = region.missingComponents?.length
      ? region.missingComponents.join("·")
      : "필수 구성점수";
    return (
      <div style={{ ...cardStyle, padding: 20 }}>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <MapPin size={16} color={MISSING_COLOR} />
            <span style={{ fontSize: 16, fontWeight: 700 }}>{region.name}</span>
          </div>
          <button onClick={onClose} className="flex items-center gap-1" style={{ color: "#64748b", background: "none", border: "none", cursor: "pointer", fontSize: 10.5 }}>목록으로 <X size={15} /></button>
        </div>
        <div style={{ fontSize: 12, ...mutedText, marginTop: 14, lineHeight: 1.6, background: "#f8fafc", border: "1px solid #e2e8f0", padding: "12px 14px", borderRadius: 10 }}>
          {region.key
            ? <><b style={{ color: "#0f172a" }}>{missingComponents}</b> 원천값이 검증 기준을 충족하지 못해 최종 위험도가 <b style={{ color: "#0f172a" }}>산출되지 않은 지역</b>입니다. 0점이 아니라 <b style={{ color: "#0f172a" }}>&quot;데이터 부족으로 미산출&quot;</b>입니다.</>
            : <>행정안전부 2026-07-01 행정구역 체계를 반영한 최신 경계 데이터셋에 대응하는 <b style={{ color: "#0f172a" }}>원천 데이터가 없는 지역</b>입니다. 행정구역 개편이나 데이터 매칭 누락으로 아직 위험도가 연결되지 않았습니다.</>}
        </div>
        <BedCoverageDisclosure region={region} />
      </div>
    );
  }

  const data = COMPONENTS.map((c) => ({ ...c, value: region[c.key] }));
  const sorted = [...data].sort((a, b) => b.value - a.value);
  const [top1, top2] = sorted;
  const lvl = riskLabel(region.risk);
  const hospitals = region.hospitals?.length ? region.hospitals : null;
  const accessibilityRoute = region.accessibilityRoute;
  const hasRoadRoute = isFiniteNumber(accessibilityRoute?.roadDistanceKm)
    && isFiniteNumber(accessibilityRoute?.etaMin);
  const fallbackDistanceKm = isFiniteNumber(accessibilityRoute?.straightDistanceKm)
    ? accessibilityRoute.straightDistanceKm
    : (!hasRoadRoute && isFiniteNumber(accessibilityRoute?.distanceKm)
      ? accessibilityRoute.distanceKm
      : null);
  const shownDistanceKm = hasRoadRoute ? accessibilityRoute.roadDistanceKm : fallbackDistanceKm;

  return (
    <div style={{ ...cardStyle, padding: 20, maxHeight: 560, overflowY: "auto" }}>
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <MapPin size={16} color={riskColor(region.risk)} />
              <span style={{ fontSize: 16, fontWeight: 700 }}>{region.name}</span>
            </div>
            {region.clusterLabel && (
              <div style={{ marginTop: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: region.clusterColor, background: region.clusterColor + "1f", padding: "3px 8px", borderRadius: 999 }}>
                  {region.clusterLabel}
                </span>
              </div>
            )}
          </div>
          <button onClick={onClose} className="flex items-center gap-1" style={{ color: "#64748b", background: "none", border: "none", cursor: "pointer", fontSize: 10.5 }}>목록으로 <X size={15} /></button>
        </div>

        <div className="flex items-end gap-2" style={{ marginTop: 14 }}>
          <span style={{ fontSize: 34, fontWeight: 800, color: riskTextColor(region.risk), lineHeight: 1 }}>{region.risk.toFixed(1)}</span>
          <span style={{ ...mutedText, fontSize: 12, marginBottom: 4 }}>/ 100점</span>
          <span style={{ fontSize: 12, fontWeight: 700, color: riskTextColor(region.risk), marginBottom: 5, marginLeft: 4 }}>· {lvl} 등급</span>
        </div>

        <div style={{ marginTop: 14 }}>
          {data.map((d) => (
            <div key={d.name} style={{ marginBottom: 8 }}>
              <div className="flex justify-between" style={{ fontSize: 11, ...mutedText, marginBottom: 3 }}>
                <span>{d.name} <span style={{ opacity: 0.6 }}>({d.weight})</span></span>
                <span style={{ color: "#0f172a", fontVariantNumeric: "tabular-nums" }}>{d.value.toFixed(1)}점</span>
              </div>
              <div style={{ background: "#e2e8f0", height: 6, borderRadius: 4, overflow: "hidden" }}>
                <div style={{ width: `${Math.min(d.value, 100)}%`, height: "100%", background: d.name === top1.name ? "#f97316" : d.name === top2.name ? "#fbbf24" : "#cbd5e1" }} />
              </div>
            </div>
          ))}
        </div>

        <div style={{ fontSize: 11.5, ...mutedText, marginTop: 4, lineHeight: 1.55, background: "#f8fafc", border: "1px solid #e2e8f0", padding: "10px 12px", borderRadius: 10 }}>
          {region.name}의 종합 위험도는 <b style={{ color: "#0f172a" }}>{region.risk.toFixed(1)}점 ({lvl})</b>입니다.
          네 요인 중 <b style={{ color: "#0f172a" }}>{top1.name}</b>({top1.value.toFixed(0)}점)과{" "}
          <b style={{ color: "#0f172a" }}>{top2.name}</b>({top2.value.toFixed(0)}점)이 가장 큰 영향을 주고 있어{region.clusterLabel ? <>, <b style={{ color: region.clusterColor }}>{region.clusterLabel}</b> 지역으로 분석됩니다.</> : "입니다."}
        </div>

        <BedCoverageDisclosure region={region} />

        <div style={{ marginTop: 12, background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 10, padding: "10px 12px" }}>
          <div style={{ fontSize: 10, ...mutedText }}>접근성 선정 센터</div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a", marginTop: 2 }}>
            {accessibilityRoute?.destinationName ?? "미산출"}
          </div>
          {accessibilityRoute?.destinationOrgCode && (
            <div style={{ fontSize: 9.5, ...mutedText, marginTop: 1 }}>기관코드 {accessibilityRoute.destinationOrgCode}</div>
          )}
          <div className="flex" style={{ gap: 8, marginTop: 8 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 15, fontWeight: 800, color: "#0f172a" }}>
                {shownDistanceKm == null ? "미산출" : `${shownDistanceKm.toFixed(1)}km`}
              </div>
              <div style={{ fontSize: 9.5, ...mutedText }}>
                {hasRoadRoute ? "카카오 도로거리" : (fallbackDistanceKm == null ? "거리" : "직선거리 (대체)")}
              </div>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 15, fontWeight: 800, color: "#0f172a" }}>
                {hasRoadRoute ? `${Math.round(accessibilityRoute.etaMin)}분` : "미산출"}
              </div>
              <div style={{ fontSize: 9.5, ...mutedText }}>예상 소요시간</div>
            </div>
          </div>
          <div style={{ fontSize: 9.5, color: "#94a3b8", marginTop: 6 }} title={accessibilityRoute?.routeStatus ?? undefined}>
            {hasRoadRoute
              ? "* 지역 대표점 기준 · 카카오모빌리티 도로 경로"
              : fallbackDistanceKm != null
                ? "* 지역 대표점 기준 · 카카오 경로 미산출로 직선거리 대체 · 예상시간 미산출"
                : "* 지역 대표점 기준 · 경로 데이터 미산출"}
          </div>
        </div>

        <div style={{ marginTop: 16 }}>
          <div className="flex items-center gap-1.5" style={{ fontSize: 12, fontWeight: 600, marginBottom: 8, ...mutedText }}>
            <Building2 size={13} /> 소속 의료기관 {hospitals && <span style={{ fontWeight: 400 }}>({hospitals.length})</span>}
          </div>
          {hospitals ? hospitals.map((h, i) => (
            <div key={h.name + i} className="flex items-center justify-between gap-2" onClick={() => onSelectHospital?.(h)}
              style={{ padding: "7px 0", borderTop: i ? "1px solid #e2e8f0" : "none", cursor: onSelectHospital ? "pointer" : "default" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12.5 }}>{h.name}</div>
                <div style={{ fontSize: 10.5, ...mutedText }}>{h.grade}</div>
              </div>
              <span className="flex items-center gap-1.5" style={{ fontSize: 10.5, flexShrink: 0, whiteSpace: "nowrap" }}>
                <span style={{ width: 7, height: 7, borderRadius: 99, background: bedStatusColor[h.status] }} />
                <span style={{ color: bedStatusColor[h.status] }}>{h.status}</span>
                {onSelectHospital && <ChevronRight size={12} color="#94a3b8" />}
              </span>
            </div>
          )) : (
            <div style={{ fontSize: 11.5, ...mutedText }}>등록된 의료기관 정보가 없습니다.</div>
          )}
        </div>
    </div>
  );
}
