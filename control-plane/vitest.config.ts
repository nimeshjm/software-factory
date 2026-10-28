import { defineConfig } from "vitest/config";

// Pure modules are tested under plain node (WebCrypto is global in Node 20+).
export default defineConfig({
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
