import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const runtimeRoot = path.resolve(
  process.env.PIPELINE_RUNTIME_DIR
    || process.env.RAILWAY_VOLUME_MOUNT_PATH
    || path.join(root, "runtime"),
);
const liveData = path.join(runtimeRoot, "data");
const stateDir = path.join(runtimeRoot, "state");
const boundaryFile = path.join(runtimeRoot, "koreaGeo.json");
const statusFile = path.join(stateDir, "pipeline_status.json");
const requestFile = path.join(stateDir, "refresh_request.json");
const lockFile = path.join(stateDir, ".pipeline.lock");
const python = process.env.PIPELINE_PYTHON || (
  process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python")
);
const port = process.env.PORT || "3000";
const schedulerEnabled = process.env.ENABLE_PIPELINE_SCHEDULER === "true";
const fastIntervalMinutes = positiveNumber("FAST_REFRESH_INTERVAL_MINUTES", 60);
const fullIntervalHours = positiveNumber("FULL_REFRESH_INTERVAL_HOURS", 24);
const failureRetryMinutes = positiveNumber("PIPELINE_FAILURE_RETRY_MINUTES", 15);
const schedulerTickMilliseconds = 60_000;

let currentJob = null;
let pendingJob = null;
let webProcess = null;
let previousStatus = readJson(statusFile) || {};
let shuttingDown = false;

function positiveNumber(name, fallback) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return value;
}

function readJson(filename) {
  try {
    return JSON.parse(fs.readFileSync(filename, "utf8"));
  } catch {
    return null;
  }
}

function writeJsonAtomic(filename, value) {
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  const temporary = `${filename}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, filename);
}

function moveAside(target, label) {
  if (!fs.existsSync(target)) return null;
  const safeName = path.basename(target).replace(/[^a-zA-Z0-9_.-]/g, "-");
  let destination = path.join(stateDir, `${label}-${Date.now()}-${safeName}`);
  let suffix = 0;
  while (fs.existsSync(destination)) {
    suffix += 1;
    destination = path.join(stateDir, `${label}-${Date.now()}-${suffix}-${safeName}`);
  }
  fs.renameSync(target, destination);
  return destination;
}

function restoreInterruptedPromotion() {
  const candidates = fs.readdirSync(stateDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && (
      entry.name.startsWith(".pipeline-backup-")
      || entry.name.startsWith(".bed-refresh-backup-")
    ))
    .map((entry) => {
      const source = path.join(stateDir, entry.name);
      return { name: entry.name, source, mtimeMs: fs.statSync(source).mtimeMs };
    })
    .sort((left, right) => right.mtimeMs - left.mtimeMs);

  if (candidates.length === 0) {
    for (const entry of fs.readdirSync(stateDir, { withFileTypes: true })) {
      if (entry.isFile() && /^\.pipeline-backup-.+\.koreaGeo\.json$/.test(entry.name)) {
        moveAside(path.join(stateDir, entry.name), "orphaned-boundary-backup");
      }
    }
    return;
  }

  const selected = candidates[0];
  const isFull = selected.name.startsWith(".pipeline-backup-");
  const boundaryBackup = isFull
    ? path.join(stateDir, `${selected.name}.koreaGeo.json`)
    : null;
  if (isFull && !fs.existsSync(boundaryBackup)) {
    throw new Error(`Cannot recover full pipeline backup without boundary: ${selected.name}`);
  }

  moveAside(liveData, "interrupted-live-data");
  fs.cpSync(selected.source, liveData, { recursive: true, force: true });
  if (boundaryBackup) {
    moveAside(boundaryFile, "interrupted-live-boundary");
    fs.copyFileSync(boundaryBackup, boundaryFile);
  }

  moveAside(selected.source, "recovered-data-backup");
  if (boundaryBackup) moveAside(boundaryBackup, "recovered-boundary-backup");
  for (const stale of candidates.slice(1)) {
    moveAside(stale.source, "superseded-data-backup");
    const staleBoundary = path.join(stateDir, `${stale.name}.koreaGeo.json`);
    moveAside(staleBoundary, "superseded-boundary-backup");
  }
  updateStatus({
    state: "idle",
    recoveredAt: new Date().toISOString(),
    recoveredFrom: selected.name,
    error: null,
  });
  console.warn(`[pipeline] restored interrupted promotion from ${selected.name}`);
}

function seedRuntime() {
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.mkdirSync(stateDir, { recursive: true });
  restoreInterruptedPromotion();
  if (!fs.existsSync(path.join(liveData, "hospital_master.csv"))) {
    fs.mkdirSync(liveData, { recursive: true });
    fs.cpSync(path.join(root, "data"), liveData, { recursive: true, force: true });
  }
  if (!fs.existsSync(boundaryFile)) {
    fs.copyFileSync(path.join(root, "src", "data", "koreaGeo.json"), boundaryFile);
  }
  // Railway의 volume 연결 서비스처럼 동시에 한 인스턴스만 마운트되는 환경에서만
  // 명시적으로 켠다. 기본값으로 지우면 rolling deploy 중 살아 있는 worker의 lock을
  // 새 인스턴스가 제거할 수 있다.
  if (process.env.CLEAR_STALE_PIPELINE_LOCK_ON_START === "true") {
    fs.rmSync(lockFile, { force: true });
  }
}

function pipelineEnvironment() {
  return {
    ...process.env,
    PIPELINE_DATA_DIR: liveData,
    PIPELINE_LIVE_DATA_DIR: liveData,
    PIPELINE_STATE_DIR: stateDir,
    BOUNDARY_FILE: boundaryFile,
    BED_HISTORY_RETENTION_DAYS: process.env.BED_HISTORY_RETENTION_DAYS || "30",
    PYTHONIOENCODING: "utf-8",
  };
}

function updateStatus(values) {
  previousStatus = { ...previousStatus, ...values };
  writeJsonAtomic(statusFile, previousStatus);
}

function runJob(mode, trigger) {
  if (shuttingDown) return false;
  if (currentJob) {
    if (!pendingJob || mode === "full") {
      pendingJob = { mode, trigger };
      updateStatus({ queuedMode: mode, queuedTrigger: trigger });
      console.log(`[pipeline] queued ${mode} refresh (${trigger})`);
    }
    return false;
  }
  const command = mode === "full" ? "scripts/run_pipeline.py" : "scripts/run_bed_refresh.py";
  const startedAt = new Date().toISOString();
  const attemptField = mode === "full" ? "lastFullAttemptAt" : "lastBedsAttemptAt";
  updateStatus({
    state: "running",
    mode,
    trigger,
    startedAt,
    finishedAt: null,
    error: null,
    queuedMode: null,
    queuedTrigger: null,
    [attemptField]: startedAt,
  });
  console.log(`[pipeline] starting ${mode} refresh (${trigger})`);
  currentJob = spawn(python, [command], {
    cwd: root,
    env: pipelineEnvironment(),
    stdio: "inherit",
    detached: process.platform !== "win32",
  });
  let spawnError = null;
  currentJob.once("error", (error) => {
    spawnError = error;
    console.error(`[pipeline] ${mode} failed to start: ${error.name}`);
  });
  currentJob.once("close", (code, signal) => {
    const finishedAt = new Date().toISOString();
    const success = !spawnError && code === 0;
  const successfulTimestamps = success
      ? {
          lastSuccessAt: finishedAt,
          lastSuccessfulMode: mode,
          ...(mode === "full"
            ? { lastFullSuccessAt: finishedAt, lastBedsSuccessAt: finishedAt }
            : { lastBedsSuccessAt: finishedAt }),
        }
      : {
          lastFailureAt: finishedAt,
          ...(mode === "full"
            ? { lastFullFailureAt: finishedAt }
            : { lastBedsFailureAt: finishedAt }),
        };
    updateStatus({
      state: success ? "idle" : "failed",
      mode,
      trigger,
      finishedAt,
      error: success
        ? null
        : (spawnError ? `process failed to start (${spawnError.code || spawnError.name})` : `process exited (${signal || code || "unknown"})`),
      ...successfulTimestamps,
    });
    console.log(`[pipeline] ${mode} refresh ${success ? "completed" : "failed"}`);
    currentJob = null;
    const queued = pendingJob;
    pendingJob = null;
    if (queued && !shuttingDown) {
      setTimeout(() => runJob(queued.mode, queued.trigger), 250);
    }
  });
  return true;
}

function consumeManualRequest() {
  const request = readJson(requestFile);
  if (!request || currentJob) return;
  fs.rmSync(requestFile, { force: true });
  runJob(request.mode === "full" ? "full" : "beds", "manual");
}

function latestStatusTime(fields) {
  const values = fields
    .map((field) => Date.parse(previousStatus[field] || ""))
    .filter(Number.isFinite);
  return values.length > 0 ? Math.max(...values) : Date.now();
}

function retryCooldownElapsed(mode, now) {
  const modeFailureField = mode === "full" ? "lastFullFailureAt" : "lastBedsFailureAt";
  const modeFailure = Date.parse(previousStatus[modeFailureField] || "");
  const legacyFailure = previousStatus.mode === mode
    ? Date.parse(previousStatus.lastFailureAt || "")
    : Number.NaN;
  const lastFailure = Math.max(
    Number.isFinite(modeFailure) ? modeFailure : 0,
    Number.isFinite(legacyFailure) ? legacyFailure : 0,
  );
  return lastFailure === 0 || now - lastFailure >= failureRetryMinutes * 60_000;
}

function runDueScheduledJob() {
  if (currentJob || shuttingDown) return;
  const now = Date.now();
  const fullAnchor = latestStatusTime([
    "lastFullSuccessAt",
    "schedulerStartedAt",
  ]);
  const bedsAnchor = latestStatusTime([
    "lastBedsSuccessAt",
    "schedulerStartedAt",
  ]);
  if (
    now - fullAnchor >= fullIntervalHours * 3_600_000
    && retryCooldownElapsed("full", now)
  ) {
    runJob("full", "schedule");
  } else if (
    now - bedsAnchor >= fastIntervalMinutes * 60_000
    && retryCooldownElapsed("beds", now)
  ) {
    runJob("beds", "schedule");
  }
}

function startScheduler() {
  if (!schedulerEnabled) {
    updateStatus({ state: "idle", schedulerEnabled: false });
    console.log("[pipeline] scheduler disabled");
    return;
  }
  const schedulerStartedAt = Number.isFinite(Date.parse(previousStatus.schedulerStartedAt || ""))
    ? previousStatus.schedulerStartedAt
    : new Date().toISOString();
  updateStatus({
    state: previousStatus.state === "running" ? "idle" : (previousStatus.state || "idle"),
    schedulerEnabled: true,
    fastIntervalMinutes,
    fullIntervalHours,
    failureRetryMinutes,
    schedulerStartedAt,
  });
  setInterval(consumeManualRequest, 5_000);
  setInterval(runDueScheduledJob, schedulerTickMilliseconds);
  setTimeout(runDueScheduledJob, 1_000);
  if (process.env.RUN_FAST_REFRESH_ON_START === "true") {
    setTimeout(() => runJob("beds", "startup"), 30_000);
  }
}

function startWeb() {
  const standalone = path.join(root, "server.js");
  const localStandalone = path.join(root, ".next", "standalone", "server.js");
  let executable = process.execPath;
  let args;
  if (fs.existsSync(standalone)) {
    args = [standalone];
  } else if (fs.existsSync(localStandalone)) {
    args = [localStandalone];
  } else {
    args = [path.join(root, "node_modules", "next", "dist", "bin", "next"), "start", "-p", port];
  }
  webProcess = spawn(executable, args, {
    cwd: root,
    env: {
      ...pipelineEnvironment(),
      NODE_ENV: "production",
      HOSTNAME: "0.0.0.0",
      PORT: port,
      PIPELINE_MUTATIONS_ENABLED: schedulerEnabled ? "true" : "false",
    },
    stdio: "inherit",
  });
  webProcess.once("exit", (code, signal) => {
    if (shuttingDown) return;
    console.error(`[web] stopped (${signal || code || "unknown"})`);
    terminatePipeline("SIGTERM");
    process.exit(code || 1);
  });
}

function terminatePipeline(signal) {
  if (!currentJob?.pid) return;
  try {
    if (process.platform === "win32") currentJob.kill(signal);
    else process.kill(-currentJob.pid, signal);
  } catch (error) {
    if (error?.code !== "ESRCH") console.error(`[pipeline] stop failed: ${error?.name ?? "Error"}`);
  }
}

function shutdown(signal) {
  shuttingDown = true;
  console.log(`[runtime] received ${signal}`);
  terminatePipeline("SIGTERM");
  if (webProcess) webProcess.kill("SIGTERM");
  setTimeout(() => {
    terminatePipeline("SIGKILL");
    process.exit(0);
  }, 20_000).unref();
}

seedRuntime();
startWeb();
startScheduler();
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
