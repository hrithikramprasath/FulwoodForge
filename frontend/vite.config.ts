import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // Vite ignores $PORT by default, which makes it collide on 5173 when a second
  // copy of the app runs from a git worktree. Honouring it lets both run at once.
  server: { port: Number(process.env.PORT) || 5173 },
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "vendor-react",
              test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/,
            },
            { name: "vendor-radix", test: /node_modules[\\/]@radix-ui[\\/]/ },
            {
              name: "vendor-render",
              test: /node_modules[\\/](katex|marked|dompurify)[\\/]/,
            },
            {
              name: "vendor-forms",
              test: /node_modules[\\/](react-hook-form|@hookform|zod)[\\/]/,
            },
          ],
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
});
