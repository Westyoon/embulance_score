"use client";
import { useState } from "react";
import { Map as MapIcon, Activity, AlertTriangle, ChevronRight, Search, X } from "lucide-react";
import { riskColor, riskTextColor, riskLabel, bedStatusColor } from "@/lib/riskScale";
import { enrichHospital } from "@/lib/mockDetail";
import { cardStyle, mutedText, TabGroup, KpiCard, RiskLegendStrip } from "./shared";
import KoreaMap from "./KoreaMap";
import RegionPopup from "./RegionPopup";
import HospitalPopup from "./HospitalPopup";

const searchInputStyle = {
  width: "100%", fontSize: 12.5, padding: "8px 10px", borderRadius: 8,
  border: "1px solid #e2e8f0", outline: "none", color: "#0f172a", background: "#f8fafc",
};

function SearchBox({ value, onChange, placeholder }) {
  return (
    <div className="flex items-center" style={{ position: "relative", marginBottom: 8 }}>
      <Search size={13} color="#94a3b8" style={{ position: "absolute", left: 9 }} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        style={{ ...searchInputStyle, paddingLeft: 28 }} />
      {value && (
        <button aria-label={`${placeholder} 검색어 지우기`} onClick={() => onChange("")} style={{ position: "absolute", right: 8, background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}>
          <X size={13} />
        </button>
      )}
    </div>
  );
}

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
  const [selectedRegionRef, setSelectedRegionRef] = useState(null);
  const [selectedHospitalCode, setSelectedHospitalCode] = useState(null);
  const [sidePanel, setSidePanel] = useState("summary");
  const [regionQuery, setRegionQuery] = useState("");
  const [hospitalQuery, setHospitalQuery] = useState("");

  const codesForRegion = (region) => (
    region?.geoCodes?.length ? region.geoCodes : (region?.code ? [region.code] : [])
  );
  const selected = selectedRegionRef
    ? (
      (selectedRegionRef.code ? regionIndex[selectedRegionRef.code] : null)
      ?? (selectedRegionRef.key ? regionsByKey[selectedRegionRef.key] : null)
    )
    : null;
  const selectedHospitalBase = selectedHospitalCode
    ? allHospitals.find((hospital) => hospital.orgCode === selectedHospitalCode) ?? null
    : null;
  const selectedHospital = selectedHospitalBase ? enrichHospital(selectedHospitalBase) : null;
  const hospitalRegion = selectedHospitalBase
    ? findRegionForHospital(regionsByKey, selectedHospitalBase)
    : null;
  const highlightCodes = selected
    ? codesForRegion(selected)
    : (selectedHospitalBase?.geoCode
      ? [selectedHospitalBase.geoCode]
      : codesForRegion(hospitalRegion));

  const openRegion = (r) => {
    setSelectedRegionRef({ key: r.key ?? null, code: r.code ?? null });
    setSelectedHospitalCode(null);
  };
  const openHospital = (h) => {
    setSelectedHospitalCode(h.orgCode);
    setSelectedRegionRef(null);
  };
  const backToRegion = () => {
    const region = findRegionForHospital(regionsByKey, selectedHospitalBase);
    setSelectedHospitalCode(null);
    if (region) openRegion(region);
  };

  // 탭을 클릭하면 열려있던 상세 패널을 닫고 해당 리스트로 전환한다.
  const changeSidePanel = (key) => {
    setSidePanel(key);
    setSelectedRegionRef(null);
    setSelectedHospitalCode(null);
  };

  const filteredRanked = regionQuery.trim() ? ranked.filter((r) => r.name.includes(regionQuery.trim())) : ranked;
  const hq = hospitalQuery.trim();
  const filteredHospitals = hq ? allHospitals.filter((h) => h.name.includes(hq) || h.region.includes(hq)) : allHospitals;
  const averageRisk = Number.isFinite(kpi.avg) ? kpi.avg.toFixed(1) : "-";

  return (
    <div className="grid grid-cols-1 items-start lg:grid-cols-[minmax(0,1fr)_320px]" style={{ gap: 16 }}>
      <div className="lg:sticky lg:top-4" style={{ ...cardStyle, padding: 16, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
          <div className="flex items-center gap-2">
            <MapIcon size={16} color="#38bdf8" />
            <span style={{ fontWeight: 700, fontSize: 14 }}>전국 응급의료 위험도 지도</span>
          </div>
          <span style={{ fontSize: 10.5, ...mutedText }}>Ctrl/⌘ + 휠로 확대/축소 · 드래그로 이동 · 지역 클릭 시 상세</span>
        </div>
        <KoreaMap geo={geo} regionIndex={regionIndex} onSelect={openRegion} highlightCodes={highlightCodes}
          selectedHospital={selectedHospital} hospitalRegionCode={selectedHospital?.geoCode ?? hospitalRegion?.code ?? null} />
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <TabGroup
          options={[{ key: "summary", label: "핵심 지표" }, { key: "hospitals", label: "병원 리스트" }]}
          active={sidePanel} onChange={changeSidePanel} />

        {selected ? (
          <RegionPopup region={selected} onClose={() => setSelectedRegionRef(null)} onSelectHospital={openHospital} />
        ) : selectedHospital ? (
          <HospitalPopup hospital={selectedHospital} region={hospitalRegion}
            onClose={() => setSelectedHospitalCode(null)} onBack={hospitalRegion ? backToRegion : undefined} />
        ) : sidePanel === "summary" ? (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <KpiCard label="평균 위험도" value={averageRisk} accent="#38bdf8" icon={Activity} />
              <KpiCard label="고위험 지역" value={kpi.high} sub="50점 초과" accent="#ef4444" icon={AlertTriangle} />
            </div>
            <div style={{ ...cardStyle, padding: "10px 12px" }}>
              <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 12.5 }}>지역별 위험도</span>
                <span style={{ fontSize: 10, ...mutedText }}>{filteredRanked.length}개 지역 · 클릭 시 지도 연동</span>
              </div>
              <SearchBox value={regionQuery} onChange={setRegionQuery} placeholder="지역명 검색 (예: 고령군)" />
              <div style={{ display: "grid", gridTemplateColumns: "1fr 62px 42px", gap: 4, fontSize: 9.5, ...mutedText, padding: "0 6px 6px", borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0, background: "#ffffff" }}>
                <span>지역</span><span style={{ textAlign: "center" }}>등급</span><span style={{ textAlign: "right" }}>점수</span>
              </div>
              {filteredRanked.length === 0 && <div style={{ fontSize: 11.5, ...mutedText, padding: "14px 4px", textAlign: "center" }}>검색 결과가 없습니다</div>}
              {filteredRanked.map((r) => {
                const active = codesForRegion(r).some((code) => highlightCodes.includes(code));
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
          <div style={{ ...cardStyle, padding: 14 }}>
            <div className="flex items-center justify-between" style={{ marginBottom: 10 }}>
              <span style={{ fontWeight: 700, fontSize: 12.5 }}>전체 의료기관</span>
              <span style={{ fontSize: 10, ...mutedText }}>{filteredHospitals.length}곳</span>
            </div>
            <SearchBox value={hospitalQuery} onChange={setHospitalQuery} placeholder="병원명 또는 지역명 검색 (예: 성모병원, 포항)" />
            {filteredHospitals.length === 0 && <div style={{ fontSize: 11.5, ...mutedText, padding: "14px 4px", textAlign: "center" }}>검색 결과가 없습니다</div>}
            {filteredHospitals.map((h, i) => (
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
