import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": api,
      "/demo": api,
      "/healthz": api,
      "/cases": api,
      "/webhooks": api,
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 2000 },
});
