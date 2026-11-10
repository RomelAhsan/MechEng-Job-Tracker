/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The site is served from the project subpath on GitHub Pages
// (https://RomelAhsan.github.io/MechEng-Job-Tracker/), so every asset and the
// data fetch must resolve under that base. A relative fetch of
// `data/jobs.json` therefore hits the right place.
export default defineConfig({
  base: "/MechEng-Job-Tracker/",
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
