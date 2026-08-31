import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

// 사용자의 셸에 NODE_ENV=development가 전역 설정돼 있어도 배포 빌드는 항상
// Next.js가 요구하는 production 모드로 실행한다. 나머지 배포 변수는 그대로 전달한다.
const nextCli = path.join(process.cwd(), "node_modules", "next", "dist", "bin", "next");
const result = spawnSync(process.execPath, [nextCli, "build"], {
  cwd: process.cwd(),
  env: { ...process.env, NODE_ENV: "production" },
  stdio: "inherit",
});

if (result.error) throw result.error;
const status = result.status ?? 1;
if (status === 0) {
  const standaloneRoot = path.join(process.cwd(), ".next", "standalone");
  if (fs.existsSync(standaloneRoot)) {
    for (const entry of fs.readdirSync(standaloneRoot)) {
      if (entry === ".env" || entry.startsWith(".env.")) {
        fs.rmSync(path.join(standaloneRoot, entry), { force: true });
      }
    }
    fs.cpSync(
      path.join(process.cwd(), ".next", "static"),
      path.join(standaloneRoot, ".next", "static"),
      { recursive: true, force: true },
    );
    const publicRoot = path.join(process.cwd(), "public");
    if (fs.existsSync(publicRoot)) {
      fs.cpSync(publicRoot, path.join(standaloneRoot, "public"), {
        recursive: true,
        force: true,
      });
    }
  }
}
process.exit(status);
