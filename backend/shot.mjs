import { chromium } from "@playwright/test";
const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const shots = [
  ["/?date=2026-08-01&sort=win_probability", "game-center", 1280, 1500],
  ["/backtest", "backtest", 1280, 1700],
];
for (const [url, name, w, h] of shots) {
  for (const theme of ["light", "dark"]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    await p.addInitScript((t) => localStorage.setItem("jerry-theme", t), theme);
    await p.goto("http://127.0.0.1:3100" + url, { waitUntil: "networkidle" });
    await p.screenshot({ path: `/tmp/${name}-${theme}.png`, fullPage: false });
    await ctx.close();
  }
}
// Game detail + mobile
const gid = process.argv[2];
const ctx = await b.newContext({ viewport: { width: 1280, height: 1500 } });
const p = await ctx.newPage();
await p.goto(`http://127.0.0.1:3100/game/${gid}`, { waitUntil: "networkidle" });
await p.screenshot({ path: "/tmp/game-detail.png" });
await p.goto(`http://127.0.0.1:3100/game/${gid}?tab=explanation`, { waitUntil: "networkidle" });
await p.screenshot({ path: "/tmp/game-explanation.png" });
const m = await b.newContext({ viewport: { width: 390, height: 1000 }, isMobile: true, hasTouch: true });
const mp = await m.newPage();
await mp.goto("http://127.0.0.1:3100/?date=2026-08-01", { waitUntil: "networkidle" });
await mp.screenshot({ path: "/tmp/mobile.png" });
await b.close();
console.log("done");
