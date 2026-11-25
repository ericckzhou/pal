import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // assetsInlineLimit 0: small fonts would otherwise become data: URIs, which the CSP (font-src 'self') blocks.
  build: { outDir: "dist", emptyOutDir: true, modulePreload: { polyfill: false }, assetsInlineLimit: 0 },
});
