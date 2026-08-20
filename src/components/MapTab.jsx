"use client";
import { useState } from "react";
import { Map as MapIcon, Activity, AlertTriangle, ChevronRight } from "lucide-react";
import { riskColor, riskTextColor, riskLabel, bedStatusColor } from "@/lib/riskScale";
import { enrichHospital } from "@/lib/mockDetail";
import { cardStyle, mutedText, TabGroup, KpiCard, RiskLegendStrip } from "./shared";
import KoreaMap from "./KoreaMap";
import RegionPopup from "./RegionPopup";
import HospitalPopup from "./HospitalPopup";

// 사이드바 "병원 리스트"에서 들어온 병원은 시도+시군구명으로만 지역을 알기
// 때문에, 지도용 regionsByKey를 직접/부모도시 순으로 역참조한다. 지역 팝업
// 쪽 진입(hospitals 배열)은 이미 정확한 region 객체를 들고 있어 이 조회가
// 필요 없다.
function findRegionForHospital(regionsByKey, h) {
  const direct = regionsByKey[`${h.sido}|${h.region}`];
  if (direct) return direct;
  const m = h.region.match(/^(.+?시)(.+구)$/);
  if (m) return regionsByKey[`${h.sido}|${m[1]}`];
  return null;
}

export default function MapTab({ data }) {
  const { geo, regionIndex, regionsByKey, ranked, kpi, allHospitals } = data;
  const [selected, setSelected] = useState(null);
  const [selectedHospital, setSelectedHospital] = useState(null);
  const [highlightCode, setHighlightCode] = useState(null);
  const [sidePanel, setSidePanel] = useState("summary");

  const openRegion = (r) => { setHighlightCode(r.code ?? null); setSelected(r); setSelectedHospital(null); };
  const openHospital = (h) => {
    const region = findRegionForHospital(regionsByKey, h);
    setSelectedHospital(enrichHospital(h));
    setSelected(null);
    if (region) setHighlightCode(region.code ?? null);
  };
  const backToRegion = () => {
    const region = findRegionForHospital(regionsByKey, selectedHospital);
    setSelectedHospital(null);
    if (region) openRegion(region);
  };
  const hospitalRegion = selectedHospital ? findRegionForHospital(regionsByKey, selectedHospital) : null;

  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 320px", gap: 16, height: "100%" }}>
      <div style={{ ...cardStyle, padding: 16, position: "relative", display: "flex", flexDirection: "column" }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
          <div className="flex items-center gap-2">
            <MapIcon size={16} color="#38bdf8" />
            <span style={{ fontWeight: 700, fontSize: 14 }}>전국 응급의료 위험도 지도</span>
          </div>
          <span style={{ fontSize: 10.5, ...mutedText }}>휠로 확대/축소 · 드래그로 이동 · 지역 클릭 시 상세</span>
        </div>
        <KoreaMap geo={geo} regionIndex={regionIndex} onSelect={openRegion} highlightCode={highlightCode} />
        {selected && <RegionPopup region={selected} onClose={() => setSelected(null)} onSelectHospital={openHospital} />}
        {selectedHospital && (
          <HospitalPopup hospital={selectedHospital} region={hospitalRegion}
            onClose={() => setSelectedHospital(null)} onBack={hospitalRegion ? backToRegion : undefined} />
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <TabGroup
          options={[{ key: "summary", label: "핵심 지표" }, { key: "hospitals", label: "병원 리스트" }]}
          active={sidePanel} onChange={setSidePanel} />

        {sidePanel === "summary" ? (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <KpiCard label="평균 위험도" value={kpi.avg.toFixed(1)} accent="#38bdf8" icon={Activity} />
              <KpiCard label="고위험 지역" value={kpi.high} sub="50점 이상" accent="#ef4444" icon={AlertTriangle} />
            </div>
            <div style={{ ...cardStyle, padding: "10px 12px", flex: 1, overflowY: "auto", maxHeight: 380 }}>
              <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 12.5 }}>지역별 위험도</span>
                <span style={{ fontSize: 10, ...mutedText }}>{ranked.length}개 지역 · 클릭 시 지도 연동</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 62px 42px", gap: 4, fontSize: 9.5, ...mutedText, padding: "0 6px 6px", borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0, background: "#ffffff" }}>
                <span>지역</span><span style={{ textAlign: "center" }}>등급</span><span style={{ textAlign: "right" }}>점수</span>
              </div>
              {ranked.map((r) => {
                const active = r.code != null && highlightCode === r.code;
                return (
                  <div key={r.key} onClick={() => openRegion(r)}
                    style={{ display: "grid", gridTemplateColumns: "1fr 62px 42px", gap: 4, alignItems: "center",
                      padding: "6px 6px", marginTop: 2, borderRadius: 6, cursor: "pointer",
                      background: active ? "#e2e8f0" : "transparent",
                      borderLeft: `3px solid ${riskColor(r.risk)}` }}>
                    <span style={{ fontSize: 12, fontWeight: active ? 700 : 400 }}>{r.name}</span>
                    <span style={{ fontSize: 9.5, fontWeight: 700, color: riskTextColor(r.risk), background: riskColor(r.risk) + "22", borderRadius: 999, padding: "2px 0", textAlign: "center" }}>
                      {riskLabel(r.risk)}
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: riskTextColor(r.risk), textAlign: "right" }}>{r.risk.toFixed(1)}</span>
                  </div>
                );
              })}
            </div>
            <div style={{ ...cardStyle, padding: 12 }}>
              <RiskLegendStrip compact />
            </div>
          </>
        ) : (
          <div style={{ ...cardStyle, padding: 14, flex: 1, overflowY: "auto", maxHeight: 500 }}>
            <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 10 }}>전체 의료기관 ({allHospitals.length})</div>
            {allHospitals.map((h, i) => (
              <div key={h.name + i} className="flex items-center justify-between gap-2" onClick={() => openHospital(h)}
                style={{ padding: "8px 0", borderTop: i ? "1px solid #e2e8f0" : "none", cursor: "pointer" }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12.5 }}>{h.name}</div>
                  <div style={{ fontSize: 10.5, ...mutedText }}>{h.sido} {h.region} · {h.grade}</div>
                </div>
                <span className="flex items-center gap-1.5" style={{ fontSize: 10.5, flexShrink: 0, whiteSpace: "nowrap" }}>
                  <span style={{ width: 7, height: 7, borderRadius: 99, background: bedStatusColor[h.status] }} />
                  <span style={{ color: bedStatusColor[h.status] }}>{h.status}</span>
                  <ChevronRight size={12} color="#94a3b8" />
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
