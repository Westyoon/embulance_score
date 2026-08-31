import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function authorized(request) {
  const expected = process.env.PIPELINE_ADMIN_TOKEN || "";
  const authorization = request.headers.get("authorization") || "";
  const provided = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!expected || !provided) return false;
  const expectedBuffer = Buffer.from(expected);
  const providedBuffer = Buffer.from(provided);
  return expectedBuffer.length === providedBuffer.length
    && crypto.timingSafeEqual(expectedBuffer, providedBuffer);
}

export async function POST(request) {
  if (process.env.PIPELINE_MUTATIONS_ENABLED !== "true") {
    return Response.json({ error: "파이프라인 갱신이 비활성화되어 있습니다." }, { status: 503 });
  }
  if (!authorized(request)) {
    return Response.json({ error: "인증이 필요합니다." }, { status: 401 });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "JSON 요청 본문이 필요합니다." }, { status: 400 });
  }
  const mode = body?.mode === "full" ? "full" : (body?.mode === "beds" ? "beds" : null);
  if (!mode) {
    return Response.json({ error: "mode는 beds 또는 full이어야 합니다." }, { status: 400 });
  }

  const runtimeRoot = process.env.PIPELINE_RUNTIME_DIR
    || process.env.RAILWAY_VOLUME_MOUNT_PATH
    || path.join(process.cwd(), "runtime");
  const stateDir = process.env.PIPELINE_STATE_DIR
    ? process.env.PIPELINE_STATE_DIR
    : path.join(runtimeRoot, "state");
  const requestFile = path.join(stateDir, "refresh_request.json");
  const temporary = `${requestFile}.${process.pid}.tmp`;
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(
    temporary,
    `${JSON.stringify({ mode, requestedAt: new Date().toISOString() }, null, 2)}\n`,
    "utf8",
  );
  fs.renameSync(temporary, requestFile);
  return Response.json({ accepted: true, mode }, { status: 202 });
}
