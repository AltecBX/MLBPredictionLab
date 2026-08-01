import { chromium } from "@playwright/test";
const out = "/tmp/claude-0/-home-user-MLBPredictionLab/a8e72b9b-ad96-55d6-87c3-a9d6297eac91/scratchpad";
const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const shots = process.argv.slice(2).map((s) => JSON.parse(s));
for (const { name, url, w, h, mobile, dark, full } of shots) {
  const ctx = await b.newContext({
    viewport: { width: w, height: h }, isMobile: !!mobile, hasTouch: !!mobile,
    deviceScaleFactor: 2, colorScheme: dark ? "dark" : "light",
  });
  const p = await ctx.newPage();
  await p.goto("http://127.0.0.1:3000" + url, { waitUntil: "networkidle" });
  await p.waitForTimeout(700);
  await p.screenshot({ path: `${out}/${name}.png`, fullPage: !!full });
  await ctx.close();
}
await b.close();
console.log("done");
