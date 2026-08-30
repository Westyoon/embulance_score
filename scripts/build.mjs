import { spawnSync } from "node:child_process";
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
process.exit(result.status ?? 1);
