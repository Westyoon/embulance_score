import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import Papa from "papaparse";

import { clearOwnedPipelineLock } from "./pipeline_lock.mjs";
import {
  bedDeadlineObservationConsumed,
  bedScheduleState,
  boundedBedsFailureRetryMinutes,
  boundedFullFailureRetryMinutes,
  decideScheduledMode,
  inspectBedSourceDeadline,
  mergePendingJob,
  nextBedDeadlineStallCount,
} from "./pipeline_schedule.mjs";

const root = process.cwd();
const localEnvFile = path.join(root, ".env");
if (fs.existsSync(localEnvFile)) process.loadEnvFile(localEnvFile);

const railwayVolumeMount = process.env.RAILWAY_VOLUME_MOUNT_PATH?.trim() || "";
const configuredRuntimeRoot = process.env.PIPELINE_RUNTIME_DIR?.trim() || "";
const runtimeRoot = path.resolve(
  railwayVolumeMount
    || configuredRuntimeRoot
    || path.join(root, "runtime"),
);
const liveData = path.join(runtimeRoot, "data");
const stateDir = path.join(runtimeRoot, "state");
const boundaryFile = path.join(runtimeRoot, "koreaGeo.json");
const statusFile = path.join(stateDir, "pipeline_status.json");
const requestFile = path.join(stateDir, "refresh_request.json");
const lockFile = path.join(stateDir, ".pipeline.lock");
const fullBedReuseFile = path.join(stateDir, "full_bed_reuse.json");
const bedRefreshAuditFile = path.join(liveData, "bed_refresh_audit.json");
const python = process.env.PIPELINE_PYTHON || (
  process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python")
);
const port = process.env.PORT || "3000";
const schedulerEnabled = process.env.ENABLE_PIPELINE_SCHEDULER === "true";
const fastIntervalMinutes = positiveNumber("FAST_REFRESH_INTERVAL_MINUTES", 480);
const fullIntervalHours = positiveNumber("FULL_REFRESH_INTERVAL_HOURS", 24);
const failureRetryMinutes = positiveNumber("PIPELINE_FAILURE_RETRY_MINUTES", 60);
const configuredBedsFailureRetryMinutes = positiveNumber(
  "BEDS_FAILURE_RETRY_MINUTES",
  45,
);
const configuredFullFailureRetryMinutes = positiveNumber(
  "FULL_FAILURE_RETRY_MINUTES",
  1440,
);
const fullFailureRetryMinutes = boundedFullFailureRetryMinutes({
  configuredMinutes: configuredFullFailureRetryMinutes,
  fullIntervalHours,
});
const bedSourceMaxAgeHours = positiveNumber("BED_SOURCE_MAX_AGE_HOURS", 12);
const dataStaleAfterMinutes = positiveNumber("DASHBOARD_DATA_STALE_AFTER_MINUTES", 600);
const bedRefreshSafetyLeadMinutes = positiveNumber("BED_REFRESH_SAFETY_LEAD_MINUTES", 75);
const bedRetryCompletionSafetyMinutes = positiveNumber(
  "BED_RETRY_COMPLETION_SAFETY_MINUTES",
  40,
);
const bedMinimumFailureRetryMinutes = positiveNumber(
  "BED_MINIMUM_FAILURE_RETRY_MINUTES",
  15,
);
const bedStalledSourceRetryMinutes = positiveNumber(
  "BED_STALLED_SOURCE_RETRY_MINUTES",
  15,
);
const bedStalledSourceRetryMaxMinutes = positiveNumber(
  "BED_STALLED_SOURCE_RETRY_MAX_MINUTES",
  480,
);
const bedDeadlineAdvanceToleranceMinutes = positiveNumber(
  "BED_DEADLINE_ADVANCE_TOLERANCE_MINUTES",
  30,
);
const fullStartGuardMinutes = positiveNumber("FULL_REFRESH_START_GUARD_MINUTES", 125);
const bedsRefreshTimeoutMinutes = positiveNumber("BEDS_REFRESH_TIMEOUT_MINUTES", 30);
const fullRefreshTimeoutMinutes = positiveNumber("FULL_REFRESH_TIMEOUT_MINUTES", 120);
const scheduleConfig = {
  fastIntervalMinutes,
  fullIntervalHours,
  bedsFailureRetryMinutes: configuredBedsFailureRetryMinutes,
  fullFailureRetryMinutes,
  bedSourceMaxAgeHours,
  dataStaleAfterMinutes,
  bedRefreshSafetyLeadMinutes,
  bedRetryCompletionSafetyMinutes,
  bedMinimumFailureRetryMinutes,
  bedStalledSourceRetryMinutes,
  bedStalledSourceRetryMaxMinutes,
  bedDeadlineAdvanceToleranceMinutes,
  fullStartGuardMinutes,
};
const bedsFailureRetryMinutes = boundedBedsFailureRetryMinutes(scheduleConfig);
const schedulerTickMilliseconds = 60_000;

let currentJob = null;
let currentMode = null;
let pendingJob = null;
let webProcess = null;
let previousStatus = readJson(statusFile) || {};
let shuttingDown = false;
let runtimeSeeded = false;
let refreshStartedSinceBoot = false;

validateRuntimeConfiguration();

function validateRuntimeConfiguration() {
  const onRailway = Boolean(process.env.RAILWAY_ENVIRONMENT_ID || process.env.RAILWAY_PROJECT_ID);
  if (schedulerEnabled && onRailway && !railwayVolumeMount) {
    throw new Error(
      "ENABLE_PIPELINE_SCHEDULER=true on Railway requires a persistent Volume "
      + "and RAILWAY_VOLUME_MOUNT_PATH.",
    );
  }
  if (
    schedulerEnabled
    && railwayVolumeMount
    && configuredRuntimeRoot
    && path.resolve(railwayVolumeMount) !== path.resolve(configuredRuntimeRoot)
  ) {
    throw new Error(
      "PIPELINE_RUNTIME_DIR must match RAILWAY_VOLUME_MOUNT_PATH when the scheduler is enabled.",
    );
  }
  if (configuredBedsFailureRetryMinutes > bedsFailureRetryMinutes) {
    console.warn(
      `[pipeline] BEDS_FAILURE_RETRY_MINUTES=${configuredBedsFailureRetryMinutes} is unsafe `
      + `for the configured freshness window; using ${bedsFailureRetryMinutes} minutes`,
    );
  }
  if (configuredFullFailureRetryMinutes < fullFailureRetryMinutes) {
    console.warn(
      `[pipeline] FULL_FAILURE_RETRY_MINUTES=${configuredFullFailureRetryMinutes} is too short `
      + `for maintenance refreshes; using ${fullFailureRetryMinutes} minutes`,
    );
  }
  const fullShutdownAndDispatchMinutes = 20 / 60 + schedulerTickMilliseconds / 60_000;
  if (
    fullRefreshTimeoutMinutes + fullShutdownAndDispatchMinutes
    > fullStartGuardMinutes
  ) {
    throw new Error(
      "FULL_REFRESH_START_GUARD_MINUTES must cover the full timeout, shutdown, and dispatch",
    );
  }
  if (bedRetryCompletionSafetyMinutes < bedsRefreshTimeoutMinutes + 5) {
    throw new Error(
      "BED_RETRY_COMPLETION_SAFETY_MINUTES must cover the beds timeout plus promotion margin",
    );
  }
  if (
    bedRefreshSafetyLeadMinutes
    < bedsRefreshTimeoutMinutes + bedRetryCompletionSafetyMinutes
      + schedulerTickMilliseconds / 60_000
  ) {
    throw new Error(
      "BED_REFRESH_SAFETY_LEAD_MINUTES must cover the first attempt, retry completion, and dispatch",
    );
  }
}

function positiveNumber(name, fallback) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return value;
}

function readBedRefreshDeadline() {
  try {
    const filename = path.join(liveData, "bed_status.csv");
    const parsed = Papa.parse(fs.readFileSync(filename, "utf8"), {
      header: true,
      skipEmptyLines: true,
    });
    if (parsed.errors.length > 0) {
      throw new Error(`bed_status.csv parse failed (${parsed.errors[0].code || "unknown"})`);
    }
    const required = ["가용병상", "전체병상", "포화율", "API기준시각"];
    if (!required.every((field) => parsed.meta.fields?.includes(field))) {
      throw new Error("bed_status.csv is missing freshness fields");
    }
    const inspection = inspectBedSourceDeadline(parsed.data, bedSourceMaxAgeHours);
    return { ...inspection, known: inspection.deadlineAt != null };
  } catch (error) {
    if (error?.code !== "ENOENT") {
      console.warn(`[pipeline] could not inspect bed source deadline: ${error?.name ?? "Error"}`);
    }
    return { deadlineAt: null, fingerprint: null, known: false };
  }
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
    runtimeSeeded = true;
  }
  // An existing data directory is one validated generation. Repository-managed
  // inputs enter it only through a full pipeline staging promotion, together
  // with every artifact that depends on them.
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
    const merged = mergePendingJob(pendingJob, { mode, trigger });
    if (
      merged?.mode !== pendingJob?.mode
      || merged?.trigger !== pendingJob?.trigger
    ) {
      pendingJob = merged;
      updateStatus({ queuedMode: merged.mode, queuedTrigger: merged.trigger });
      console.log(`[pipeline] queued ${merged.mode} refresh (${merged.trigger})`);
    }
    return false;
  }
  refreshStartedSinceBoot = true;
  const command = mode === "full" ? "scripts/run_pipeline.py" : "scripts/run_bed_refresh.py";
  const startedAt = new Date().toISOString();
  const attemptField = mode === "full" ? "lastFullAttemptAt" : "lastBedsAttemptAt";
  const attemptedBedDeadline = mode === "beds"
    ? readBedRefreshDeadline()
    : undefined;
  if (mode === "full") fs.rmSync(fullBedReuseFile, { force: true });
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
    ...(mode === "beds" ? {
      lastBedsAttemptedDeadlineAt: attemptedBedDeadline.deadlineAt,
      lastBedsAttemptedDeadlineFingerprint: attemptedBedDeadline.fingerprint,
    } : {}),
  });
  console.log(`[pipeline] starting ${mode} refresh (${trigger})`);
  currentMode = mode;
  const jobProcess = spawn(python, [command], {
    cwd: root,
    env: pipelineEnvironment(),
    stdio: "inherit",
    detached: process.platform !== "win32",
  });
  currentJob = jobProcess;
  const timeoutMinutes = mode === "full"
    ? fullRefreshTimeoutMinutes
    : bedsRefreshTimeoutMinutes;
  let timedOut = false;
  let forceKillTimer = null;
  const jobTimeout = setTimeout(() => {
    timedOut = true;
    console.error(`[pipeline] ${mode} exceeded ${timeoutMinutes} minute timeout`);
    terminateChildProcess(jobProcess, "SIGTERM");
    forceKillTimer = setTimeout(() => terminateChildProcess(jobProcess, "SIGKILL"), 20_000);
    forceKillTimer.unref();
  }, timeoutMinutes * 60_000);
  jobTimeout.unref();
  let spawnError = null;
  jobProcess.once("error", (error) => {
    spawnError = error;
    console.error(`[pipeline] ${mode} failed to start: ${error.name}`);
  });
  jobProcess.once("close", (code, signal) => {
    clearTimeout(jobTimeout);
    if (forceKillTimer) clearTimeout(forceKillTimer);
    let timeoutRecoveryError = null;
    if (timedOut) {
      try {
        restoreInterruptedPromotion();
      } catch (error) {
        timeoutRecoveryError = error;
        console.error(`[pipeline] timeout recovery failed: ${error?.name ?? "Error"}`);
      }
      try {
        const lockRecovery = clearOwnedPipelineLock(lockFile, jobProcess.pid);
        if (lockRecovery.reason === "owner-mismatch") {
          throw new Error("pipeline lock belongs to a different process");
        }
      } catch (error) {
        timeoutRecoveryError ??= error;
        console.error(`[pipeline] lock recovery failed: ${error?.name ?? "Error"}`);
      }
    }
    const finishedAt = new Date().toISOString();
    const success = !spawnError && !timedOut && code === 0;
    const bedReuseAudit = mode === "full" ? readJson(fullBedReuseFile) : null;
    if (mode === "full") fs.rmSync(fullBedReuseFile, { force: true });
    const reusedBedSnapshot = bedReuseAudit?.reused === true;
    const refreshedBeds = success && (mode === "beds" || !reusedBedSnapshot);
    const bedRefreshAudit = refreshedBeds ? readJson(bedRefreshAuditFile) : null;
    const bedRefreshFailureCount = refreshedBeds
      ? (bedRefreshAudit?.failedRegionCount ?? 1)
      : 0;
    const refreshedBedDeadline = refreshedBeds ? readBedRefreshDeadline() : null;
    const bedDeadlineStalled = mode === "beds" && refreshedBeds
      ? bedDeadlineObservationConsumed({
          attemptAt: startedAt,
          successAt: finishedAt,
          attemptedDeadlineAt: attemptedBedDeadline.deadlineAt,
          attemptedFingerprint: attemptedBedDeadline.fingerprint,
          currentDeadlineAt: refreshedBedDeadline.deadlineAt,
          currentFingerprint: refreshedBedDeadline.fingerprint,
          config: scheduleConfig,
        })
      : false;
    const bedDeadlineStallCount = nextBedDeadlineStallCount({
      stalled: bedDeadlineStalled,
      previousCount: previousStatus.lastBedsDeadlineStallCount,
      previousFingerprint: previousStatus.lastBedsDeadlineStallFingerprint,
      currentFingerprint: refreshedBedDeadline?.fingerprint,
    });
    const fullBedReuseStatus = mode === "full" && success
      ? {
          lastFullReusedBedSnapshot: reusedBedSnapshot,
          lastFullBedSnapshotAt: bedReuseAudit?.snapshotCollectedAt || null,
          lastFullBedSnapshotAgeMinutes: bedReuseAudit?.snapshotAgeMinutes ?? null,
          lastFullBedUsableHospitals: bedReuseAudit?.usableHospitals ?? null,
          lastFullBedStaleSourceHospitals: bedReuseAudit?.staleSourceHospitals ?? null,
          lastFullBedSanitizedSourceHospitals: bedReuseAudit?.sanitizedSourceHospitals ?? null,
          lastFullBedSourceMaxAgeHours: bedReuseAudit?.sourceMaxAgeHours ?? null,
        }
      : {};
    const successfulTimestamps = success
      ? {
          lastSuccessAt: finishedAt,
          lastSuccessfulMode: mode,
          ...(mode === "full"
            ? {
                lastFullSuccessAt: finishedAt,
                ...(!reusedBedSnapshot ? { lastBedsSuccessAt: finishedAt } : {}),
              }
            : { lastBedsSuccessAt: finishedAt }),
        }
      : {
          lastFailureAt: finishedAt,
          lastFailureMode: mode,
          ...(mode === "full"
            ? { lastFullFailureAt: finishedAt }
            : { lastBedsFailureAt: finishedAt }),
        };
    const bedRefreshStatus = refreshedBeds
      ? {
          lastBedsPartialFailureAt: bedRefreshFailureCount > 0
            ? finishedAt
            : null,
          lastBedsFailedRegionCount: bedRefreshFailureCount,
          lastBedsFailedRegions: bedRefreshAudit?.failedRegions ?? [
            { reason: "bed_refresh_audit.json missing or invalid" },
          ],
          lastBedsAuditMissing: bedRefreshAudit == null,
          lastBedsDeadlineStallCount: bedDeadlineStallCount,
          lastBedsDeadlineStallAt: bedDeadlineStalled ? finishedAt : null,
          lastBedsDeadlineStallFingerprint: bedDeadlineStalled
            ? refreshedBedDeadline.fingerprint
            : null,
          lastBedsFreshFallbackHospitals: bedRefreshAudit?.freshFallbackHospitals ?? 0,
          lastBedsMaskedFailedRegionHospitals:
            bedRefreshAudit?.maskedFailedRegionHospitals ?? 0,
          lastBedsNewResponseHospitals: bedRefreshAudit?.newResponseHospitals ?? null,
          lastBedsUsableHospitals: bedRefreshAudit?.usableHospitals ?? null,
        }
      : {};
    updateStatus({
      state: success ? "idle" : "failed",
      mode,
      trigger,
      finishedAt,
      error: success
        ? null
        : (
            timedOut
              ? `process timed out after ${timeoutMinutes} minutes`
              : (spawnError
                  ? `process failed to start (${spawnError.code || spawnError.name})`
                  : `process exited (${signal || code || "unknown"})`)
          ),
      timeoutRecoveryFailed: timeoutRecoveryError != null,
      ...fullBedReuseStatus,
      ...bedRefreshStatus,
      ...successfulTimestamps,
    });
    console.log(`[pipeline] ${mode} refresh ${success ? "completed" : "failed"}`);
    currentJob = null;
    currentMode = null;
    if (timeoutRecoveryError) {
      shuttingDown = true;
      if (webProcess) webProcess.kill("SIGTERM");
      setTimeout(() => process.exit(1), 1_000).unref();
      return;
    }
    const queued = pendingJob;
    pendingJob = null;
    if (queued && queued.trigger !== "schedule" && !shuttingDown) {
      setTimeout(() => runJob(queued.mode, queued.trigger), 250);
    } else if (!shuttingDown) {
      if (queued) updateStatus({ queuedMode: null, queuedTrigger: null });
      setTimeout(runDueScheduledJob, 250);
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

function runDueScheduledJob() {
  if (shuttingDown) return;
  const now = Date.now();
  const bedDeadline = readBedRefreshDeadline();
  const schedulingBedDeadlineAt = bedDeadline.deadlineAt || new Date(0).toISOString();
  const bedsState = bedScheduleState({
    status: previousStatus,
    now,
    config: scheduleConfig,
    bedDeadlineAt: schedulingBedDeadlineAt,
    bedDeadlineFingerprint: bedDeadline.fingerprint,
  });
  const scheduleStatus = {
    bedRefreshDeadlineAt: bedDeadline.deadlineAt,
    bedRefreshDeadlineFingerprint: bedDeadline.fingerprint,
    bedRefreshDeadlineKnown: bedDeadline.known,
    nextBedsAttemptAt: new Date(bedsState.nextAttemptAt).toISOString(),
  };
  if (
    scheduleStatus.bedRefreshDeadlineAt !== previousStatus.bedRefreshDeadlineAt
    || scheduleStatus.bedRefreshDeadlineFingerprint
      !== previousStatus.bedRefreshDeadlineFingerprint
    || scheduleStatus.bedRefreshDeadlineKnown !== previousStatus.bedRefreshDeadlineKnown
    || scheduleStatus.nextBedsAttemptAt !== previousStatus.nextBedsAttemptAt
  ) updateStatus(scheduleStatus);

  const mode = decideScheduledMode({
    status: previousStatus,
    now,
    config: scheduleConfig,
    bedDeadlineAt: schedulingBedDeadlineAt,
    bedDeadlineFingerprint: bedDeadline.fingerprint,
  });
  if (!mode || mode === currentMode) return;
  runJob(mode, "schedule");
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
    configuredBedsFailureRetryMinutes,
    bedsFailureRetryMinutes,
    configuredFullFailureRetryMinutes,
    fullFailureRetryMinutes,
    bedSourceMaxAgeHours,
    dataStaleAfterMinutes,
    bedRefreshSafetyLeadMinutes,
    bedRetryCompletionSafetyMinutes,
    bedMinimumFailureRetryMinutes,
    bedStalledSourceRetryMinutes,
    bedStalledSourceRetryMaxMinutes,
    bedDeadlineAdvanceToleranceMinutes,
    fullStartGuardMinutes,
    bedsRefreshTimeoutMinutes,
    fullRefreshTimeoutMinutes,
    schedulerStartedAt,
  });
  setInterval(consumeManualRequest, 5_000);
  setInterval(runDueScheduledJob, schedulerTickMilliseconds);
  setTimeout(runDueScheduledJob, 1_000);
  if (runtimeSeeded || process.env.RUN_FAST_REFRESH_ON_START === "true") {
    setTimeout(() => {
      if (refreshStartedSinceBoot) {
        console.log("[pipeline] skipped startup beds refresh; another refresh already started");
        return;
      }
      runJob("beds", "startup");
    }, 30_000);
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

function terminateChildProcess(childProcess, signal) {
  if (!childProcess?.pid) return;
  try {
    if (process.platform === "win32") childProcess.kill(signal);
    else process.kill(-childProcess.pid, signal);
  } catch (error) {
    if (error?.code !== "ESRCH") console.error(`[pipeline] stop failed: ${error?.name ?? "Error"}`);
  }
}

function terminatePipeline(signal) {
  terminateChildProcess(currentJob, signal);
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
