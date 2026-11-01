const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "radar", "templates", "birlesik.html"));
const now = Date.now();
const data = { generated_at: now, btc: { price: 65000, chg24: 0.2 }, coins: [],
  feeds: { okx: { healthy: false, last_msg_age_s: null, reconnects: 0 } }, karne: null };
const unified = { mod: "unified", ts: now, bakiye: 100, baslangic: 100, gun_basi: 100,
  pozisyonlar: [], kapananlar: [], hatalar: [],
  tanilama: { hazir: true, ts: now, ws_healthy: false, tick_count: 0, feed_symbols: 0,
    decision_coins: 0, book_entries: 0, spread_durum_sayim: {}, ornek: [] } };
const polat = { source: "polat", scope: "research_only", status: "fresh", reason: null,
  last_sequence: 100, sequence: 101, generated_at_ms: now - 200, received_at_ms: now,
  age_ms: 200, observation_count: 2, source_run_id: "mobile-test-run" };
const settings = { schema_version: 3, mode: "read_only_discovery", write_enabled: false,
  write_ready_count: 0, parameter_count: 1,
  parameters: [{ path: "risk.max_spread_bp", label: "Max spread bp", category: "risk",
    value_type: "integer", value: 15, protected: false, description: "Test",
    schema: { type: "integer", minimum: 1, maximum: 50, unit: "bp" } }] };

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  res.setHeader("Cache-Control", "no-store");
  if (url.pathname === "/" || url.pathname === "/birlesik.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }); res.end(html); return;
  }
  const fixtures = { "/data.json": data, "/birlesik/durum.json": unified,
    "/birlesik/polat_kopru.json": polat, "/birlesik/ayarlar.json": settings };
  if (Object.prototype.hasOwnProperty.call(fixtures, url.pathname)) {
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(fixtures[url.pathname])); return;
  }
  if (/kararlar-\d{4}-\d{2}-\d{2}\.jsonl$/.test(url.pathname)) { res.writeHead(404); res.end(""); return; }
  res.writeHead(404); res.end("not found");
});

async function main() {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [360, 390, 430]) {
      const page = await browser.newPage({ viewport: { width, height: 844 }, deviceScaleFactor: 1 });
      await page.addInitScript(() => {
        class BlockedWebSocket {
          constructor(url) { this.url = url; this.readyState = 3;
            setTimeout(() => { if (this.onclose) this.onclose({ code: 1000, reason: "browser-test" }); }, 0); }
          send() {}
          close() { this.readyState = 3; }
        }
        window.WebSocket = BlockedWebSocket;
      });
      await page.goto("http://127.0.0.1:" + address.port + "/", { waitUntil: "domcontentloaded" });
      await page.waitForFunction(() => document.querySelectorAll("#tabs .tab").length >= 7);
      await page.waitForTimeout(100);
      for (const tab of ["veri", "ayarlar", "sistem", "portfoy"]) {
        await page.locator('#tabs .tab[data-t="' + tab + '"]').click();
        await page.waitForTimeout(30);
        const metrics = await page.evaluate(() => ({
          viewport: window.innerWidth, document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth, app: document.querySelector(".app")?.getBoundingClientRect().width,
          view: document.querySelector("#view")?.getBoundingClientRect().width,
          viewScroll: document.querySelector("#view")?.scrollWidth,
          tabStrip: document.querySelector("#tabs")?.getBoundingClientRect().width
        }));
        assert.ok(metrics.document <= width + 1, "document overflow at " + width + "px / " + tab + ": " + JSON.stringify(metrics));
        assert.ok(metrics.body <= width + 1, "body overflow at " + width + "px / " + tab + ": " + JSON.stringify(metrics));
        assert.ok(metrics.app <= width + 1, "app exceeds viewport at " + width + "px / " + tab + ": " + JSON.stringify(metrics));
        assert.ok(metrics.view <= width + 1, "view exceeds viewport at " + width + "px / " + tab + ": " + JSON.stringify(metrics));
        assert.ok(metrics.tabStrip <= width + 1, "tab strip exceeds viewport at " + width + "px / " + tab + ": " + JSON.stringify(metrics));
        if (tab === "veri") {
          assert.match(await page.locator("#view").innerText(), /Polat → Unified/);
          assert.match(await page.locator("#view").innerText(), /araştırma\/teşhis telemetrisi/);
        }
      }
      await page.close();
    }
    process.stdout.write("PASS: 360px, 390px, 430px dashboard layout and tab interactions\n");
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
