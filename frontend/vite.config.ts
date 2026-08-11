import { defineConfig } from "vite";

// No React plugin on purpose: Vite's esbuild transpiles .tsx via tsconfig's
// "jsx": "react-jsx". Same setup as DUT_browser — one less dependency, and the
// build output is what ships to the Pi (SPEC D3: dist/ is committed).
export default defineConfig({
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
