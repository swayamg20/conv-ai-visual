import assert from "node:assert/strict";
import http from "node:http";
import { createRequire } from "node:module";
import { test } from "node:test";
import { installStreamReadObserver } from "./probe_stream_observer.mjs";

const { chromium } = createRequire(new URL("../../web/package.json", import.meta.url))("playwright");

test("captures consumed SSE despite cancellation without another request or reader", async () => {
  let requests = 0;
  let closed = false;
  const payload = 'data: {"type":"completed","text":"30°"}\n\n';
  const server = http.createServer((request, response) => {
    if (request.url === "/chat") {
      requests += 1;
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write(payload);
      response.on("close", () => { closed = true; });
      return;
    }
    response.writeHead(200, { "Content-Type": "text/html" });
    response.end("<html><body>Offline observation regression</body></html>");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch();
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const page = await browser.newPage();
    await page.addInitScript(installStreamReadObserver, origin);
    await page.goto(origin);
    const bodyUnavailable = page.waitForResponse((response) => response.url().endsWith("/chat"))
      .then((response) => response.text().then(() => false).catch(() => true));
    const received = await page.evaluate(async () => {
      const response = await fetch("/chat", { method: "POST" });
      const reader = response.body.getReader();
      const result = await reader.read();
      await reader.cancel();
      reader.releaseLock();
      return { text: new TextDecoder().decode(result.value), observations: globalThis.__murmurProbeStreams };
    });
    assert.equal(received.text, payload);
    assert.deepEqual(received.observations, [{ path: "/chat", status: 200, body: payload, truncated: false }]);
    assert.equal(await bodyUnavailable, true, "Reproduce why response.text is not a reliable observer.");
    assert.equal(requests, 1);
    assert.equal(closed, true, "Observation must not keep cancelled provider streams alive.");
    const unrelated = await page.evaluate(async () => {
      await (await fetch("/unobserved")).text();
      return globalThis.__murmurProbeStreams.length;
    });
    assert.equal(unrelated, 1, "Unrelated response bodies must not be collected.");
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
