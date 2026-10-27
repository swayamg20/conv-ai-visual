/** Opt-in live-provider probe. Browser authentication is a fixture, never a server bypass. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium, expect } from "@playwright/test";
import { DataPacket } from "@livekit/protocol";
import { prepareTurnClips, runTurnProbe } from "./cosmo-turn-probe.mjs";

if (!process.argv.includes("--live")) {
  throw new Error("Pass --live to run a real Cosmo session using this worktree's key.");
}
const root = fileURLToPath(new URL("../../", import.meta.url));
const turns = process.argv.includes("--turn-taking");
const completeClause = process.argv.includes("--complete-clause");
assert(!completeClause || turns, "--complete-clause requires --turn-taking.");
const pauseArgument = process.argv.find(value => value.startsWith("--pause-ms="));
const pauseMs = pauseArgument ? Number(pauseArgument.split("=")[1]) : 1200;
assert(Number.isInteger(pauseMs) && pauseMs >= 200 && pauseMs <= 4000, "Use --pause-ms=200..4000.");
assert(!pauseArgument || turns, "--pause-ms requires --turn-taking.");
const quadratic = process.argv.includes("--quadratic");
const microphone = turns || process.argv.includes("--microphone");
const output = path.join(root, "var", "cosmo-smoke", turns ? "turn-taking" : quadratic ? "quadratic" : microphone ? "microphone" : "text");
await mkdir(output, { recursive: true });
const input = quadratic ? "Can you tell me like can you just show me quadratic equations?" : "Please pin this exact thought on the board. The title is Experiment. The body is Voice and visuals can work together. Then briefly confirm it aloud.";
const browserArgs = ["--autoplay-policy=no-user-gesture-required"];
const turnClips = turns ? await prepareTurnClips(output, { completeClause }) : null;
if (microphone && !turns) {
  const speech = path.join(output, "speech.aiff");
  const wav = path.join(output, "microphone.wav");
  assert.equal(spawnSync("/usr/bin/say", ["-v", "Samantha", "-o", speech, input]).status, 0, "Microphone probe needs macOS say.");
  assert.equal(spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-i", speech, "-af", "adelay=10000,apad=pad_dur=8", "-ar", "48000", "-ac", "1", wav]).status, 0, "Microphone probe needs ffmpeg.");
  browserArgs.push("--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${wav}%noloop`);
}
const publicEnv = await readFile(path.join(root, "web/.env.local"), "utf8");
const apiKey = publicEnv.match(/^NEXT_PUBLIC_FIREBASE_API_KEY=(.+)$/m)?.[1].trim().replace(/^["']|["']$/g, "");
assert(apiKey, "Configure this worktree's Firebase frontend environment first.");
const probeUser = "murmur-cosmo-lab-browser-probe";
const minted = spawnSync(path.join(root, ".venv/bin/python"), ["-c", `
import asyncio, json
from dotenv import dotenv_values
from murmur.voice.cosmo import CosmoTokenBroker
async def main():
    key = dotenv_values('.env').get('COSMO_API_KEY', '')
    token = await CosmoTokenBroker(True, key).mint('${probeUser}')
    print(json.dumps(token.browser_payload()))
asyncio.run(main())
`], { cwd: root, encoding: "utf8", timeout: 60000 });
if (minted.status !== 0) throw new Error("The real token broker could not mint a probe token. Check the key and backend configuration.");
// The token stays in process memory. It is never logged or saved in artifacts.
const token = JSON.parse(minted.stdout.trim());
console.log(JSON.stringify({ stage: "token_minted" }));
const browser = await chromium.launch({
  headless: true,
  args: browserArgs,
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: "block" });
const page = await context.newPage();
let tokenRequests = 0;
const errors = [];
const network = [];
let providerSettings = null;
page.on("request", request => {
  const url = new URL(request.url());
  if (url.hostname !== "platform.askcosmo.ai" || !url.pathname.endsWith("/realtime/session/start") || request.method() !== "POST") return;
  const agent = request.postDataJSON()?.agent;
  const options = agent?.model_options;
  // Allowlist non-secret settings; never retain the full request or instructions.
  providerSettings = {
    provider: options?.provider,
    turnDetection: options?.turn_detection,
    pauseMs: options?.cosmo_vad?.pause_ms,
    maxHoldMs: options?.cosmo_vad?.max_hold_ms,
    interruption: agent?.interruption_sensitivity,
  };
});
// Only allowlisted provider events from this synthetic conversation are retained.
// Never save raw transport packets, room credentials, or token responses.
const wireEvents = [];
await page.exposeFunction("__cosmoObservePacket", (bytes, at) => {
  try {
    const packet = DataPacket.fromBinary(Uint8Array.from(bytes));
    if (packet.value.case !== "user") return;
    const event = JSON.parse(new TextDecoder().decode(packet.value.value.payload));
    if (event.type === "transcript") {
      wireEvents.push({ at, type: event.type, role: event.role, text: event.text, isFinal: event.is_final });
    } else if (/^(?:user-(?:started|stopped)-speaking|bot-(?:started|stopped)-speaking|turn-complete|tool-call|tool-result)$/.test(event.type)) {
      wireEvents.push({ at, type: event.type, role: event.role, name: event.name, ok: event.ok });
    }
  } catch { /* Other LiveKit packet types are irrelevant to this probe. */ }
});
page.on("pageerror", error => errors.push(error.name));
page.on("response", async response => {
  const url = new URL(response.url());
  if (!url.hostname.endsWith("askcosmo.ai")) return;
  const entry = { path: url.pathname, status: response.status() };
  if (response.status() >= 400) {
    const body = await response.json().catch(() => null);
    const code = body?.error?.code ?? body?.detail?.code;
    if (typeof code === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(code)) entry.code = code;
    const message = body?.error?.message ?? body?.detail?.message;
    if (typeof message === "string") entry.message = message.replace(/(?:cosmo_[\w.-]+|eyJ[\w.-]+|Bearer\s+\S+)/g, "[redacted]").slice(0, 800);
    if (Array.isArray(body?.detail)) entry.validation = body.detail.map(item => ({ location: item.loc, type: item.type }));
  }
  network.push(entry);
});
page.on("requestfailed", request => {
  const url = new URL(request.url());
  network.push({ path: url.pathname, failure: request.failure()?.errorText });
});
try {
  await context.route("https://identitytoolkit.googleapis.com/**", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ users: [{ localId: probeUser, email: "cosmo-probe@example.test", emailVerified: true, providerUserInfo: [] }] }),
  }));
  await context.route("**/api/voice/cosmo/token", async route => {
    assert.equal(route.request().headers().authorization, "Bearer cosmo-browser-fixture-token");
    assert.deepEqual(route.request().postDataJSON(), {});
    tokenRequests += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(token), headers: { "Access-Control-Allow-Origin": "http://localhost:3013" } });
  });
  await page.addInitScript(({ apiKey, probeUser, turns }) => {
    localStorage.setItem(`firebase:authUser:${apiKey}:[DEFAULT]`, JSON.stringify({
      uid: probeUser, email: "cosmo-probe@example.test", emailVerified: true,
      isAnonymous: false, providerData: [], apiKey, appName: "[DEFAULT]",
      stsTokenManager: { accessToken: "cosmo-browser-fixture-token", refreshToken: "fixture", expirationTime: Date.now() + 3600000 },
    }));
    const NativePeer = window.RTCPeerConnection;
    window.__cosmoProbe = { peers: [], localTracks: [], nonzeroAudioSamples: 0, peak: 0, audioFrames: [] };
    const observedChannels = new WeakSet();
    const observeChannel = channel => {
      if (observedChannels.has(channel)) return;
      observedChannels.add(channel);
      channel.addEventListener("message", async event => {
        const data = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
        if (!(data instanceof ArrayBuffer) || data.byteLength > 65536) return;
        void window.__cosmoObservePacket(Array.from(new Uint8Array(data)), performance.now());
      });
    };
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      let stream;
      if (turns) {
        const inputContext = new AudioContext({ sampleRate: 48000 });
        const destination = inputContext.createMediaStreamDestination();
        await inputContext.resume();
        stream = destination.stream;
        window.__cosmoProbe.playInput = async base64 => {
          const bytes = Uint8Array.from(atob(base64), character => character.charCodeAt(0));
          const decoded = await inputContext.decodeAudioData(bytes.buffer);
          const source = inputContext.createBufferSource();
          source.buffer = decoded;
          source.connect(destination);
          const startMs = performance.now();
          await new Promise(resolve => { source.onended = resolve; source.start(); });
          source.disconnect();
          return { startMs, endMs: performance.now(), durationMs: decoded.duration * 1000 };
        };
      } else stream = await getUserMedia(constraints);
      window.__cosmoProbe.localTracks.push(...stream.getTracks());
      return stream;
    };
    window.RTCPeerConnection = class extends NativePeer {
      constructor(...args) {
        super(...args);
        window.__cosmoProbe.peers.push(this);
        this.addEventListener("datachannel", event => observeChannel(event.channel));
        this.addEventListener("track", ({ track }) => {
          if (track.kind !== "audio") return;
          const audio = new AudioContext();
          const source = audio.createMediaStreamSource(new MediaStream([track]));
          const analyser = audio.createAnalyser();
          const silence = audio.createGain();
          silence.gain.value = 0;
          source.connect(analyser); analyser.connect(silence); silence.connect(audio.destination);
          void audio.resume();
          const samples = new Float32Array(analyser.fftSize);
          const timer = setInterval(() => {
            analyser.getFloatTimeDomainData(samples);
            let energy = 0;
            for (const sample of samples) {
              energy += sample * sample;
              const level = Math.abs(sample);
              window.__cosmoProbe.peak = Math.max(window.__cosmoProbe.peak, level);
              if (level > 0.0001) window.__cosmoProbe.nonzeroAudioSamples += 1;
            }
            window.__cosmoProbe.audioFrames.push({ at: performance.now(), rms: Math.sqrt(energy / samples.length) });
            if (window.__cosmoProbe.audioFrames.length > 6000) window.__cosmoProbe.audioFrames.shift();
          }, 20);
          track.addEventListener("ended", () => { clearInterval(timer); void audio.close(); }, { once: true });
        });
      }
      createDataChannel(...args) {
        const channel = super.createDataChannel(...args);
        observeChannel(channel);
        return channel;
      }
    };
  }, { apiKey, probeUser, turns });

  await page.goto("http://localhost:3013/labs/cosmo", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Start conversation" }).waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent.includes("Start conversation") && !button.disabled));
  await page.getByText("Conversation settings", { exact: true }).click();
  if (!microphone) await page.getByLabel("Use microphone", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Start conversation" }).click();
  await Promise.race([
    page.getByRole("status").filter({ hasText: /^Connected$/ }).waitFor({ timeout: 60000 }),
    page.getByRole("alert").filter({ hasText: /Could not|Cosmo reported|Cosmo rejected/ }).waitFor({ timeout: 60000 }).then(() => { throw new Error("Connection failed; see sanitized network diagnostics."); }),
  ]);
  console.log(JSON.stringify({ stage: "provider_ready", mode: turns ? "turn-taking" : quadratic ? "quadratic" : microphone ? "microphone" : "text" }));
  let turnEvidence = null;
  if (turns) {
    assert(providerSettings?.turnDetection === "cosmo_vad" && Number.isFinite(providerSettings.pauseMs), "The probe must observe the actual Gemini turn settings.");
    turnEvidence = await runTurnProbe(page, turnClips, { pauseMs, providerSettings, completeClause });
  }
  else if (!microphone) {
    await page.getByRole("textbox", { name: "Message to Murmur" }).fill(input);
    await page.getByRole("button", { name: "Send message" }).click();
  }
  if (!turns) {
    await page.locator("[data-thought-id]").first().waitFor({ timeout: 45000 });
    await page.locator('[data-transcript-role="assistant"]').filter({ hasText: quadratic ? /graph|curve|quadratic|board/i : /pinned|added|captured|on (?:the|your) board|visible/i }).first().waitFor({ timeout: 30000 });
    const thought = await page.locator("[data-thought-id]").first().textContent();
    const userTranscript = await page.locator('[data-transcript-role="user"]').allTextContents();
    if (quadratic) {
      assert(thought.includes("x²"));
      assert((await page.locator('[data-visual="quadratic"] g[id^="plot_"] path').getAttribute("d")).length > 2000);
      assert(userTranscript.join(" ").toLowerCase().includes("quadratic"));
    } else {
      assert(thought.toLowerCase().includes("voice and visuals can work together"));
      assert(userTranscript.join(" ").toLowerCase().includes("voice and visuals"));
    }
    // Finish the post-tool response; a greeting or first fragment is insufficient.
    await expect.poll(() => {
      const result = wireEvents.findLast(event => event.type === "tool-result" && event.ok);
      return Boolean(result && wireEvents.some(event => event.type === "transcript" && event.role === "ASSISTANT" && event.isFinal && event.at > result.at));
    }, { timeout: 30000 }).toBe(true);
  }
  await page.waitForFunction(() => window.__cosmoProbe.nonzeroAudioSamples > 1000, undefined, { timeout: 20000 });
  const pcm = await page.evaluate(() => ({ samples: window.__cosmoProbe.nonzeroAudioSamples, peak: window.__cosmoProbe.peak }));
  if (microphone) {
    await page.getByRole("button", { name: "Mute", exact: true }).click();
    await page.getByRole("button", { name: "Unmute", exact: true }).waitFor();
  }
  await page.screenshot({ path: path.join(output, "live-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(output, "live-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "End call" }).click();
  await page.getByRole("status").filter({ hasText: /^Call ended$/ }).waitFor({ timeout: 15000 });
  const openPeers = await page.evaluate(() => window.__cosmoProbe.peers.filter(peer => peer.connectionState !== "closed").length);
  assert.equal(openPeers, 0);
  const media = await page.evaluate(() => ({
    capturedTracks: window.__cosmoProbe.localTracks.length,
    liveTracks: window.__cosmoProbe.localTracks.filter(track => track.readyState !== "ended").length,
  }));
  assert.equal(media.liveTracks, 0);
  if (microphone) assert(media.capturedTracks > 0);
  assert.equal(errors.length, 0);
  const evidence = {
    authentication: "Firebase browser fixture; real server auth tested separately",
    tokenBroker: "real CosmoTokenBroker and real Cosmo mint API",
    provider: "real Cosmo session through the production browser adapter",
    input: turns ? "controlled synthetic Web Audio microphone; real Cosmo speech handling" : microphone ? "synthetic speech through Chromium's microphone capture and real Cosmo transcription" : "typed text; no microphone capture in this probe",
    tool: turns ? "not requested" : quadratic ? "show_quadratic rendered with Murmur SVGCanvas" : "pin_thought rendered on the actual page",
    turnEvidence,
    providerSettings,
    tokenRequests, decodedNonzeroAudioSamples: pcm.samples, audioPeak: pcm.peak,
    openPeersAfterEnd: openPeers, localTracksCaptured: media.capturedTracks, liveLocalTracksAfterEnd: media.liveTracks, pageErrors: errors.length,
  };
  await writeFile(path.join(output, "result.json"), JSON.stringify(evidence, null, 2) + "\n");
  console.log(JSON.stringify(evidence));
} catch (error) {
  await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
  const alert = await page.getByRole("alert").allTextContents().catch(() => []);
  await writeFile(path.join(output, "failure-transcript.json"), JSON.stringify(await page.locator('[data-transcript-role]').allTextContents().catch(() => []), null, 2));
  const activity = await page.locator("details").filter({ has: page.getByText("Session activity", { exact: true }) }).allTextContents().catch(() => []);
  console.error(JSON.stringify({ failure: error.message, visibleAlerts: alert, network, errors, activity }));
  process.exitCode = 1;
} finally {
  const end = page.getByRole("button", { name: "End call" });
  if (await end.isVisible().catch(() => false)) await end.click().catch(() => {});
  await writeFile(path.join(output, "wire-events.json"), JSON.stringify(wireEvents, null, 2) + "\n");
  await context.close();
  await browser.close();
}
