import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";

/**
 * Load the repo's single runtime-parameter file (config/platform.env, or the
 * file PLATFORM_ENV_FILE points at) into process.env, so `npm run dev` /
 * `npm run build` see the same values the api and worker read. Variables
 * already set in the real environment always win — docker compose, Container
 * Apps and Helm inject them that way, and the file is simply absent there.
 */
function loadPlatformEnv(): void {
  const file = process.env.PLATFORM_ENV_FILE || path.resolve(__dirname, "..", "config", "platform.env");
  if (!existsSync(file)) return;
  for (const raw of readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim().replace(/^(["'])(.*)\1$/, "$2");
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

loadPlatformEnv();

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
};

export default nextConfig;
