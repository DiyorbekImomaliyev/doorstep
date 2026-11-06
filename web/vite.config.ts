import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const api = "http://localhost:8000";

export default defineConfig(({ mode }) => {
  // AG Grid Enterprise and AG Studio licences: from the repo-root .env locally, from the Render env
  // group at build time. Never committed (CLAUDE.md rule 10).
  // loadEnv reads ../.env and lets real environment variables (Render) win.
  const env = loadEnv(mode, "..", "AG_");
  return {
    plugins: [react()],
    define: {
      __AG_GRID_LICENSE_KEY__: JSON.stringify(env.AG_GRID_LICENSE_KEY ?? ""),
      __AG_STUDIO_LICENSE_KEY__: JSON.stringify(env.AG_STUDIO_LICENSE_KEY ?? ""),
    },
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
  };
});
