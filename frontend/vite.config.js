import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/aml": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
