import fs from "node:fs";

/** Remove only the lock proven to belong to a child process that has exited. */
export function clearOwnedPipelineLock(filename, expectedPid) {
  let owner;
  try {
    owner = fs.readFileSync(filename, "utf8").trim();
  } catch (error) {
    if (error?.code === "ENOENT") return { cleared: false, reason: "missing" };
    throw error;
  }

  if (!owner || owner !== String(expectedPid)) {
    return { cleared: false, reason: "owner-mismatch" };
  }
  fs.rmSync(filename);
  return { cleared: true, reason: "owned" };
}
