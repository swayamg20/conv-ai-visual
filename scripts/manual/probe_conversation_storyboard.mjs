#!/usr/bin/env node
/**
 * One explicitly requested live conversation-to-storyboard acceptance run.
 * Uses a fresh Firebase test identity, the real deployed UI/APIs, and no mocks.
 * Allows one chat submission and one Director submission, with no probe retries.
 * Auth tokens stay in process memory; artifacts contain only test data.
 *
 * node scripts/manual/probe_conversation_storyboard.mjs --run-live \
 *   --backend-env /private/backend.env --frontend-env /private/frontend.env \
 *   --web-url https://... --api-url https://... --release-sha <40-char-sha> \
 *   --output var/conversation-storyboard-live
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "web/package.json"));
const { chromium } = require("playwright");
const { values } = parseArgs({ options: {
  "run-live": { type: "boolean" },
  "backend-env": { type: "string" },
  "frontend-env": { type: "string" },
  "web-url": { type: "string" },
  "api-url": { type: "string" },
  "release-sha": { type: "string" },
  output: { type: "string" },
} });
assert(values["run-live"], "Live model calls require --run-live and prior user authorization.");
for (const key of ["backend-env", "frontend-env", "web-url", "api-url", "release-sha", "output"]) {
  assert(values[key], `Missing --${key}`);
}
assert(/^[0-9a-f]{40}$/.test(values["release-sha"]), "Expected an exact release SHA.");
const webUrl = new URL(values["web-url"]).origin;
const apiUrl = new URL(values["api-url"]).origin;
assert(webUrl.startsWith("https://") && apiUrl.startsWith("https://"), "Live URLs require HTTPS.");
const output = resolve(values.output);
await mkdir(output, { recursive: true, mode: 0o700 });
const report = {
  startedAt: new Date().toISOString(), releaseSha: values["release-sha"], webUrl, apiUrl,
  prompt: "Explain projectile motion visually by comparing launches at 30 degrees and 60 degrees with the same speed of 20 m/s and no air resistance. Trace both flights, then show why their landing ranges are equal.",
  requests: { chat: 0, reflex: 0, director: 0 }, streams: [], blockedRequests: [],
  cleanup: {}, result: "incomplete",
  testUid: `storyboard-acceptance-${randomUUID()}`,
};
const progress = (stage) => console.log(JSON.stringify({ stage, requests: report.requests }));
async function requestJson(url, options = {}) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(20_000) });
  assert(response.ok, `HTTP ${response.status} at ${new URL(url).pathname}`);
  return response.json();
}
for (const [origin, path] of [[webUrl, "/healthz"], [apiUrl, "/readyz"]]) {
  const health = await requestJson(`${origin}${path}`);
  assert.equal(health.release_sha, report.releaseSha, "Deployed release changed.");
}

// The signer reads only the existing deployment credentials. Its stdout is
// captured privately; it never enters the report or terminal output.
const prepareIdentity = String.raw`
import json, sys
from pathlib import Path
import firebase_admin
from firebase_admin import auth, credentials
from dotenv import dotenv_values
import httpx
backend_path, frontend_path = map(Path, sys.argv[1:3])
backend = dotenv_values(backend_path, interpolate=False)
frontend = dotenv_values(frontend_path, interpolate=False)
key = frontend["NEXT_PUBLIC_FIREBASE_API_KEY"]
assert backend["FIREBASE_PROJECT_ID"] == frontend["NEXT_PUBLIC_FIREBASE_PROJECT_ID"]
path = Path(backend["FIREBASE_RUNTIME_SERVICE_ACCOUNT_PATH"]).expanduser()
if not path.is_absolute():
    path = backend_path.parent / path
assert path.is_file() and path.stat().st_mode & 0o077 == 0
app = firebase_admin.initialize_app(credentials.Certificate(str(path)))
uid = sys.argv[3]
email = uid + "@murmur.invalid"
token = auth.create_custom_token(uid, {"email": email, "email_verified": True, "name": "Storyboard acceptance"}, app)
with httpx.Client(timeout=20) as client:
    response = client.post("https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken", params={"key": key}, json={"token": token.decode(), "returnSecureToken": True})
    if response.status_code != 200:
        import re
        reason = response.json().get("error", {}).get("message", "UNKNOWN").split(":", 1)[0]
        safe_reason = reason if re.fullmatch(r"[A-Z_ ]+", reason) else "UNKNOWN"
        raise SystemExit("Firebase test sign-in failed: HTTP " + str(response.status_code) + " " + safe_reason)
    data = response.json()
print(json.dumps({"uid": uid, "apiKey": key, "idToken": data["idToken"], "refreshToken": data["refreshToken"], "expiresIn": data["expiresIn"]}))
`;
let identity;
let browser;
let page;
let agent;
const pendingResponses = [];
const headers = () => ({ Authorization: `Bearer ${identity.idToken}`, "Content-Type": "application/json" });
try {
  progress("health_verified");
  try {
    identity = JSON.parse(execFileSync(resolve(root, ".venv/bin/python"), [
      "-c", prepareIdentity, resolve(values["backend-env"]), resolve(values["frontend-env"]), report.testUid,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 40_000 }));
  } catch (error) {
    const failure = String(error.stderr ?? "").trim().split("\n").at(-1) ?? "";
    const exceptionType = failure.match(/^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):/)?.[1];
    report.identityFailure = /^Firebase test sign-in failed: HTTP \d{3} [A-Z_ ]+$/.test(failure)
      ? failure : `Local credential preparation failed (${exceptionType ?? error.code ?? "unknown"}).`;
    throw new Error(`${report.identityFailure} No model calls made.`);
  }
  const me = await requestJson(`${apiUrl}/api/auth/me`, { headers: headers() });
  assert.equal(me.user.id, identity.uid, "Test identity must not link to another user.");
  progress("firebase_auth_verified");
  agent = await requestJson(`${apiUrl}/api/agents`, {
    method: "POST", headers: headers(), body: JSON.stringify({
      name: "Storyboard release acceptance", description: "Isolated deployment verification",
      persona: { subject: "Physics", name: "Newton" }, capabilities: ["canvas"],
    }),
  });
  report.agentId = agent.id;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1512, height: 982 }, reducedMotion: "no-preference" });
  // Seed a real, newly exchanged token in Firebase's normal persistence format.
  // Firebase accounts:lookup and every Murmur API request remain live.
  await context.addInitScript(({ origin, user }) => {
    if (location.origin === origin) {
      localStorage.setItem(`firebase:authUser:${user.apiKey}:[DEFAULT]`, JSON.stringify(user));
    }
  }, { origin: webUrl, user: {
    uid: identity.uid, email: null, emailVerified: false, isAnonymous: false, providerData: [],
    stsTokenManager: { accessToken: identity.idToken, refreshToken: identity.refreshToken,
      expirationTime: Date.now() + Number(identity.expiresIn) * 1000 },
    apiKey: identity.apiKey, appName: "[DEFAULT]",
  } });
  await context.route(`${apiUrl}/**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "POST" && request.method() !== "DELETE") return route.continue();
    let lane;
    if (path === "/chat") lane = "chat";
    if (path.endsWith("/storyboard/stream")) lane = request.postDataJSON().routingMode;
    if (lane && Object.hasOwn(report.requests, lane)) {
      report.requests[lane] += 1;
      if (report.requests[lane] === 1) return route.continue();
    } else if (path === "/api/sessions" && request.method() === "POST") {
      return route.continue();
    }
    // Teardown summaries, automatic retries and extra turns can spend money.
    report.blockedRequests.push({ method: request.method(), path });
    return route.abort("blockedbyclient");
  });
  page = await context.newPage();
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin !== apiUrl) return;
    if (url.pathname === "/api/sessions" && response.request().method() === "POST") {
      pendingResponses.push(response.json().then((body) => { report.sessionId = body.id; }).catch(() => {}));
    }
    if (url.pathname !== "/chat" && !url.pathname.endsWith("/storyboard/stream")) return;
    pendingResponses.push((async () => {
      const observation = { path: url.pathname, status: response.status(), events: [] };
      report.streams.push(observation);
      try {
        const body = await response.text();
        for (const line of body.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const event = JSON.parse(line.slice(6));
          observation.events.push(event.type);
          if (event.type === "storyboard_command") report.command = event.command;
          if (event.type.endsWith("_completed")) observation.completion = event;
          if (event.type.endsWith("_failed") || event.type === "error") observation.error = event;
        }
      } catch { observation.readFailed = true; }
    })());
  });
  await page.goto(`${webUrl}/session/${agent.id}`, { waitUntil: "domcontentloaded" });
  const input = page.getByPlaceholder("Type your message...");
  await input.waitFor({ state: "visible", timeout: 30_000 });
  await input.fill(report.prompt);
  report.submittedAt = new Date().toISOString();
  await input.press("Enter");
  progress("one_chat_turn_submitted");
  const board = page.getByTestId("semantic-storyboard-product");
  await board.waitFor({ state: "visible", timeout: 90_000 });
  report.boardVisibleAt = new Date().toISOString();
  progress("embedded_board_visible");
  await page.waitForFunction(() => {
    const board = document.querySelector('[data-testid="semantic-storyboard-product"]');
    return board && ["paused", "failed", "declined"].includes(board.dataset.sessionStatus);
  }, null, { timeout: 90_000 });
  report.board = await board.evaluate((element) => ({ ...element.dataset }));
  await page.screenshot({ path: resolve(output, "live-storyboard.png"), fullPage: true });
  assert.equal(report.board.sessionStatus, "paused", "Live storyboard did not complete.");
  assert.equal(report.board.rendererTrusted, "true");
  assert(Number(report.board.settledBeatCount) >= 3, "Both traces and the range comparison must settle.");
  for (const id of ["trajectory_lower", "trajectory_higher", "range_relation"]) {
    assert(await page.locator(`[data-element-id="projectile-comparison__${id}"]`).count(), `Missing rendered ${id}`);
  }
  const countsBeforeReplay = { ...report.requests };
  await page.getByTestId("semantic-storyboard-replay").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="semantic-storyboard-product"]')?.dataset.sessionStatus === "paused", null, { timeout: 90_000 });
  assert.deepEqual(report.requests, countsBeforeReplay, "Replay made a provider request.");
  const replay = await board.evaluate((element) => ({ ...element.dataset }));
  assert.equal(replay.certificateHead, report.board.certificateHead, "Replay changed the certified frontier.");
  report.replay = "same_certificate_zero_network_requests";
  await page.getByRole("button", { name: "Close visual lesson" }).click();
  assert(await page.getByTestId("legacy-session-canvas").isVisible());
  assert(await input.isEnabled(), "Normal chat did not remain usable.");
  report.closeLesson = "legacy_canvas_and_chat_restored";
  await Promise.all(pendingResponses);
  assert.deepEqual(report.requests, { chat: 1, reflex: 1, director: 1 });
  assert(report.command, "No live model-selected storyboard command was observed.");
  assert(report.streams.every((s) => s.status === 200 && !s.readFailed && !s.error));
  const logs = await requestJson(`${apiUrl}/api/logs?limit=5`, { headers: headers() });
  report.modelLogs = logs.logs.filter((log) => log.session_id === report.sessionId).map((log) => ({
    provider: log.llm_provider, model: log.llm_model, tokensIn: log.tokens_in,
    tokensOut: log.tokens_out, latencyMs: log.latency_total_ms, error: log.error,
  }));
  report.result = "passed";
  progress("live_acceptance_passed");
} catch (error) {
  report.result = "failed";
  report.error = String(error.message).slice(0, 1500);
  if (page) {
    report.pageText = (await page.locator("body").innerText().catch(() => "")).slice(0, 5000);
    await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
  }
} finally {
  await browser?.close();
  if (identity) {
    // Delete only this generated test identity using its own fresh ID token.
    // Retain the isolated Murmur agent/session/log rows as acceptance evidence.
    const response = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:delete?key=${encodeURIComponent(identity.apiKey)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idToken: identity.idToken }), signal: AbortSignal.timeout(20_000),
    }).catch(() => null);
    report.cleanup.firebaseIdentityDeleted = response?.ok === true;
  }
  report.finishedAt = new Date().toISOString();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
  console.log(JSON.stringify({ result: report.result, error: report.error, requests: report.requests,
    cleanup: report.cleanup, report: resolve(output, "report.json") }));
  if (report.result !== "passed" || report.cleanup.firebaseIdentityDeleted !== true) process.exitCode = 1;
}
