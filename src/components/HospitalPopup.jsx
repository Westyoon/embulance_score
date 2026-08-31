"use client";
import { X, ChevronLeft, Building2 } from "lucide-react";
import { riskColor, bedStatusColor } from "@/lib/riskScale";
import { BED_ITEMS, CAPA_DISEASES, CAPA_COLOR } from "@/lib/mockDetail";
import { cardStyle, mutedText } from "./shared";

const COMPONENTS = [
  { key: "bed", name: "병상포화도", weight: "35%" },
  { key: "access", name: "접근성", weight: "30%" },
  { key: "popBed", name: "인구대비병상", weight: "20%" },
  { key: "doc", name: "의료진부족", weight: "15%" },
];

function formatUpdatedAt(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export default function HospitalPopup({ hospital, region, onClose, onBack }) {
  if (!hospital) return null;
  const bedEntries = Object.values(hospital.beds ?? {});
  const updatedLabel = formatUpdatedAt(hospital.updatedAt);
  const routeUpdatedLabel = formatUpdatedAt(hospital.routeUpdatedAt);
  const hasRoadRoute = isFiniteNumber(hospital.roadDistanceKm) && isFiniteNumber(hospital.etaMin);
  const fallbackDistanceKm = isFiniteNumber(hospital.straightDistanceKm)
    ? hospital.straightDistanceKm
    : (!hasRoadRoute && isFiniteNumber(hospital.distanceKm) ? hospital.distanceKm : null);
  const shownDistanceKm = hasRoadRoute ? hospital.roadDistanceKm : fallbackDistanceKm;

  return (
    <div style={{ ...cardStyle, padding: 20 }}>
        <div className="flex items-start justify-between">
          <div>
            {onBack && (
              <button onClick={onBack} className="flex items-center gap-1" style={{ background: "none", border: "none", color: "#64748b", fontSize: 10.5, cursor: "pointer", marginBottom: 6, padding: 0 }}>
                <ChevronLeft size={13} /> {hospital.region}로 돌아가기
              </button>
            )}
            <div className="flex items-center gap-2">
              <Building2 size={16} color={bedStatusColor[hospital.status]} />
              <span style={{ fontSize: 15, fontWeight: 700 }}>{hospital.name}</span>
            </div>
            <div className="flex items-center" style={{ marginTop: 6, gap: 6 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "#64748b", background: "#f1f5f9", padding: "3px 8px", borderRadius: 999 }}>{hospital.grade}</span>
              <span className="flex items-center gap-1.5" style={{ fontSize: 11 }}>
                <span style={{ width: 7, height: 7, borderRadius: 99, background: bedStatusColor[hospital.status] }} />
                <span style={{ color: bedStatusColor[hospital.status] }}>{hospital.status}</span>
              </span>
            </div>
          </div>
          <button onClick={onClose} className="flex items-center gap-1" style={{ color: "#64748b", background: "none", border: "none", cursor: "pointer", fontSize: 10.5 }}>목록으로 <X size={15} /></button>
        </div>

        {/* HD-01 기본정보 */}
        <div style={{ marginTop: 14, fontSize: 11.5, color: "#475569", lineHeight: 1.7 }}>
          <div>{hospital.address}</div>
          <div>{hospital.phone} · 기관코드 {hospital.orgCode}</div>
        </div>

        {/* HD-04 접근성 */}
        <div className="flex" style={{ marginTop: 12, gap: 8 }}>
          <div style={{ flex: 1, background: "#f8fafc", borderRadius: 10, padding: "10px 12px", textAlign: "center" }}>
            <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a" }}>
              {shownDistanceKm == null ? "미산출" : `${shownDistanceKm.toFixed(1)}km`}
            </div>
            <div style={{ fontSize: 10, color: "#64748b" }}>
              {hasRoadRoute ? "카카오 도로거리" : (fallbackDistanceKm == null ? "거리" : "직선거리 (대체)")}
            </div>
          </div>
          <div style={{ flex: 1, background: "#f8fafc", borderRadius: 10, padding: "10px 12px", textAlign: "center" }}>
            <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a" }}>
              {hasRoadRoute ? `${Math.round(hospital.etaMin)}분` : "미산출"}
            </div>
            <div style={{ fontSize: 10, color: "#64748b" }}>예상 소요시간</div>
          </div>
        </div>
        <div style={{ fontSize: 9.5, color: "#94a3b8", marginTop: 4 }} title={hospital.routeStatus ?? undefined}>
          {hasRoadRoute
            ? `* 지역 대표점 기준 · 카카오모빌리티 도로 경로${routeUpdatedLabel ? ` · ${routeUpdatedLabel} 수집` : ""}`
            : fallbackDistanceKm != null
              ? "* 지역 대표점 기준 · 카카오 경로 미산출로 직선거리 대체 · 예상시간 미산출"
              : "* 지역 대표점 기준 · 카카오 경로와 거리 모두 미산출"}
        </div>

        {/* 실시간 응급실 병상 (bed_status.csv 실데이터) */}
        {hospital.status !== "결측" && (
          <div style={{ marginTop: 16, background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 10, padding: "10px 12px" }}>
            <div className="flex items-center justify-between">
              <span style={{ fontSize: 11.5, fontWeight: 600 }}>실시간 응급실 병상</span>
              <span style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>{hospital.availableBeds} / {hospital.totalBeds}석</span>
            </div>
            <div style={{ fontSize: 10, ...mutedText, marginTop: 2 }}>
              포화율 {hospital.saturation?.toFixed(0)}% {updatedLabel && `· ${updatedLabel} 기준`}
            </div>
          </div>
        )}

        {/* HD-02 병상 현황 (카테고리별 — 자리채움) */}
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 2 }}>항목별 병상 현황</div>
          <div style={{ fontSize: 10, ...mutedText, marginBottom: 8 }}>* 카테고리별 세부 병상은 아직 실데이터 연동 전 추정치입니다</div>
          {bedEntries.map((b) => (
            <div key={b.label} style={{ marginBottom: 8 }}>
              <div className="flex justify-between" style={{ fontSize: 11, marginBottom: 3 }}>
                <span>{b.label}</span>
                <span style={{ color: "#0f172a" }}>{b.total == null ? "미확인" : `${b.avail} / ${b.total}병상`}</span>
              </div>
              <div style={{ background: "#e2e8f0", height: 6, borderRadius: 4, overflow: "hidden" }}>
                {b.total != null && <div style={{ width: `${(b.avail / b.total) * 100}%`, height: "100%", background: bedStatusColor[hospital.status] }} />}
              </div>
            </div>
          ))}
        </div>

        {/* HD-03 수용가능여부 */}
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>중증질환별 수용 가능 여부</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {CAPA_DISEASES.map((d) => (
              <div key={d} className="flex items-center justify-between" style={{ background: "#f8fafc", borderRadius: 8, padding: "6px 10px" }}>
                <span style={{ fontSize: 11 }}>{d}</span>
                <span style={{ fontSize: 10.5, fontWeight: 700, color: CAPA_COLOR[hospital.capability[d]] }}>{hospital.capability[d]}</span>
              </div>
            ))}
          </div>
        </div>

        {/* HD-06 소속 지역 위험도 */}
        {region && !region.missing && (
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid #e2e8f0" }}>
            <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 700 }}>소속 지역 위험도 — {hospital.region}</span>
              <span style={{ fontSize: 13, fontWeight: 800, color: riskColor(region.risk) }}>{region.risk.toFixed(1)}점</span>
            </div>
            {COMPONENTS.map((d) => (
              <div key={d.name} style={{ marginBottom: 6 }}>
                <div className="flex justify-between" style={{ fontSize: 10.5, color: "#64748b", marginBottom: 2 }}>
                  <span>{d.name} ({d.weight})</span><span>{region[d.key].toFixed(0)}점</span>
                </div>
                <div style={{ background: "#e2e8f0", height: 5, borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ width: `${Math.min(region[d.key], 100)}%`, height: "100%", background: "#94a3b8" }} />
                </div>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}
