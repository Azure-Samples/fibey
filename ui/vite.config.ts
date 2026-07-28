import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // Use 127.0.0.1 (not "localhost") so the proxy always targets the
        // IPv4 gateway. On macOS "localhost" can resolve to IPv6 ::1 first,
        // where an unrelated process (e.g. a Docker container publishing
        // [::]:8080) may answer and return spurious 400s.
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
});
