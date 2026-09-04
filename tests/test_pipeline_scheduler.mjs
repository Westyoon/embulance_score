import assert from "node:assert/strict";
import test from "node:test";

import {
  bedScheduleState,
  bedsRetryCeilingMinutes,
  boundedBedsFailureRetryMinutes,
  boundedFullFailureRetryMinutes,
  decideScheduledMode,
  earliestBedSourceDeadline,
  inspectBedSourceDeadline,
  koreanBedSourceTimestampMillis,
  mergePendingJob,
  nextBedDeadlineStallCount,
  retryCooldownElapsed,
} from "../scripts/pipeline_schedule.mjs";

const NOW = Date.parse("2026-09-04T00:00:00.000Z");
const minutesAgo = (minutes) => new Date(NOW - minutes * 60_000).toISOString();
const minutesFromNow = (minutes) => new Date(NOW + minutes * 60_000).toISOString();

test("Korea-local hospital timestamps produce the earliest usable source deadline", () => {
  const rows = [
    { 기관코드: "A", 전체병상: "20", 가용병상: "3", 포화율: "85", API기준시각: "20260904093000.0" },
    { 기관코드: "B", 전체병상: "10", 가용병상: "", 포화율: "", API기준시각: "20260904080000.0" },
    { 기관코드: "C", 전체병상: "12", 가용병상: "1", 포화율: "91.7", API기준시각: "20260904090000.0" },
  ];

  assert.equal(
    koreanBedSourceTimestampMillis("20260904090000.0"),
    Date.parse("2026-09-04T00:00:00.000Z"),
  );
  assert.equal(
    earliestBedSourceDeadline(rows, 12),
    "2026-09-04T12:00:00.000Z",
  );
  assert.equal(inspectBedSourceDeadline(rows, 12).fingerprint.length, 16);
  assert.notEqual(
    inspectBedSourceDeadline(rows, 12).fingerprint,
    inspectBedSourceDeadline([
      { ...rows[0], API기준시각: "20260904090000.0" },
      { ...rows[2], API기준시각: "20260904093000.0" },
    ], 12).fingerprint,
  );
  assert.equal(koreanBedSourceTimestampMillis("20260230090000"), null);
});

test("beds wins when both scheduled modes are due", () => {
  const status = {
    schedulerStartedAt: minutesAgo(2_000),
    lastBedsSuccessAt: minutesAgo(500),
    lastFullSuccessAt: minutesAgo(25 * 60),
  };

  assert.equal(decideScheduledMode({ status, now: NOW }), "beds");
});

test("beds retry is capped by source TTL, dashboard staleness, interval, and safety lead", () => {
  assert.equal(bedsRetryCeilingMinutes(), 45);
  assert.equal(boundedBedsFailureRetryMinutes(), 45);
  assert.equal(
    boundedBedsFailureRetryMinutes({ bedsFailureRetryMinutes: 480 }),
    45,
  );
  assert.equal(
    boundedBedsFailureRetryMinutes({ bedsFailureRetryMinutes: 30 }),
    30,
  );
});

test("full failures cannot repeat faster than their maintenance cadence", () => {
  assert.equal(boundedFullFailureRetryMinutes({
    configuredMinutes: 60,
    fullIntervalHours: 24,
  }), 1440);
  assert.equal(boundedFullFailureRetryMinutes({
    configuredMinutes: 60,
    fullIntervalHours: 6,
  }), 360);
});

test("invalid freshness budget is rejected instead of silently allowing late retries", () => {
  assert.throws(
    () => bedsRetryCeilingMinutes({
      bedSourceMaxAgeHours: 10,
      fastIntervalMinutes: 540,
      bedRefreshSafetyLeadMinutes: 60,
    }),
    /leave at least one minute/,
  );
});

test("actual bed deadline makes beds due before the regular interval", () => {
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: minutesAgo(60),
    lastFullSuccessAt: minutesAgo(60),
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(30),
  });

  assert.equal(state.due, true);
  assert.equal(state.ready, true);
  assert.equal(state.intervalDueAt, NOW + 420 * 60_000);
  assert.equal(state.deadlineDueAt, NOW - 45 * 60_000);
  assert.equal(decideScheduledMode({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(30),
  }), "beds");
});

test("a bed deadline outside the safety lead does not force an early refresh", () => {
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: minutesAgo(60),
    lastFullSuccessAt: minutesAgo(60),
  };

  assert.equal(decideScheduledMode({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(76),
  }), null);
});

test("a successful refresh consumes an unchanged near-expiry source deadline", () => {
  const deadlineAt = minutesFromNow(30);
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsAttemptAt: minutesAgo(5),
    lastBedsSuccessAt: new Date(NOW).toISOString(),
    lastBedsAttemptedDeadlineAt: deadlineAt,
    lastBedsAttemptedDeadlineFingerprint: "same-source",
    lastFullSuccessAt: minutesAgo(60),
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: deadlineAt,
    bedDeadlineFingerprint: "same-source",
  });

  assert.equal(state.due, false);
  assert.equal(state.deadlineConsumed, true);
  assert.equal(state.nextAttemptAt, NOW + 15 * 60_000);
});

test("an early manual or interval refresh does not consume a later deadline", () => {
  const deadlineAt = minutesFromNow(180);
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsAttemptAt: minutesAgo(5),
    lastBedsSuccessAt: new Date(NOW).toISOString(),
    lastBedsAttemptedDeadlineAt: deadlineAt,
    lastBedsAttemptedDeadlineFingerprint: "same-source",
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: deadlineAt,
    bedDeadlineFingerprint: "same-source",
  });

  assert.equal(state.deadlineConsumed, false);
  assert.equal(state.nextAttemptAt, NOW + 105 * 60_000);
});

test("small deadline movement is one consumed observation rather than an API loop", () => {
  const attemptedDeadline = minutesFromNow(30);
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsAttemptAt: minutesAgo(5),
    lastBedsSuccessAt: new Date(NOW).toISOString(),
    lastBedsAttemptedDeadlineAt: attemptedDeadline,
    lastBedsAttemptedDeadlineFingerprint: "same-source",
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(30.1),
    bedDeadlineFingerprint: "same-source",
  });

  assert.equal(state.deadlineConsumed, true);
  assert.equal(state.due, false);
  assert.equal(state.nextAttemptAt, NOW + 15 * 60_000);
});

test("repeated stalled source observations use bounded exponential backoff", () => {
  const deadlineAt = minutesFromNow(30);
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsAttemptAt: minutesAgo(5),
    lastBedsSuccessAt: new Date(NOW).toISOString(),
    lastBedsAttemptedDeadlineAt: deadlineAt,
    lastBedsAttemptedDeadlineFingerprint: "same-source",
    lastBedsDeadlineStallCount: 4,
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: deadlineAt,
    bedDeadlineFingerprint: "same-source",
  });

  assert.equal(state.stalledSourceRetryMinutes, 120);
  assert.equal(state.nextAttemptAt, NOW + 120 * 60_000);
});

test("a new stalled source set resets exponential backoff", () => {
  assert.equal(nextBedDeadlineStallCount({
    stalled: true,
    previousCount: 5,
    previousFingerprint: "old-source",
    currentFingerprint: "new-source",
  }), 1);
  assert.equal(nextBedDeadlineStallCount({
    stalled: true,
    previousCount: 5,
    previousFingerprint: "same-source",
    currentFingerprint: "same-source",
  }), 6);
});

test("an unconsumed source deadline inside five minutes triggers beds immediately", () => {
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: new Date(NOW).toISOString(),
  };

  assert.equal(decideScheduledMode({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(5),
  }), "beds");
});

test("a deadline-driven failure retries with time left to finish before expiry", () => {
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: minutesAgo(500),
    lastBedsFailureAt: new Date(NOW).toISOString(),
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(90),
  });

  assert.equal(state.failureRetryMinutes, 45);
  assert.equal(state.nextAttemptAt, NOW + 45 * 60_000);
  assert.equal(decideScheduledMode({
    status,
    now: NOW + 45 * 60_000,
    bedDeadlineAt: minutesFromNow(90),
  }), "beds");
});

test("expired or unknown deadlines retain a minimum failure backoff", () => {
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: minutesAgo(500),
    lastBedsAttemptAt: new Date(NOW).toISOString(),
    lastBedsFailureAt: new Date(NOW).toISOString(),
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: "1970-01-01T00:00:00.000Z",
  });

  assert.equal(state.failureRetryMinutes, 15);
  assert.equal(state.ready, false);
  assert.equal(decideScheduledMode({
    status,
    now: NOW + 15 * 60_000,
    bedDeadlineAt: "1970-01-01T00:00:00.000Z",
  }), "beds");
});

test("an earlier replacement deadline is not hidden by forward tolerance", () => {
  const attemptedDeadline = minutesFromNow(30);
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsAttemptAt: minutesAgo(5),
    lastBedsSuccessAt: new Date(NOW).toISOString(),
    lastBedsAttemptedDeadlineAt: attemptedDeadline,
    lastBedsAttemptedDeadlineFingerprint: "old-source",
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: minutesFromNow(29),
    bedDeadlineFingerprint: "new-source",
  });

  assert.equal(state.deadlineConsumed, false);
  assert.equal(state.due, true);
});

test("beds cooldown starts at attempt time so a slow failure does not consume freshness budget twice", () => {
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: minutesAgo(500),
    lastBedsAttemptAt: minutesAgo(20),
    lastBedsFailureAt: new Date(NOW).toISOString(),
  };
  const state = bedScheduleState({ status, now: NOW });

  assert.equal(state.failureRetryDueAt, NOW + 25 * 60_000);
});

test("deadline retry leaves one beds timeout plus promotion margin before expiry", () => {
  const deadlineAt = minutesFromNow(45);
  const status = {
    schedulerStartedAt: minutesAgo(1_000),
    lastBedsSuccessAt: minutesAgo(500),
    lastBedsAttemptAt: minutesAgo(30),
    lastBedsFailureAt: new Date(NOW).toISOString(),
  };
  const state = bedScheduleState({
    status,
    now: NOW,
    bedDeadlineAt: deadlineAt,
  });
  const retryFinishesAt = state.failureRetryDueAt + 30 * 60_000;

  assert.equal(state.failureRetryDueAt, NOW + 5 * 60_000);
  assert.ok(retryFinishesAt <= Date.parse(deadlineAt) - 10 * 60_000);
});

test("full does not start when beds will need the only worker soon", () => {
  const status = {
    schedulerStartedAt: minutesAgo(2_000),
    lastBedsSuccessAt: minutesAgo(479),
    lastFullSuccessAt: minutesAgo(25 * 60),
  };

  assert.equal(decideScheduledMode({ status, now: NOW }), null);
  assert.equal(decideScheduledMode({
    status: { ...status, lastBedsSuccessAt: minutesAgo(300) },
    now: NOW,
  }), "full");
});

test("full and beds retry cooldowns are independent", () => {
  const config = { fullFailureRetryMinutes: 60 };
  const fullFailedRecently = {
    lastFullFailureAt: minutesAgo(30),
    lastBedsFailureAt: minutesAgo(180),
  };
  assert.equal(retryCooldownElapsed("full", fullFailedRecently, NOW, config), false);
  assert.equal(retryCooldownElapsed("beds", fullFailedRecently, NOW, config), true);

  const bedsFailedRecently = {
    lastFullFailureAt: minutesAgo(180),
    lastBedsFailureAt: minutesAgo(30),
  };
  assert.equal(retryCooldownElapsed("full", bedsFailedRecently, NOW, config), true);
  assert.equal(retryCooldownElapsed("beds", bedsFailedRecently, NOW, config), false);
});

test("legacy failure timestamp applies only to its recorded mode", () => {
  const status = {
    mode: "full",
    lastFailureAt: minutesAgo(30),
  };

  const config = { fullFailureRetryMinutes: 60 };
  assert.equal(retryCooldownElapsed("full", status, NOW, config), false);
  assert.equal(retryCooldownElapsed("beds", status, NOW, config), true);
});

test("persisted failure mode is not reassigned when a later job changes status.mode", () => {
  const status = {
    mode: "beds",
    lastFailureMode: "full",
    lastFailureAt: minutesAgo(30),
  };
  const config = { fullFailureRetryMinutes: 60 };

  assert.equal(retryCooldownElapsed("full", status, NOW, config), false);
  assert.equal(retryCooldownElapsed("beds", status, NOW, config), true);
});

test("a later success clears an older mode failure for cooldown decisions", () => {
  const status = {
    lastBedsFailureAt: minutesAgo(30),
    lastBedsSuccessAt: minutesAgo(5),
  };

  assert.equal(retryCooldownElapsed("beds", status, NOW), true);
});

test("a due beds job reserves the worker while its bounded cooldown is active", () => {
  const status = {
    schedulerStartedAt: minutesAgo(2_000),
    lastBedsSuccessAt: minutesAgo(600),
    lastFullSuccessAt: minutesAgo(25 * 60),
    lastBedsFailureAt: minutesAgo(44),
  };

  assert.equal(decideScheduledMode({ status, now: NOW }), null);
  assert.equal(
    decideScheduledMode({ status, now: NOW + 60_000 }),
    "beds",
  );
});

test("a partial bed refresh is retried before maintenance full work", () => {
  const status = {
    schedulerStartedAt: minutesAgo(2_000),
    lastBedsSuccessAt: minutesAgo(10),
    lastFullSuccessAt: minutesAgo(25 * 60),
    lastBedsFailedRegionCount: 2,
    lastBedsPartialFailureAt: minutesAgo(44),
  };

  assert.equal(decideScheduledMode({ status, now: NOW }), null);
  assert.equal(decideScheduledMode({ status, now: NOW + 60_000 }), "beds");
});

test("pending beds work cannot be overwritten by full work", () => {
  const pendingBeds = { mode: "beds", trigger: "schedule" };
  const candidateFull = { mode: "full", trigger: "manual" };

  assert.deepEqual(
    mergePendingJob(pendingBeds, candidateFull),
    pendingBeds,
  );
  assert.deepEqual(
    mergePendingJob(candidateFull, pendingBeds),
    pendingBeds,
  );
});

test("mergePendingJob does not mutate either input", () => {
  const pending = Object.freeze({ mode: "full", trigger: "schedule" });
  const candidate = Object.freeze({ mode: "beds", trigger: "deadline" });
  const merged = mergePendingJob(pending, candidate);

  assert.deepEqual(merged, candidate);
  assert.notEqual(merged, candidate);
});
