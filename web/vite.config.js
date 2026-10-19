import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 转给本地 Python 服务（python -m tools.serve）
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8790" } },
});
