// `vitest/config` rather than `vite` so the `test` block below type-checks.
import { defineConfig } from "vitest/config";

// No React plugin on purpose: Vite's esbuild transpiles .tsx via tsconfig's
// "jsx": "react-jsx". Same setup as DUT_browser — one less dependency, and the
// build output is what ships to the Pi (SPEC D3: dist/ is committed).
export default defineConfig({
  // Node environment on purpose: these tests cover the pure state-derivation
  // functions (delta folding, history upserts, KPI arithmetic), not rendering.
  // No jsdom / testing-library, so the dev toolchain stays one package heavier
  // and nothing new ships to the Pi — dist/ is prebuilt.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/ws": {
        target: "ws://127.0.0.1:8080",
        ws: true,
      },
    },
  },
});
