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

export default function RegionPopup({ region, onClose, onSelectHospital }) {
  if (!region) return null;

  if (region.missing) {
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
            ? <>구성점수 중 일부(주로 의료진부족점수)가 HIRA 매칭 기준을 충족하지 못해 최종 위험도가 <b style={{ color: "#0f172a" }}>산출되지 않은 지역</b>입니다. "의료진 부족 0점"이 아니라 <b style={{ color: "#0f172a" }}>"데이터 부족으로 미산출"</b>로 표시해야 합니다.</>
            : <>이 경계 데이터셋(2013 KOSTAT 단순화)에 대응하는 <b style={{ color: "#0f172a" }}>원천 데이터가 없는 지역</b>입니다. 최근 행정구역 개편이나 데이터 매칭 누락으로 아직 위험도가 연결되지 않았습니다.</>}
        </div>
      </div>
    );
  }

  const data = COMPONENTS.map((c) => ({ ...c, value: region[c.key] }));
  const sorted = [...data].sort((a, b) => b.value - a.value);
  const [top1, top2] = sorted;
  const lvl = riskLabel(region.risk);
  const hospitals = region.hospitals?.length ? region.hospitals : null;

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
