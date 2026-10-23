import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.PROJECTOR_SERVER ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": target,
      "/files": target,
      "/download": target,
      "/ws": { target: target.replace("http", "ws"), ws: true },
    },
  },
});
