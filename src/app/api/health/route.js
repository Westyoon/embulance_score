import { getDashboardSnapshot, readPipelineStatus } from "@/lib/dashboardSnapshot";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const snapshot = getDashboardSnapshot();
    const dataAsOf = snapshot.data.kpi.asOf;
    return Response.json(
      {
        status: snapshot.degraded || snapshot.dataStale ? "degraded" : "ok",
        dataVersion: snapshot.version,
        dataAsOf,
        dataAgeMinutes: snapshot.dataAgeMinutes,
        dataStaleAfterMinutes: snapshot.dataStaleAfterMinutes,
        dataStale: snapshot.dataStale,
        bedRiskExpiredRegions: snapshot.bedRiskExpiredRegions,
        bedRiskExpiredHospitals: snapshot.bedRiskExpiredHospitals,
        nextBedRiskExpiryAt: snapshot.nextBedRiskExpiryAt,
        regions: snapshot.data.kpi.total,
        completeRegions: snapshot.data.kpi.complete,
        pipeline: readPipelineStatus(),
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    console.error(`Health check failed: ${error?.name ?? "Error"}`);
    return Response.json(
      { status: "unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
