# Cosmo voice lab

**Architecture correction, 2026-09-17:** The requested experiment is a cascaded STT -> regular text LLM -> TTS pipeline. The implementation described below is the earlier native-audio prototype; its Gemini Live results do not meet that requirement. Cosmo's [architecture guide](https://platform.askcosmo.ai/docs/architecture) says it supports modular pipelines, but a runnable non-native configuration or workspace catalog agent is still needed. See [the current capability findings](COSMO_SDK_FEEDBACK.md#cascaded-pipeline-requirement) and [updated execution plan](../plans/2026-09-13-cosmo-voice-lab.md). No runtime switch has been made yet.

Open **http://localhost:3013/labs/cosmo** to experiment with Cosmo as Murmur's conversational runtime. Sign in with the existing Murmur account, start a conversation, and ask it to show a quadratic equation or pin an idea. The page provides speech, a live transcript, microphone controls, typed messages, and a temporary visual board. Conversation settings expose the provider, pause handling, interruption sensitivity, instructions, and recording choice before a call starts.

This experiment lives on `codex/cosmo-voice-lab`, in `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-cosmo-voice`, based on merged main `01acc4683c2420c580ebb77283ab2a2292e0b3a4`. It runs separately from the Pipecat and Gate 1.8 worktrees. It uses `cosmo-ai@0.6.0` and does not change `VOICE_RUNTIME`.

## Run locally

From the worktree root, install backend dependencies with `uv sync --locked --extra dev`. In `web/`, run `npm ci`. Copy `.env.example` to `.env` and `web/.env.example` to `web/.env.local` only if the local files do not already exist. Use the project's normal Firebase server and browser configuration. This lab does not require Deepgram, ElevenLabs, or a separate model-provider key in Murmur.

Set these backend values, keeping the database and ports isolated:

```dotenv
MURMUR_COSMO_ENABLED=true
ALLOWED_CORS_ORIGINS=http://localhost:3013
MURMUR_DATABASE_URL=sqlite:////Users/swayam.gupta/Documents/GitHub/conv-ai-visual-cosmo-voice/var/murmur.db
```

Set `NEXT_PUBLIC_API_URL=http://localhost:8013` in `web/.env.local`. Enter a Cosmo workspace key with `user_tokens:mint` through the hidden-input helper:

```sh
python3 scripts/configure_cosmo.py
```

The helper updates only the Cosmo entries, atomically saves `.env` with owner-only permissions, and does not echo the key. Restart the backend after changing it. Both local environment files and `var/` are ignored by Git.

Start these in separate terminals:

```sh
# Worktree root
uv run uvicorn main:app --host 127.0.0.1 --port 8013
```

```sh
# web/
npm run dev -- --hostname 127.0.0.1 --port 3013
```

For a production browser build, use `npm run build` followed by `npm run start -- --hostname 127.0.0.1 --port 3013`. The legacy runtime may log a missing Deepgram key on startup; this lab's credential path is independent.

## Ownership and behavior

The browser sends its Firebase bearer token to `POST /api/voice/cosmo/token` with `{}`. FastAPI verifies that identity and uses its user ID when minting a 15-minute Cosmo token. The browser cannot supply a different user ID and never receives the workspace key. The server bounds the upstream wait and response size, validates token expiry, disables redirects, and returns sanitized errors with successful credential responses marked `no-store`.

Cosmo owns the realtime conversation, audio transport, and model tool requests. Murmur owns the visual board and the evidence that a card was rendered. `pin_thought` first acknowledges acceptance, then completes only after React commits the matching card. `show_quadratic` accepts numeric coefficients for ax² + bx + c = 0; Murmur computes the curve, real roots, and vertex, then uses the existing `SVGCanvas.renderFunctionPlot` renderer. This fast foreground tool returns only after the actual curve exists in the SVG and crosses a browser presentation frame, so the result can precede the explanation. Coefficients are bounded to -100..100 and |a| must be at least 0.1; arbitrary code and model-authored plot coordinates are not accepted. `read_thoughts` returns committed cards and their mathematical summaries. Ending a call cancels pending render waits; callbacks from old calls cannot alter a new call. A new conversation clears the board. The main scene/reasoner stream and long-term memory are still separate from this temporary board.

Recording defaults off through `storeRecording: false`; the optional checkbox requests recording subject to Cosmo's account policy. Only the chosen provider's access in the Cosmo workspace is needed. After the first human-session feedback, the initial selection is explicit Gemini with patient turn-taking and high interruption sensitivity. Gemini thought summaries are explicitly disabled. Workspace default and OpenAI remain selectable.

## Pauses and interruptions

The two controls are independent. High interruption sensitivity makes the assistant yield more readily when the user starts speaking. Patient turn-taking gives the user more time before their turn is considered complete. After the user clarified that the main issue was answering before they finished, the Gemini patient preset changed from 900/4,000 ms to `pauseMs: 2000`, `prefixMs: 400`, and `maxHoldMs: 5000` with `cosmo_vad`. This allows two seconds of silence before the semantic classifier first checks whether the turn ended; 5,000 ms is a total-silence ceiling when the classifier holds, not an unconditional five-second delay. It deliberately trades some response speed for more room to finish. The prefix preserves opening syllables. For OpenAI the patient preset selects `semantic_vad` with `eagerness: low`. These are experimental application presets, not claimed Cosmo defaults. [Cosmo turn-taking documentation](https://platform.askcosmo.ai/docs/concepts/turn-taking).

Balanced and Quick presets are available for comparison. Choosing Workspace default disables the pause selector because a provider-specific setting cannot honestly apply to an unspecified provider. Settings are applied when a new session starts: end the old call, refresh after code updates, and start a new call. Noise cancellation stays at the SDK default (off); the SDK's browser capture still uses echo cancellation.

Session activity includes relative browser-receipt times for user speech boundaries, assistant response/audio markers, completed turns, and tool events. The async stream is consumed once from `onSession`. Server audio markers describe server emission rather than sound at the user's speakers; do not use them alone as measured barge-in latency. The bounded live probe additionally samples decoded remote audio energy.

Transcripts stay in each speaker's open bubble until that speaker's final arrives. Cosmo's shared SDK turn ID can reset on the other speaker's completion, so grouping solely by that ID duplicated delayed user finals and hid a subsequent interruption in the first implementation. A saved live wire replay confirmed this local bug. The lab now follows SDK 0.6.0's open-bubble reducer semantics, while retaining event deduplication and bounds. This fixes displayed text; it does not itself change the provider's audio endpointing.

The adapter subscribes through `start({ onSession })` before connection events can fire. It waits for protocol readiness, handles rejected tools, and closes a connected room that has not become ready within 45 seconds. Tool schemas use Cosmo's `tool()` builder so its restricted dialect is checked locally.

SDK 0.6.0 has a reproduced startup cancellation defect. Our adapter defers its first `end()` until the pending `start()` settles, then closes that final transport exactly once. The page stays in “Ending the call…” throughout and does not allow another call to overlap. This prevents a late room from becoming unowned, but a stalled startup POST cannot currently be aborted through the public SDK, and microphone acquisition can occur briefly before deferred cleanup. If cleanup fails, the page retains the call owner and asks for a reload. See [the internal SDK feedback](COSMO_SDK_FEEDBACK.md) for evidence and suggested fixes.

## Verification

The implementation passed 20 token-broker and HTTP tests plus 73 existing authentication/session-continuity tests. Frontend typecheck, production build, full lint, and all 1,169 frontend tests passed. The lab has 36 tests covering early SDK events, pending-start cancellation, uncertain teardown, readiness timeout, remote disconnect, stale callbacks, interleaved transcripts, graph mathematics, provider-specific turn settings, render acknowledgement, and signed-in UI behavior.

```sh
# Worktree root
uv run ruff check backend/murmur/voice/cosmo.py backend/murmur/api/routers/cosmo_voice.py scripts/configure_cosmo.py tests/test_cosmo_voice.py
uv run pytest tests/test_cosmo_voice.py tests/test_authentication.py tests/test_authenticated_session_continuity.py
```

```sh
# web/
npm run lint
npm run typecheck
npm run test
npm run build
```

For an explicit live-provider check, run the backend and production frontend on the ports above, then:

```sh
# web/
node scripts/cosmo-voice-smoke.mjs --live
node scripts/cosmo-voice-smoke.mjs --live --microphone
node scripts/cosmo-voice-smoke.mjs --live --quadratic
node scripts/cosmo-voice-smoke.mjs --live --quadratic --microphone
node scripts/cosmo-voice-smoke.mjs --live --turn-taking
node scripts/cosmo-voice-smoke.mjs --live --turn-taking --pause-ms=2200
node scripts/cosmo-voice-smoke.mjs --live --turn-taking --pause-ms=1600 --complete-clause
```

The browser probe uses a fixture for Firebase sign-in and intercepts only that browser's token endpoint. It exercises the real Python broker against Cosmo's mint API, then the production browser adapter against the real Cosmo service. No authentication bypass is installed in FastAPI. The microphone variants use macOS `say` and `ffmpeg` to feed synthetic speech into Chromium's microphone capture or a controlled Web Audio media stream. They do not record the user's microphone. Checks cover requested tool rendering or turn-taking behavior, transcription, decoded remote audio, desktop/mobile layout, and final WebRTC cleanup. Credentials remain in process memory and are not saved in artifacts. Screenshots, a small result JSON, and allowlisted provider events from the synthetic conversation go under ignored `var/cosmo-smoke/`; raw transport packets are never retained.

On 2026-09-13, real sessions succeeded with both typed input and synthetic microphone input. Cosmo transcribed the spoken request, invoked `pin_thought`, rendered “voice and visuals can work together,” and confirmed it in the assistant transcript while delivering nonzero decoded audio. The microphone run counted 712,801 nonzero samples, captured one local audio track, exercised Mute, and ended with zero live local tracks, zero open WebRTC peers, and zero browser exceptions. Desktop and narrow mobile screenshots passed the horizontal-overflow check. A final production-build typed run also rendered its thought, received the assistant confirmation and 163,913 nonzero audio samples, captured no microphone tracks, and ended with zero open peers or browser exceptions. Result JSON and screenshots are in `var/cosmo-smoke/microphone/` and `var/cosmo-smoke/text/`.

Those initial probes did not establish genuine browser Firebase sign-in, human microphone quality, barge-in latency, Hinglish quality, or live reconnect behavior. The user's subsequent human session exposed premature turn endings/missed interruptions and an explicit visual refusal. The follow-up probes exercise the quadratic request and a controlled synthetic pause/interruption through the new Gemini preset. These remain quiet-room synthetic checks, not a human latency benchmark or qualification of OpenAI.

A follow-up synthetic microphone session replayed the user's quadratic request successfully: `show_quadratic` was requested, returned success after the real curve rendered, and the assistant then described the graph. The run received 587,607 nonzero decoded samples, captured one microphone track, and ended with zero live tracks, open peers, or browser errors. An earlier background-tool attempt had claimed a graph without calling the tool, and another emitted planning-style text. The current foreground handoff, explicit capability prompt, and `includeThoughts: false` reduce those failure paths, but tool compliance remains model behavior that should be checked in continued human use.

A final-build typed quadratic probe also passed, including waiting for a complete post-tool response. The actual plotted example was x² − 5x + 6 = 0; the response correctly identified roots 2 and 3 and vertex (2.5, −0.25). It received 2,824,810 nonzero decoded samples and ended with zero peers, microphone captures, or browser errors.

With the earlier 900/4,000 ms preset, the corrected turn-taking probe waited for the greeting to finish, spoke an unfinished sentence, paused 1.224 seconds, then completed it. No assistant transcript began during the pause; the full request and later interruption were preserved. Interrupting an ongoing dragon story caused it to stop mid-sentence and answer the replacement request. Cleanup left zero live tracks, open peers, or browser errors. A first-quiet-gap timing was misleading because ordinary gaps occur within speech; the probe now measures the final decoded speech followed by silence during the remainder of the interrupting clip. These observations support the experimental settings, not a claim that human interruptions are universally fixed.

The stricter repeat also passed: no assistant text or decoded audio during a 1.230-second pause, both user utterances preserved, and the replacement request obeyed. The last old-response speech was observed 1.441 seconds after interruption began, followed by 3.960 seconds of quiet while the interrupting clip continued. This is browser-decoded audio evidence from one synthetic scenario, not a calibrated human latency benchmark. The run captured one local track and finished with zero live tracks, open peers, or browser errors.

For the clarified early-answer complaint, the probe now also checks that the assistant stays silent through the entire spoken continuation, and records the actual non-secret settings in the outgoing session request. `--complete-clause` pauses after “Please tell me a long story” before adding “about a dragon…”; this is a more ambiguous stopping point than a phrase ending in “about.” A baseline with the old preset also passed a 2.225-second unfinished-phrase pause, so those synthetic successes should not be described as reproducing the original human failure. The baseline result and wire events are preserved under `var/cosmo-smoke/turn-taking/baseline-900ms-pause-2200ms/`.

The new 2,000/5,000 ms preset passed both final live scenarios: an unfinished phrase paused 2.223 seconds and a complete clause paused 1.626 seconds before additional detail. Neither produced assistant text or decoded audio before the full request finished. Both preserved the request and interruption, obeyed the replacement request, and ended with one captured microphone track, zero live tracks, zero open peers, and zero browser errors. The actual outgoing request carried the intended Gemini settings. Results and wire events are preserved in `patient-2000ms-unfinished-pause-2200ms/` and `patient-2000ms-complete-clause-pause-1600ms/` under the same turn-taking artifact directory. The 36 lab tests, scoped lint, typecheck, and production build passed after this adjustment. Human microphone behavior still requires a fresh-call retest.

## Why this experiment is useful

The successful live tool-to-render path shows that a managed voice runtime can drive Murmur-owned visual work. It gives us a place to test natural conversation independently of the existing voice implementation. The next product slice would replace the temporary board adapter with the verified scene/reasoner contract once that contract is ready, preserving render receipts, task ownership, and stale-result rejection.
