import crypto from "node:crypto";

const MINUTE_MILLISECONDS = 60_000;
const HOUR_MILLISECONDS = 60 * MINUTE_MILLISECONDS;

export const DEFAULT_SCHEDULE_CONFIG = Object.freeze({
  refreshTimeZone: "Asia/Seoul",
  coreRefreshStartHour: 21,
  coreRefreshEndHour: 9,
  coreRefreshIntervalMinutes: 480,
  offHoursRefreshIntervalMinutes: 480,
  fullIntervalHours: 24,
  bedsFailureRetryMinutes: 45,
  fullFailureRetryMinutes: 1440,
  bedSourceMaxAgeHours: 12,
  dataStaleAfterMinutes: 600,
  bedRefreshSafetyLeadMinutes: 75,
  bedRetryCompletionSafetyMinutes: 40,
  bedMinimumFailureRetryMinutes: 15,
  bedStalledSourceRetryMinutes: 15,
  bedStalledSourceRetryMaxMinutes: 480,
  bedDeadlineAdvanceToleranceMinutes: 30,
  fullStartGuardMinutes: 125,
});

function positiveNumber(value, name) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    throw new RangeError(`${name} must be a positive number`);
  }
  return number;
}

function scheduleHour(value, name) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 0 || number > 23) {
    throw new RangeError(`${name} must be an integer from 0 through 23`);
  }
  return number;
}

const hourFormatters = new Map();

function normalizedTimeZone(value) {
  const timeZone = String(value ?? "").trim();
  if (!timeZone) throw new RangeError("refreshTimeZone must be a valid IANA time zone");
  try {
    if (!hourFormatters.has(timeZone)) {
      hourFormatters.set(timeZone, new Intl.DateTimeFormat("en-GB-u-nu-latn", {
        timeZone,
        hour: "2-digit",
        hourCycle: "h23",
      }));
    }
    hourFormatters.get(timeZone).format(new Date(0));
  } catch {
    throw new RangeError("refreshTimeZone must be a valid IANA time zone");
  }
  return timeZone;
}

function localHourAt(timestamp, timeZone) {
  const hourPart = hourFormatters.get(timeZone)
    .formatToParts(new Date(timestamp))
    .find(({ type }) => type === "hour")?.value;
  const hour = Number(hourPart);
  if (!Number.isInteger(hour)) {
    throw new RangeError(`could not determine the local hour in ${timeZone}`);
  }
  return hour;
}

function isCoreHour(hour, policy) {
  const start = policy.coreRefreshStartHour;
  const end = policy.coreRefreshEndHour;
  if (start === end) return true;
  if (start < end) return hour >= start && hour < end;
  return hour >= start || hour < end;
}

function cadenceAtMillis(nowMillis, policy) {
  const core = isCoreHour(localHourAt(nowMillis, policy.refreshTimeZone), policy);
  return {
    window: core ? "core" : "off-hours",
    intervalMinutes: core
      ? policy.coreRefreshIntervalMinutes
      : policy.offHoursRefreshIntervalMinutes,
  };
}

function nextCoreWindowStartMillis(nowMillis, policy) {
  if (cadenceAtMillis(nowMillis, policy).window === "core") return null;

  const firstWholeMinute = Math.ceil(nowMillis / MINUTE_MILLISECONDS)
    * MINUTE_MILLISECONDS;
  const coarseStep = 15 * MINUTE_MILLISECONDS;
  const searchLimit = nowMillis + 72 * HOUR_MILLISECONDS;
  for (
    let coarse = firstWholeMinute;
    coarse <= searchLimit;
    coarse += coarseStep
  ) {
    if (cadenceAtMillis(coarse, policy).window !== "core") continue;
    const fineStart = Math.max(firstWholeMinute, coarse - coarseStep);
    for (
      let candidate = fineStart;
      candidate <= coarse;
      candidate += MINUTE_MILLISECONDS
    ) {
      if (cadenceAtMillis(candidate, policy).window === "core") return candidate;
    }
  }
  throw new RangeError(
    `could not find the next core refresh window in ${policy.refreshTimeZone}`,
  );
}

function timestampMillis(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (value instanceof Date) {
    const millis = value.getTime();
    return Number.isFinite(millis) ? millis : null;
  }
  const millis = Date.parse(value || "");
  return Number.isFinite(millis) ? millis : null;
}

function normalizedNow(now) {
  const millis = timestampMillis(now);
  if (millis == null) throw new RangeError("now must be a valid timestamp");
  return millis;
}

function finiteCsvNumber(value) {
  if (value == null || String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function koreanBedSourceTimestampMillis(value) {
  const text = String(value ?? "").trim().replace(/\.0+$/, "");
  const match = text.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  const localAsUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  const check = new Date(localAsUtc);
  if (
    check.getUTCFullYear() !== year
    || check.getUTCMonth() !== month - 1
    || check.getUTCDate() !== day
    || check.getUTCHours() !== hour
    || check.getUTCMinutes() !== minute
    || check.getUTCSeconds() !== second
  ) return null;
  return localAsUtc - 9 * HOUR_MILLISECONDS;
}

export function inspectBedSourceDeadline(rows, maxAgeHours = 12) {
  const ageHours = positiveNumber(maxAgeHours, "maxAgeHours");
  const observations = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => {
      const totalBeds = finiteCsvNumber(row?.["전체병상"]);
      const availableBeds = finiteCsvNumber(row?.["가용병상"]);
      const saturation = finiteCsvNumber(row?.["포화율"]);
      return totalBeds != null && totalBeds > 0
        && availableBeds != null && availableBeds >= 0
        && saturation != null;
    })
    .map(({ row, index }) => ({
      sourceMillis: koreanBedSourceTimestampMillis(row["API기준시각"]),
      sourceId: String(row["기관코드"] || `row-${index}`).trim(),
    }))
    .filter(({ sourceMillis }) => sourceMillis != null);
  if (observations.length === 0) {
    return { deadlineAt: null, fingerprint: null };
  }
  const earliestSource = Math.min(...observations.map(({ sourceMillis }) => sourceMillis));
  const earliestSourceIds = observations
    .filter(({ sourceMillis }) => sourceMillis === earliestSource)
    .map(({ sourceId }) => sourceId)
    .sort();
  const fingerprint = crypto
    .createHash("sha256")
    .update(JSON.stringify(earliestSourceIds))
    .digest("hex")
    .slice(0, 16);
  return {
    deadlineAt: new Date(earliestSource + ageHours * HOUR_MILLISECONDS).toISOString(),
    fingerprint,
  };
}

export function earliestBedSourceDeadline(rows, maxAgeHours = 12) {
  return inspectBedSourceDeadline(rows, maxAgeHours).deadlineAt;
}

function latestTimestamp(status, fields, fallback) {
  const timestamps = fields
    .map((field) => timestampMillis(status?.[field]))
    .filter((value) => value != null);
  return timestamps.length > 0 ? Math.max(...timestamps) : fallback;
}

export function normalizeScheduleConfig(config = {}) {
  return {
    refreshTimeZone: normalizedTimeZone(
      config.refreshTimeZone ?? DEFAULT_SCHEDULE_CONFIG.refreshTimeZone,
    ),
    coreRefreshStartHour: scheduleHour(
      config.coreRefreshStartHour ?? DEFAULT_SCHEDULE_CONFIG.coreRefreshStartHour,
      "coreRefreshStartHour",
    ),
    coreRefreshEndHour: scheduleHour(
      config.coreRefreshEndHour ?? DEFAULT_SCHEDULE_CONFIG.coreRefreshEndHour,
      "coreRefreshEndHour",
    ),
    coreRefreshIntervalMinutes: positiveNumber(
      config.coreRefreshIntervalMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.coreRefreshIntervalMinutes,
      "coreRefreshIntervalMinutes",
    ),
    offHoursRefreshIntervalMinutes: positiveNumber(
      config.offHoursRefreshIntervalMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.offHoursRefreshIntervalMinutes,
      "offHoursRefreshIntervalMinutes",
    ),
    fullIntervalHours: positiveNumber(
      config.fullIntervalHours ?? DEFAULT_SCHEDULE_CONFIG.fullIntervalHours,
      "fullIntervalHours",
    ),
    bedsFailureRetryMinutes: positiveNumber(
      config.bedsFailureRetryMinutes ?? DEFAULT_SCHEDULE_CONFIG.bedsFailureRetryMinutes,
      "bedsFailureRetryMinutes",
    ),
    fullFailureRetryMinutes: positiveNumber(
      config.fullFailureRetryMinutes ?? DEFAULT_SCHEDULE_CONFIG.fullFailureRetryMinutes,
      "fullFailureRetryMinutes",
    ),
    bedSourceMaxAgeHours: positiveNumber(
      config.bedSourceMaxAgeHours ?? DEFAULT_SCHEDULE_CONFIG.bedSourceMaxAgeHours,
      "bedSourceMaxAgeHours",
    ),
    dataStaleAfterMinutes: positiveNumber(
      config.dataStaleAfterMinutes ?? DEFAULT_SCHEDULE_CONFIG.dataStaleAfterMinutes,
      "dataStaleAfterMinutes",
    ),
    bedRefreshSafetyLeadMinutes: positiveNumber(
      config.bedRefreshSafetyLeadMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.bedRefreshSafetyLeadMinutes,
      "bedRefreshSafetyLeadMinutes",
    ),
    bedRetryCompletionSafetyMinutes: positiveNumber(
      config.bedRetryCompletionSafetyMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.bedRetryCompletionSafetyMinutes,
      "bedRetryCompletionSafetyMinutes",
    ),
    bedMinimumFailureRetryMinutes: positiveNumber(
      config.bedMinimumFailureRetryMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.bedMinimumFailureRetryMinutes,
      "bedMinimumFailureRetryMinutes",
    ),
    bedStalledSourceRetryMinutes: positiveNumber(
      config.bedStalledSourceRetryMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.bedStalledSourceRetryMinutes,
      "bedStalledSourceRetryMinutes",
    ),
    bedStalledSourceRetryMaxMinutes: positiveNumber(
      config.bedStalledSourceRetryMaxMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.bedStalledSourceRetryMaxMinutes,
      "bedStalledSourceRetryMaxMinutes",
    ),
    bedDeadlineAdvanceToleranceMinutes: positiveNumber(
      config.bedDeadlineAdvanceToleranceMinutes
        ?? DEFAULT_SCHEDULE_CONFIG.bedDeadlineAdvanceToleranceMinutes,
      "bedDeadlineAdvanceToleranceMinutes",
    ),
    fullStartGuardMinutes: positiveNumber(
      config.fullStartGuardMinutes ?? DEFAULT_SCHEDULE_CONFIG.fullStartGuardMinutes,
      "fullStartGuardMinutes",
    ),
  };
}

/**
 * Return the largest process-level beds retry delay that still leaves the
 * configured safety lead before the source TTL expires after a normal run.
 */
export function bedsRetryCeilingMinutes(config = {}) {
  const policy = normalizeScheduleConfig(config);
  const freshnessWindow = Math.min(
    policy.bedSourceMaxAgeHours * 60,
    policy.dataStaleAfterMinutes,
  );
  const freshnessBudget = (
    freshnessWindow
    - Math.max(
      policy.coreRefreshIntervalMinutes,
      policy.offHoursRefreshIntervalMinutes,
    )
    - policy.bedRefreshSafetyLeadMinutes
  );
  if (freshnessBudget < 1) {
    throw new RangeError(
      "bed freshness policy must leave at least one minute for a failed refresh retry",
    );
  }
  return Math.floor(freshnessBudget);
}

export function bedRefreshCadenceAt(now = Date.now(), config = {}) {
  const nowMillis = normalizedNow(now);
  const policy = normalizeScheduleConfig(config);
  const cadence = cadenceAtMillis(nowMillis, policy);
  const nextCoreWindowAt = cadence.window === "core"
    ? null
    : nextCoreWindowStartMillis(nowMillis, policy);
  return {
    ...cadence,
    timeZone: policy.refreshTimeZone,
    nextCoreWindowAt,
  };
}

export function boundedBedsFailureRetryMinutes(config = {}) {
  const policy = normalizeScheduleConfig(config);
  return Math.min(
    policy.bedsFailureRetryMinutes,
    bedsRetryCeilingMinutes(policy),
  );
}

export function boundedFullFailureRetryMinutes({
  configuredMinutes = DEFAULT_SCHEDULE_CONFIG.fullFailureRetryMinutes,
  fullIntervalHours = DEFAULT_SCHEDULE_CONFIG.fullIntervalHours,
} = {}) {
  const configured = positiveNumber(configuredMinutes, "configuredMinutes");
  const intervalHours = positiveNumber(fullIntervalHours, "fullIntervalHours");
  return Math.max(configured, Math.min(intervalHours * 60, 1440));
}

function modeFailureAt(mode, status) {
  const specificField = mode === "full" ? "lastFullFailureAt" : "lastBedsFailureAt";
  const successField = mode === "full" ? "lastFullSuccessAt" : "lastBedsSuccessAt";
  const specificFailure = timestampMillis(status?.[specificField]);
  const lastSuccess = timestampMillis(status?.[successField]) ?? 0;
  const legacyFailureMode = status?.lastFailureMode || status?.mode;
  const legacyFailure = legacyFailureMode === mode
    ? timestampMillis(status.lastFailureAt)
    : null;
  const failureAt = Math.max(specificFailure ?? 0, legacyFailure ?? 0);
  return failureAt > lastSuccess ? failureAt : 0;
}

/** Mode-specific cooldowns must never make one mode inherit another mode's failure. */
export function retryCooldownElapsed(mode, status = {}, now = Date.now(), config = {}) {
  if (mode !== "beds" && mode !== "full") {
    throw new RangeError(`unsupported pipeline mode: ${mode}`);
  }
  const nowMillis = normalizedNow(now);
  const policy = normalizeScheduleConfig(config);
  const failureAt = modeFailureAt(mode, status);
  if (failureAt === 0) return true;
  const retryMinutes = mode === "beds"
    ? boundedBedsFailureRetryMinutes(policy)
    : policy.fullFailureRetryMinutes;
  return nowMillis - failureAt >= retryMinutes * MINUTE_MILLISECONDS;
}

export function bedDeadlineObservationConsumed({
  attemptAt,
  successAt,
  attemptedDeadlineAt,
  attemptedFingerprint,
  currentDeadlineAt,
  currentFingerprint,
  config = {},
} = {}) {
  const policy = normalizeScheduleConfig(config);
  const attempt = timestampMillis(attemptAt);
  const success = timestampMillis(successAt);
  const attemptedDeadline = timestampMillis(attemptedDeadlineAt);
  const currentDeadline = timestampMillis(currentDeadlineAt);
  return (
    attempt != null
    && success != null
    && attemptedDeadline != null
    && currentDeadline != null
    && attemptedFingerprint != null
    && attemptedFingerprint === currentFingerprint
    && currentDeadline >= attemptedDeadline
    && currentDeadline - attemptedDeadline
      <= policy.bedDeadlineAdvanceToleranceMinutes * MINUTE_MILLISECONDS
    && success >= attempt
    && attempt >= (
      attemptedDeadline - policy.bedRefreshSafetyLeadMinutes * MINUTE_MILLISECONDS
    )
  );
}

export function nextBedDeadlineStallCount({
  stalled,
  previousCount = 0,
  previousFingerprint = null,
  currentFingerprint = null,
} = {}) {
  if (!stalled) return 0;
  const sameSourceSet = (
    currentFingerprint != null
    && previousFingerprint === currentFingerprint
  );
  return (sameSourceSet ? Math.max(0, Number(previousCount) || 0) : 0) + 1;
}

export function bedScheduleState({
  status = {},
  now = Date.now(),
  config = {},
  bedDeadlineAt = null,
  bedDeadlineFingerprint = null,
} = {}) {
  const nowMillis = normalizedNow(now);
  const policy = normalizeScheduleConfig(config);
  const anchor = latestTimestamp(
    status,
    ["lastBedsSuccessAt", "schedulerStartedAt"],
    nowMillis,
  );
  const cadence = cadenceAtMillis(nowMillis, policy);
  const nextCoreWindowAt = cadence.window === "core"
    ? null
    : nextCoreWindowStartMillis(nowMillis, policy);
  const anchorCadence = cadenceAtMillis(anchor, policy);
  const firstCoreWindowAfterAnchor = anchorCadence.window === "core"
    ? null
    : nextCoreWindowStartMillis(anchor, policy);
  const cadenceDueAt = anchor + cadence.intervalMinutes * MINUTE_MILLISECONDS;
  const intervalDueAt = Math.min(
    cadenceDueAt,
    nextCoreWindowAt ?? Number.POSITIVE_INFINITY,
    firstCoreWindowAfterAnchor ?? Number.POSITIVE_INFINITY,
  );
  const deadline = timestampMillis(bedDeadlineAt);
  const attemptedDeadline = timestampMillis(status?.lastBedsAttemptedDeadlineAt);
  const attemptedFingerprint = status?.lastBedsAttemptedDeadlineFingerprint || null;
  const lastAttempt = timestampMillis(status?.lastBedsAttemptAt) ?? 0;
  const lastSuccess = timestampMillis(status?.lastBedsSuccessAt) ?? 0;
  const deadlineConsumed = bedDeadlineObservationConsumed({
    attemptAt: lastAttempt,
    successAt: lastSuccess,
    attemptedDeadlineAt: attemptedDeadline,
    attemptedFingerprint,
    currentDeadlineAt: deadline,
    currentFingerprint: bedDeadlineFingerprint,
    config: policy,
  });
  const stalledSourceCount = Math.max(
    1,
    Number(status?.lastBedsDeadlineStallCount) || 0,
  );
  const stalledSourceRetryMinutes = Math.min(
    policy.bedStalledSourceRetryMaxMinutes,
    policy.bedStalledSourceRetryMinutes
      * (2 ** Math.min(stalledSourceCount - 1, 10)),
  );
  const deadlineDueAt = deadline == null
    ? null
    : (deadlineConsumed
        ? lastSuccess + stalledSourceRetryMinutes * MINUTE_MILLISECONDS
        : deadline - policy.bedRefreshSafetyLeadMinutes * MINUTE_MILLISECONDS);
  const dueAt = deadlineDueAt == null
    ? intervalDueAt
    : Math.min(intervalDueAt, deadlineDueAt);
  const retryMinutes = boundedBedsFailureRetryMinutes(policy);
  const failureAt = modeFailureAt("beds", status);
  const attemptAt = timestampMillis(status?.lastBedsAttemptAt);
  const lastSuccessAt = timestampMillis(status?.lastBedsSuccessAt) ?? 0;
  const failureRetryAnchor = (
    failureAt > 0
    && attemptAt != null
    && attemptAt > lastSuccessAt
    && attemptAt <= failureAt
  ) ? attemptAt : failureAt;
  const deadlineRetryMinutes = (failedAt) => {
    if (failedAt === 0 || deadline == null) return retryMinutes;
    const untilExpiryMinutes = Math.floor(
      (deadline - failedAt) / MINUTE_MILLISECONDS,
    );
    const minimumRetryMinutes = Math.min(
      retryMinutes,
      policy.bedMinimumFailureRetryMinutes,
    );
    return Math.min(
      retryMinutes,
      Math.max(
        minimumRetryMinutes,
        untilExpiryMinutes - policy.bedRetryCompletionSafetyMinutes,
      ),
    );
  };
  const failureRetryMinutes = deadlineRetryMinutes(failureRetryAnchor);
  const failureRetryDueAt = failureAt === 0
    ? null
    : failureRetryAnchor + failureRetryMinutes * MINUTE_MILLISECONDS;
  const partialFailureCount = Number(status?.lastBedsFailedRegionCount) || 0;
  const partialFailureAt = partialFailureCount > 0
    ? timestampMillis(status?.lastBedsPartialFailureAt)
    : null;
  const partialRetryDueAt = partialFailureAt == null
    ? null
    : partialFailureAt
      + deadlineRetryMinutes(partialFailureAt) * MINUTE_MILLISECONDS;
  const partialDueAt = partialFailureAt;
  const scheduledDueAt = partialDueAt == null
    ? dueAt
    : Math.min(dueAt, partialDueAt);
  const cooldownElapsed = (
    (failureRetryDueAt == null || nowMillis >= failureRetryDueAt)
    && (partialRetryDueAt == null || nowMillis >= partialRetryDueAt)
  );

  return {
    due: nowMillis >= scheduledDueAt,
    ready: nowMillis >= scheduledDueAt && cooldownElapsed,
    cooldownElapsed,
    dueAt: scheduledDueAt,
    intervalDueAt,
    activeRefreshWindow: cadence.window,
    activeRefreshIntervalMinutes: cadence.intervalMinutes,
    nextCoreWindowAt,
    deadlineDueAt,
    deadlineConsumed,
    stalledSourceRetryMinutes,
    failureRetryDueAt,
    failureRetryMinutes,
    partialRetryDueAt,
    nextAttemptAt: Math.max(
      scheduledDueAt,
      failureRetryDueAt ?? 0,
      partialRetryDueAt ?? 0,
    ),
    retryMinutes,
  };
}

export function fullScheduleState({
  status = {},
  now = Date.now(),
  config = {},
} = {}) {
  const nowMillis = normalizedNow(now);
  const policy = normalizeScheduleConfig(config);
  const anchor = latestTimestamp(
    status,
    ["lastFullSuccessAt", "schedulerStartedAt"],
    nowMillis,
  );
  const dueAt = anchor + policy.fullIntervalHours * HOUR_MILLISECONDS;
  const failureAt = modeFailureAt("full", status);
  const retryDueAt = failureAt === 0
    ? null
    : failureAt + policy.fullFailureRetryMinutes * MINUTE_MILLISECONDS;
  const cooldownElapsed = retryCooldownElapsed("full", status, nowMillis, policy);

  return {
    due: nowMillis >= dueAt,
    ready: nowMillis >= dueAt && cooldownElapsed,
    cooldownElapsed,
    dueAt,
    retryDueAt,
    nextAttemptAt: Math.max(dueAt, retryDueAt ?? 0),
    retryMinutes: policy.fullFailureRetryMinutes,
  };
}

/**
 * Select at most one scheduled job. A due beds job reserves the single worker
 * even while its own retry cooldown is active. When the configured bed cadence
 * is shorter than the full-job guard, no gap can ever fit a full refresh. In
 * that case, a full job that was already due may start only in the one-minute
 * handoff window after a successful beds run and only when no earlier source
 * deadline or retry is pending. The single pending slot then coalesces bed work
 * while full is running.
 */
export function decideScheduledMode({
  status = {},
  now = Date.now(),
  config = {},
  bedDeadlineAt = null,
  bedDeadlineFingerprint = null,
} = {}) {
  const beds = bedScheduleState({
    status,
    now,
    config,
    bedDeadlineAt,
    bedDeadlineFingerprint,
  });
  if (beds.due) return beds.ready ? "beds" : null;

  const full = fullScheduleState({ status, now, config });
  const policy = normalizeScheduleConfig(config);
  const nowMillis = normalizedNow(now);
  const bedsWorkerGuardAt = nowMillis
    + policy.fullStartGuardMinutes * MINUTE_MILLISECONDS;
  const cadenceCannotFitFullGuard = (
    beds.activeRefreshIntervalMinutes <= policy.fullStartGuardMinutes
  );
  const lastBedsSuccessAt = timestampMillis(status?.lastBedsSuccessAt);
  const recentBedsHandoff = (
    cadenceCannotFitFullGuard
    && lastBedsSuccessAt != null
    && nowMillis >= lastBedsSuccessAt
    && nowMillis - lastBedsSuccessAt <= MINUTE_MILLISECONDS
    && full.dueAt <= lastBedsSuccessAt
    && beds.dueAt === beds.intervalDueAt
    && beds.nextAttemptAt === beds.intervalDueAt
  );
  return full.ready && (
    beds.nextAttemptAt > bedsWorkerGuardAt || recentBedsHandoff
  ) ? "full" : null;
}

/**
 * Merge single-slot pending work without allowing maintenance full refreshes
 * to displace freshness-critical bed refreshes.
 */
export function mergePendingJob(pendingJob, candidateJob) {
  if (!pendingJob) return candidateJob ? { ...candidateJob } : null;
  if (!candidateJob) return { ...pendingJob };
  if (pendingJob.mode === "beds") return { ...pendingJob };
  if (candidateJob.mode === "beds") return { ...candidateJob };
  return { ...pendingJob };
}
