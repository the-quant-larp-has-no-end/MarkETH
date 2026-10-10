import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // fileURLToPath, not URL.pathname: a path with a space must stay a path.
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // The tests beside the source and nothing else. The specs of e2e/ are Playwright's:
    // they need a running stack and a browser (`npm run e2e`).
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: [...configDefaults.exclude, "e2e/**"],
    restoreMocks: true,
  },
});
