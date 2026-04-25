import { rmSync } from "node:fs";

const ua = process.env.npm_config_user_agent ?? "";
if (!ua.startsWith("pnpm/")) {
  console.error("Use pnpm instead");
  process.exit(1);
}

rmSync("package-lock.json", { force: true });
rmSync("yarn.lock", { force: true });
