import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Backend API base URL for the dev proxy.
const API_TARGET = process.env.API_TARGET ?? "http://127.0.0.1:8000";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        // Strip the /api prefix; the backend serves routes directly (/health, /filings, ...)
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
