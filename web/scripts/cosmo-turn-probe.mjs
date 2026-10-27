/** Controlled, synthetic speech only. Measures one pause and one barge-in. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";

export async function prepareTurnClips(output, { completeClause = false } = {}) {
  const phrases = {
    prefix: completeClause ? "Please tell me a long story" : "Please tell me a long story about",
    continuation: `${completeClause ? "about a" : "a"} dragon who learns algebra. Take about a minute to tell the whole story.`,
    interrupt: "Wait, stop the story. I changed my mind. Just say banana, and nothing else.",
  };
  const clips = {};
  for (const [name, phrase] of Object.entries(phrases)) {
    const aiff = path.join(output, `${name}.aiff`);
    const wav = path.join(output, `${name}.wav`);
    assert.equal(spawnSync("/usr/bin/say", ["-v", "Samantha", "-o", aiff, phrase]).status, 0);
    assert.equal(spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-i", aiff, "-ar", "48000", "-ac", "1", wav]).status, 0);
    clips[name] = (await readFile(wav)).toString("base64");
  }
  return clips;
}

export async function runTurnProbe(page, clips, { pauseMs, providerSettings, completeClause }) {
  await page.waitForFunction(() => typeof window.__cosmoProbe.playInput === "function");
  // Let the greeting finish; nothing in this helper captures hardware audio.
  await page.waitForFunction(() => {
    const frames = window.__cosmoProbe.audioFrames;
    const recent = frames.filter(frame => frame.at > performance.now() - 800);
    const greetingEnded = [...document.querySelectorAll("details")].some(detail => detail.textContent.includes("Assistant turn completed"));
    return greetingEnded && window.__cosmoProbe.nonzeroAudioSamples > 1000 && recent.length > 20 && recent.every(frame => frame.rms < 0.005);
  }, undefined, { timeout: 20000 });
  const before = await page.locator('[data-transcript-role="assistant"]').count();
  const prefix = await page.evaluate(base64 => window.__cosmoProbe.playInput(base64), clips.prefix);
  console.log(JSON.stringify({ stage: "prefix_played", ...prefix }));
  await page.waitForTimeout(pauseMs);
  const duringPause = await page.locator('[data-transcript-role="assistant"]').count();
  const continuation = await page.evaluate(base64 => window.__cosmoProbe.playInput(base64), clips.continuation);
  console.log(JSON.stringify({ stage: "continuation_played", ...continuation }));
  const afterContinuation = await page.locator('[data-transcript-role="assistant"]').count();
  const audioDuringPause = await page.evaluate(({ start, end }) => window.__cosmoProbe.audioFrames.some(frame => frame.at > start && frame.at < end && frame.rms > 0.02), { start: prefix.endMs, end: continuation.startMs });
  const audioBeforeFinished = await page.evaluate(({ start, end }) => window.__cosmoProbe.audioFrames.some(frame => frame.at > start && frame.at < end && frame.rms > 0.02), { start: prefix.startMs, end: continuation.endMs });
  // Require decoded output AFTER the complete user request, not greeting PCM.
  await page.waitForFunction(after => window.__cosmoProbe.audioFrames.some(frame => frame.at > after && frame.rms > 0.02), continuation.endMs, { timeout: 20000 });
  await page.waitForTimeout(1500);
  const speakingBeforeInterrupt = await page.evaluate(() => window.__cosmoProbe.audioFrames.slice(-10).some(frame => frame.rms > 0.01));
  assert(speakingBeforeInterrupt, "The assistant must be audibly speaking when the interruption starts.");
  const interruption = await page.evaluate(base64 => window.__cosmoProbe.playInput(base64), clips.interrupt);
  console.log(JSON.stringify({ stage: "interruption_played", ...interruption }));
  await page.locator('[data-transcript-role="assistant"]').filter({ hasText: /banana/i }).first().waitFor({ timeout: 25000 });
  const audioAfterInterruption = await page.evaluate(({ startMs, endMs }) => {
    const frames = window.__cosmoProbe.audioFrames.filter(frame => frame.at >= startMs && frame.at <= endMs);
    if (!frames.length) return null;
    // The FIRST quiet gap can just be a between-word pause. Measure the final
    // loud frame followed by silence through the rest of the interrupting clip.
    const lastSpeechAt = frames.findLast(frame => frame.rms >= 0.005)?.at ?? startMs;
    return { lastDecodedSpeechMs: lastSpeechAt - startMs, quietTailMs: endMs - lastSpeechAt };
  }, interruption);
  const userTurns = await page.locator('[data-transcript-role="user"]').allTextContents();
  const result = {
    scenario: completeClause ? "complete clause followed by more detail" : "unfinished phrase",
    configuredPauseMs: providerSettings.pauseMs,
    configuredMaxHoldMs: providerSettings.maxHoldMs,
    syntheticPauseMs: continuation.startMs - prefix.endMs,
    assistantRespondedDuringPause: duringPause > before,
    assistantAudioDuringPause: audioDuringPause,
    assistantRespondedBeforeRequestFinished: afterContinuation > before,
    assistantAudioBeforeRequestFinished: audioBeforeFinished,
    completeRequestPreserved: /long story/i.test(userTurns.join(" ")) && /dragon/i.test(userTurns.join(" ")),
    prefixAndContinuationInSameBubble: userTurns.some(text => /long story/i.test(text) && /dragon/i.test(text)),
    interruptionPreserved: userTurns.some(text => /stop the story/i.test(text) && /banana/i.test(text)),
    interruptionStartedDuringDecodedSpeech: speakingBeforeInterrupt,
    audioAfterInterruption,
    obeyedChangedRequest: true,
    caveat: "One synthetic quiet-room scenario; decoded-audio timing is not a calibrated human latency benchmark.",
  };
  assert.equal(result.assistantRespondedDuringPause, false, JSON.stringify(result));
  assert.equal(result.assistantAudioDuringPause, false, JSON.stringify(result));
  assert.equal(result.assistantRespondedBeforeRequestFinished, false, JSON.stringify(result));
  assert.equal(result.assistantAudioBeforeRequestFinished, false, JSON.stringify(result));
  // Transcription segments can finalize separately without the agent taking a turn.
  assert(result.completeRequestPreserved && result.interruptionPreserved, JSON.stringify(result));
  assert(audioAfterInterruption && audioAfterInterruption.lastDecodedSpeechMs < 2500 && audioAfterInterruption.quietTailMs > 500, JSON.stringify(result));
  return result;
}
