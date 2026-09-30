import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Assets are served under /app/ by FastAPI. Getting this wrong is the
  // classic blank page with 404s for every asset.
  base: "/app/",
  build: { outDir: "../jobfit/server/static/app", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
