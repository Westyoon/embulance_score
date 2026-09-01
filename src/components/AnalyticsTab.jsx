"use client";
import { useState } from "react";
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ScatterChart, Scatter, ZAxis, Cell,
} from "recharts";
import { Activity, AlertTriangle, Users, LayoutDashboard, Search, X } from "lucide-react";
import { riskColor } from "@/lib/riskScale";
import { facilityCounts } from "@/lib/mockDetail";
import { cardStyle, mutedText, KpiCard } from "./shared";
import CorrelationPanel from "./CorrelationPanel";
import TreemapHeatmapPanel from "./TreemapHeatmapPanel";
import ClusterTypePanel from "./ClusterTypePanel";
import ContributionPanel from "./ContributionPanel";

function BubbleTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: 11.5, padding: "8px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.08)" }}>
      <b>{r.name}</b><br />
      응급실 {r.hospitalCount}개 · 의료진 {r.doctorCount}명<br />
      인구대비병상 부담 {r.popBed.toFixed(0)}점 · <span style={{ color: riskColor(r.risk) }}>위험도 {r.risk.toFixed(1)}점</span>
      {r.sourcePolicyValidAtCalculation === false ? (
        <><br /><span style={{ color: "#b91c1c" }}>최근 계산 점수 · 계산 당시 원천시각 기준 미충족</span></>
      ) : r.scoreExpired ? (
        <><br /><span style={{ color: "#b45309" }}>최근 계산 점수 · 현재 원천시각 만료</span></>
      ) : null}
    </div>
  );
}

const axisTick = { fill: "#64748b", fontSize: 10 };

function formatAsOf(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function average(rows, key) {
  return rows.reduce((s, r) => s + r[key], 0) / (rows.length || 1);
}

function AnalysisUnavailableCard({ title, message }) {
  return (
    <div style={{ ...cardStyle, padding: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>{title}</div>
      <div role="status" style={{ fontSize: 11.5, lineHeight: 1.6, color: "#92400e", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 10, padding: "12px 14px" }}>
        {message}
      </div>
    </div>
  );
}

export default function AnalyticsTab({ data }) {
  const analysisSnapshot = data.analysisSnapshot ?? null;
  const analysisData = analysisSnapshot ?? data;
  const {
    ranked: rankedRows = [],
    kpi: analysisKpi = {},
    clusterProfile = [],
    clusterIds = [],
    clusterMetaById = {},
    correlation = [],
    regression = { coef: [], r2: null, mae: null, rows: 0 },
  } = analysisData;
  const ranked = rankedRows.filter((region) => Number.isFinite(region.risk));
  const analyticsStale = data.analyticsStale === true;
  const currentKpi = data.kpi ?? {};
  const sourceComplete = analysisSnapshot?.sourceComplete
    ?? analysisKpi.complete
    ?? ranked.length;
  const currentComplete = analysisSnapshot?.currentComplete
    ?? currentKpi.complete
    ?? ranked.length;
  const expiredCount = analysisSnapshot?.expiredRegions
    ?? data.bedRiskExpiredRegions
    ?? 0;
  const isHistoricalSnapshot = analysisSnapshot != null && expiredCount > 0;
  const totalRegions = analysisKpi.total
    ?? currentKpi.total
    ?? sourceComplete;
  const sourceMissingCount = analysisSnapshot?.sourceMissing
    ?? Math.max(0, totalRegions - currentComplete - expiredCount);
  const sourcePolicyValidCount = analysisSnapshot?.sourcePolicyValid
    ?? sourceComplete;
  const sourcePolicyInvalidCount = analysisSnapshot?.sourcePolicyInvalid
    ?? Math.max(0, sourceComplete - sourcePolicyValidCount);
  const expiredRegionRows = ranked.filter((region) => region.scoreExpired);
  const sourcePolicyInvalidRows = ranked.filter((region) => (
    region.sourcePolicyValidAtCalculation === false
  ));
  const sourceMissingRows = analysisSnapshot?.missingRegions ?? [];
  const unavailableCount = expiredCount + sourceMissingCount;
  const averageRisk = Number.isFinite(analysisKpi.avg)
    ? analysisKpi.avg
    : (ranked.length > 0 ? average(ranked, "risk") : null);
  const highRiskCount = Number.isFinite(analysisKpi.high)
    ? analysisKpi.high
    : ranked.filter((region) => region.risk > 50).length;
  const analysisAsOf = analysisSnapshot?.asOf ?? analysisKpi.asOf ?? currentKpi.asOf;
  const bubbleData = ranked.map((r) => ({ ...r, ...facilityCounts(r.key, r.name, r.doc) }));
  const [bubbleQuery, setBubbleQuery] = useState("");
  const bq = bubbleQuery.trim();
  const hasBubbleMatch = bq ? bubbleData.some((r) => r.name.includes(bq)) : true;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {(isHistoricalSnapshot || unavailableCount > 0) && (
        <div role="alert" style={{ color: "#92400e", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 12, padding: "12px 14px", fontSize: 11.5, lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 2 }}>
            {isHistoricalSnapshot ? "최근 계산된 위험도 점수를 참고용으로 표시 중입니다." : "유효한 위험도 점수는 계속 표시합니다."}
          </div>
          {isHistoricalSnapshot ? (
            <>
              아래 평균·순위·차트는 <b>{formatAsOf(analysisAsOf)} 기준 {sourceComplete}개 지역</b>의 최근 계산값입니다.
              현재 유효한 지역은 <b>{currentComplete}개</b>이며, 원천시각이 만료된 <b>{expiredCount}개</b>의 점수는 실시간 값이 아닙니다.
              원천 결측 <b>{sourceMissingCount}개</b>는 0점으로 대체하지 않고 산출본에서도 제외했습니다.
              {sourcePolicyInvalidCount > 0 && (
                <> 이 중 <b>{sourcePolicyInvalidCount}개</b>는 계산 당시에도 12시간 원천시각 기준을 충족하지 못해 참고용으로만 봐야 합니다.</>
              )}
            </>
          ) : (
            <>
              평균·순위·트리맵·버블차트는 현재 점수가 있는 <b>{currentComplete}개 지역</b>만 포함합니다.
              병상 원천시각 만료 <b>{expiredCount}개</b>와 원천 결측 <b>{sourceMissingCount}개</b>는 0점으로 대체하지 않고 집계와 차트에서 제외했습니다.
              {analyticsStale && <> 전체 표본 분석은 다음 갱신 전까지 패널별로 숨깁니다.</>}
            </>
          )}
          {(expiredRegionRows.length > 0 || sourceMissingRows.length > 0 || sourcePolicyInvalidRows.length > 0) && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: "pointer", fontWeight: 600 }}>만료·결측·원천기준 주의 지역 보기</summary>
              <div style={{ marginTop: 6, color: "#78350f" }}>
                {expiredRegionRows.length > 0 && (
                  <div><b>만료:</b> {expiredRegionRows.map((region) => `${region.sido ?? ""} ${region.name}`.trim()).join(" · ")}</div>
                )}
                {sourceMissingRows.length > 0 && (
                  <div>
                    <b>원천 결측:</b>{" "}
                    {sourceMissingRows.map((region) => {
                      const label = `${region.sido ?? ""} ${region.name}`.trim();
                      const components = region.missingComponents?.length
                        ? ` (${region.missingComponents.join("·")} 결측)`
                        : "";
                      return `${label}${components}`;
                    }).join(" · ")}
                  </div>
                )}
                {sourcePolicyInvalidRows.length > 0 && (
                  <div>
                    <b>계산 당시 원천시각 기준 미충족:</b>{" "}
                    {sourcePolicyInvalidRows.map((region) => `${region.sido ?? ""} ${region.name}`.trim()).join(" · ")}
                  </div>
                )}
              </div>
            </details>
          )}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <KpiCard label="평균 위험도" value={averageRisk == null ? "-" : averageRisk.toFixed(1)} sub={isHistoricalSnapshot ? `최근 계산 ${sourceComplete}개 지역` : `현재 유효 ${currentComplete}개 지역`} accent="#38bdf8" icon={Activity} />
        <KpiCard label="고위험 지역" value={highRiskCount} sub={isHistoricalSnapshot ? "최근 계산값 · 50점 초과" : "유효 점수 중 50점 초과"} accent="#ef4444" icon={AlertTriangle} />
        <KpiCard label="현재 유효 지역" value={`${currentComplete} / ${totalRegions}`} sub={`${expiredCount}개 만료 · ${sourceMissingCount}개 원천 결측`} accent="#22c55e" icon={Users} />
        <KpiCard label="점수 기준 시각" value={formatAsOf(analysisAsOf)} sub={isHistoricalSnapshot ? "KST · 최근 계산값" : "KST · 병상 API 최신 수집"} accent="#a78bfa" icon={LayoutDashboard} />
      </div>

      <TreemapHeatmapPanel data={bubbleData} excludedCount={sourceMissingCount} expiredCount={expiredCount} policyInvalidCount={sourcePolicyInvalidCount} historical={isHistoricalSnapshot} />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {correlation.length === 0 ? (
          <AnalysisUnavailableCard
            title="위험도에 영향을 주는 요인 순위"
            message={analyticsStale && !isHistoricalSnapshot
              ? "일부 지역이 만료되어 이전 전체 표본의 상관분석은 표시하지 않습니다. 유효 위험도 점수와 지역별 차트는 계속 확인할 수 있습니다."
              : "상관분석 산출 데이터가 아직 없습니다."}
          />
        ) : (
          <CorrelationPanel correlation={correlation} />
        )}

        <div style={{ ...cardStyle, padding: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>지역별 의료 취약도 비교 (버블차트)</div>
          <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 8 }}>x=응급실 수 · y=의료진 수 · 크기=인구대비병상 부담 · 색상=위험도 · {isHistoricalSnapshot ? `최근 계산 ${bubbleData.length}개 지역` : `현재 유효 ${bubbleData.length}개 지역만 표시`}</div>
          <div className="flex items-center" style={{ position: "relative", marginBottom: 10 }}>
            <Search size={13} color="#94a3b8" style={{ position: "absolute", left: 9 }} />
            <input value={bubbleQuery} onChange={(e) => setBubbleQuery(e.target.value)} placeholder="지역명 검색 (예: 안동시)"
              style={{ width: "100%", fontSize: 12.5, padding: "8px 10px 8px 28px", borderRadius: 8, border: "1px solid #e2e8f0", outline: "none", color: "#0f172a", background: "#f8fafc" }} />
            {bubbleQuery && (
              <button onClick={() => setBubbleQuery("")} style={{ position: "absolute", right: 8, background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}>
                <X size={13} />
              </button>
            )}
          </div>
          {bq && (
            <div style={{ fontSize: 10.5, ...mutedText, marginBottom: 6 }}>
              {hasBubbleMatch ? `"${bq}" 포함 지역을 진하게 강조 표시했어요` : "일치하는 지역이 없습니다"}
            </div>
          )}
          <ResponsiveContainer width="100%" height={230}>
            <ScatterChart margin={{ left: 0, right: 20, top: 10, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" />
              <XAxis type="number" dataKey="hospitalCount" name="응급실 수" tick={axisTick} />
              <YAxis type="number" dataKey="doctorCount" name="의료진 수" tick={axisTick} />
              <ZAxis type="number" dataKey="popBed" range={[40, 260]} name="인구대비병상 부담" />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<BubbleTooltip />} />
              <Scatter data={bubbleData}>
                {bubbleData.map((r, i) => {
                  const isMatch = bq && r.name.includes(bq);
                  const dim = bq && !isMatch;
                  return (
                    <Cell key={i} fill={riskColor(r.risk)} fillOpacity={dim ? 0.12 : isMatch ? 1 : 0.7}
                      stroke={isMatch ? "#0f172a" : "none"} strokeWidth={isMatch ? 2 : 0} />
                  );
                })}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
          <div style={{ fontSize: 10, ...mutedText, marginTop: 4 }}>* 응급실 수·의료진 수는 hospital_master.csv 지역 집계 연동 전 추정치입니다.</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {ranked.length > 0 ? (
          <ContributionPanel
            avgBed={average(ranked, "bed")}
            avgAccess={average(ranked, "access")}
            avgPopBed={average(ranked, "popBed")}
            avgDoc={average(ranked, "doc")}
            avgRisk={averageRisk}
            regression={regression}
            sampleCount={ranked.length}
            historical={isHistoricalSnapshot}
          />
        ) : (
          <AnalysisUnavailableCard
            title="위험도를 구성하는 요인별 영향력"
            message="현재 유효한 위험도 점수가 없어 평균 기여 점수를 계산하지 않았습니다. 만료·결측 지역을 0점으로 대체하지 않습니다."
          />
        )}

        {clusterIds.length === 0 || clusterProfile.length === 0 ? (
          <AnalysisUnavailableCard
            title="지역 유형 비교"
            message={analyticsStale && !isHistoricalSnapshot
              ? "일부 지역이 만료되어 이전 전체 표본의 군집 유형은 표시하지 않습니다. 위 위험도 차트에는 현재 유효한 지역만 포함됩니다."
              : "군집분석 산출 데이터가 아직 없습니다."}
          />
        ) : (
          <ClusterTypePanel ranked={ranked} clusterProfile={clusterProfile} clusterIds={clusterIds} clusterMetaById={clusterMetaById} />
        )}
      </div>
    </div>
  );
}
