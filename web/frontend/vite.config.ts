import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Served by FastAPI at /app (web/server.py mounts dist/ there), so every asset URL
// must be rooted at /app/. In dev, Vite serves on :5173 and proxies the API to the
// uvicorn server on :8000 — streamed responses included, since the proxy is http-proxy.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
