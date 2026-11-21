// CECL Lifetime Loss Lab site: served at finance.martinsogiekpolor.com/cecl (site/PLAN.md).
// Static output. Pages read their numbers from /cecl/data/*.json at runtime, so a quarterly data
// refresh never needs a rebuild (the data can be served from R2 by the sector router, as for Bank Health).
import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://finance.martinsogiekpolor.com",
  base: "/cecl",
  output: "static",
  trailingSlash: "ignore",
  build: { format: "directory" },
  // No analytics, no tracking, no cookies: nothing to configure here on purpose.
  // (Astro's own telemetry is switched off with ASTRO_TELEMETRY_DISABLED=1 in every build.)
  devToolbar: { enabled: false },
});
