import path from "node:path";
import { defineConfig } from "vitest/config";

// Frontend unit-test config (AGENTS.md §29 target design — no test runner
// existed for `lib/adf/*` and friends before this; the parity test in
// `worker/tests/test_generator_parity.py` only ever runs the reference
// generator through a one-shot script harness, never a real test suite).
//
// `jsdom` (not `node`) so component/wiring tests (`*.test.tsx`, e.g.
// `components/shell/app-shell.test.tsx`) can render real components — plain
// `lib/*.test.ts` logic tests are unaffected by the extra DOM globals.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  // tsconfig.json sets `"jsx": "preserve"` (Next's own SWC compiler handles the
  // actual transform in dev/build); esbuild doesn't read that value, and its
  // own default is the classic `React.createElement` transform, which every
  // component here is written against instead (no `import React` anywhere,
  // React 17+ automatic runtime) — pin it explicitly so vitest compiles .tsx
  // the same way Next does.
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
