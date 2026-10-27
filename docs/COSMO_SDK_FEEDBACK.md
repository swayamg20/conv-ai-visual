# Cosmo SDK feedback from the Murmur voice lab

Maintained as a working log at the user's request (2026-09-13). For each new finding, record the observed behavior and impact, reproduction or evidence, tested version, likely owner, current workaround or resolution, and a concrete SDK suggestion when applicable. Keep user reports, confirmed integration bugs, verified public API gaps, and unproven hypotheses distinct. Update outcomes without erasing the earlier evidence; omit credentials and raw private session data.

## Experience issues being tracked

| Finding | Current status |
| --- | --- |
| The lab uses a native speech model, while the user wants a generic STT -> text LLM -> TTS pipeline | Integration architecture mismatch, clarified 2026-09-17. Authenticated discovery on 2026-10-27 found an empty catalog and two additional native voice providers. Their editor and live validation contracts expose no separate STT or ordinary text LLM. The workspace presently offers no configurable STT -> ordinary text LLM -> TTS cascade. OpenAI availability remains true; this is not a Gemini-only workspace. |
| Assistant answers before the user finishes | User-confirmed symptom. Synthetic pause checks pass but have not reproduced the original human failure. Timing adjustments are a workaround; the request is now for explicit turn-detector and interruption ownership. |
| Assistant says it cannot show quadratics | Confirmed initial lab capability/prompt gap, addressed with `show_quadratic` and Murmur's renderer. Spoken and typed tool-to-render checks passed. |
| User reports that Gemini cannot control graph generation | Reported 2026-09-19; the exact failing request is not yet identified. Confirmed local limitation: only new quadratic cards are exposed, with no edit/delete/layout/animation tools or connection to the full scene/reasoner. Do not label this a Gemini API limitation. |
| Assistant claims a graph is visible without rendering it | Observed in an early live attempt. Foreground render receipts and stronger capability instructions added; later checks passed. Continued model-compliance verification remains necessary. |
| Planning-style prose appears in the assistant transcript | Observed in an early live attempt. Thought summaries explicitly disabled; this does not prove that every spoken response is free of such prose. |
| Interrupted speech disappears or earlier text repeats | Confirmed local transcript grouping bug, fixed and verified with a real wire replay, regression tests, and live interruption checks. |

Detailed run evidence and current lab behavior are in [COSMO_VOICE_LAB.md](COSMO_VOICE_LAB.md). The source-backed SDK findings and requested controls follow below.

## Review context

Internal notes, 2026-09-13. Reviewed the installed TypeScript `cosmo-ai@0.6.0`, source hash `d1b2d189924819e44b07588563b86e36709d271cbd4f2233cac190dca877be26`. These are integration findings, not published issues. The side review used local, provider-free reproductions and official source; it did not read credentials or open live sessions. One live HTTP observation below came from the main integration agent.

Human-session follow-up: the user clarified that the primary issue is the assistant answering before they finish a sentence. SDK research confirmed that interruption sensitivity and endpoint patience are separate and correctly serialize with Gemini `cosmo_vad` or OpenAI `semantic_vad`. The lab now exposes both controls and receives raw speech markers through one async iterator. A useful future diagnostic addition would be an effective turn-detector configuration in `ready`, with a timestamped endpoint decision/reason. Current raw speech markers have neither timestamps nor turn IDs, and docs do not publish numeric Gemini runtime defaults. The probe now records the allowlisted outgoing settings; that proves what the client requested, not a server echo of effective settings. This is a feedback suggestion arising from the investigation, not a confirmed cause of the user's early replies.

## Graph control in the current lab

On 2026-09-19, traced the actual tool path: `controller.ts` creates a call-scoped `ThoughtBoard` and passes only `board.tools()` to `sdk.ts`, which registers them on the Cosmo agent. `thought-board.ts` exposes `show_quadratic`, `pin_thought`, and `read_thoughts`. The quadratic tool accepts bounded numeric coefficients and creates a new card; `quadratic.ts` computes the points, roots, and vertex, and `quadratic-card.tsx` calls `SVGCanvas.renderFunctionPlot`. The tool returns success only after the matching SVG curve and presentation frames are observed.

This is a small creation tool, not full graph control. There is no tool for updating a selected graph by ID, removing it, changing axes/style/layout, animating it, or creating other plot/diagram types. The prompt also explicitly says other diagram types are unavailable. Each new call creates a new temporary board, and the full Murmur scene/reasoner path is not connected to this adapter. These are confirmed integration boundaries; they explain why those operations are unavailable, but do not establish the cause of an unspecified failed request to create a supported quadratic.

Gemini Live supports [function calling](https://ai.google.dev/gemini-api/docs/live-api/tools), including the previously observed Gemini 3.1 Flash Live model. Re-read the saved synthetic probe in `var/cosmo-smoke/quadratic/result.json`: it records a real Cosmo session, typed input, `show_quadratic rendered with Murmur SVGCanvas`, and no page errors. The corresponding allowlisted wire trace contains the `show_quadratic` tool call. This is historical evidence that the complete tool-to-render route can work, not a new live test or proof that the current human failure is fixed.

For a failed creation request, distinguish no tool call, rejected arguments, failed handler, absent render receipt, and a stale or ended call before assigning a cause. The current user report does not distinguish those cases. Earlier observed false visibility claims remain a separate model-compliance issue; the successful historical probe does not erase them.

The integration direction is to connect Murmur's existing scene commands and current scene state through explicit tools, with stable object IDs, validation, render receipts, and stale-operation handling. Choosing a regular text LLM in the requested STT -> LLM -> TTS architecture does not by itself supply those missing tools. The user's cascaded-pipeline preference remains unchanged. This follow-up inspected code and saved evidence and updated the log; it did not change runtime code or start a native-audio session.

## Cascaded pipeline requirement

On 2026-09-17, the user clarified that they want separate STT, a regular text LLM, and TTS rather than Gemini Live or another native speech-to-speech model. This changes the integration target. Streaming conversation and WebRTC remain appropriate transport choices; selecting a normal text model for reasoning is the important boundary.

The main review inspected Murmur's existing cascade implementations and the official architecture/session-start docs. The bounded SDK side review checked the current public source and generated schemas. No private workspace configuration, credentials, live inference, or vendor contact was used for this audit. A paper review was unnecessary because the unresolved question is Cosmo's current callable configuration.

| Evidence | Conclusion and limit |
| --- | --- |
| [Architecture guide](https://platform.askcosmo.ai/docs/architecture), checked 2026-09-17, says native speech or modular STT -> LLM -> TTS is selected per agent via `model` and `model_options`. | Vendor-documented support for a cascade. It does not name a runnable cascade model ID or demonstrate its stage configuration. |
| [Session-start reference](https://platform.askcosmo.ai/docs/reference/api/session-start) and [TypeScript types](https://platform.askcosmo.ai/docs/reference/typescript/types) expose `model`, four realtime provider option branches, and voice name/style. | No independent STT or TTS provider/model fields were found. `model` accepts a string, so the absence of a typed cascade branch is not proof that a server-selected cascade is impossible. |
| [Catalog agents](https://platform.askcosmo.ai/docs/concepts/agents#catalog-agents) launch stored workspace configuration. | A dashboard-configured cascade could be a valid route, but no such agent or available setting was verified in this account. Browser access was unavailable; the user was asked which pipeline settings or agent name appear there. |
| Installed SDK `cosmo-ai@0.6.0` and public source at `f448c5d0654f92865e8592aebed7de00b8725c78`. | The source review found no runnable public cascade example or supported non-native model selector. Arbitrary test model strings do not establish supported runtime values. |
| Local `backend/murmur/voice/provider_profiles/livekit_cascade.py` and `pipecat_cascade.py`. | Murmur already has separate Deepgram `nova-3`, Groq `openai/gpt-oss-120b`, and ElevenLabs `eleven_flash_v2_5` components. These are existing local alternatives, not claims about Cosmo's model availability or fresh live validation. |

Recommendation: retain the cascaded architecture as the requirement and integrate Cosmo through its documented non-native model configuration or an explicitly configured catalog agent once that contract is available. Reuse Murmur's existing stage boundaries. The other viable option is Murmur's direct-provider cascade; it is not a Cosmo-backed configuration and has not been substituted silently. Native audio, disabling only native audio output, and calling a text LLM as a background tool do not satisfy the requested architecture.

Risks and acceptance: a default or guessed alias could still select a native model; the three resolved stage identities must therefore be observable and unsupported selections must fail without native fallback. Separation alone does not fix premature endpointing. Validate mid-thought pauses, resumed speech cancelling pending completion, interruption stopping generation and queued TTS, correct canvas render receipts, and stale-result cleanup. Measure endpointing, text generation, synthesis, and device playout separately.

Concrete feedback request, prepared locally and not sent: “Your architecture guide describes a modular STT -> LLM -> TTS pipeline selected via model/model_options. Could you share a working TypeScript agent configuration or catalog-agent setup using a regular text LLM, including the supported model ID and resolved STT/TTS providers? Can those stages be chosen independently, or can we supply our own text-LLM endpoint? Please include the available endpointing and per-response interruption/cancellation controls.”

Current status, updated 2026-10-27: configuration discovery is complete for the available editor/API surfaces. The workspace presently offers no configurable STT -> ordinary text LLM -> TTS cascade. Integration and live acceptance require a new supported vendor contract. No new native inference session was started; the configuration-only temporary agent described below was deleted.

## Resolved model visibility

### Authenticated native-provider contract, 2026-10-27

**Verdict: neither Cosmo-native provider exposes the required cascade.** This is a definitive conclusion about this workspace's presently exposed editor and validated API contract, not a claim about every private backend implementation. PersonaPlex has a native voice configuration; Ultravox combines its speech model with selectable output voices, including external TTS. External TTS does not make Ultravox an independently selected STT -> ordinary text LLM -> TTS pipeline. No concrete ordinary text-LLM provider/model or STT provider/model can be selected in either branch.

The preceding lane established authenticated Chrome **Profile 1** access by importing `cosmo.ai` cookies into gstack browse. Its authenticated `GET /api/v1/external/agents` returned **HTTP 200, `[]`**; dashboard workspace and library lists were also empty. Its capability response was **`{"openai_provider_available":true}`**. These supersede the earlier auth-blocked discovery below. Use `https://platform.askcosmo.ai`, not the unreachable `api.cosmo.ai`. Internal workspace requests use `x-cosmo-workspace-id: wk_f3770c2a` and the existing browser authentication. Never copy cookie, key, or token values into evidence.

#### Editor controls and option schemas

Inspected `/wk-1f507574/realtime-agents/new` and the same editor after a temporary save. The model menu has Auto, Gemini Live, OpenAI Realtime, OpenAI Realtime Mini, OpenAI GPT Live, Grok Voice, **Cosmo Voice Light** (`cosmo_voice_personaplex`), and **Cosmo Voice** (`cosmo_voice_ultravox`). There is no ordinary OpenAI text-model choice in this menu.

| Surface | Observed controls / serialization |
| --- | --- |
| Agent identity | Display name, machine-safe handle, Create agent / Save; saved agents also expose Export code and Talk. |
| Agent setup | One model/provider menu, voice picker, delivery style, system prompt and expanded prompt editor. Delivery choices are Default, Warm, Delivery, and Human; the selection supplies `voice.speaking_style`, not a stage provider. |
| Opening / fixed speech | Assistant waits for user or speaks first, first-message text (`greeting`), silence-nudge text, seconds of silence (default 10), and max nudges (default 3). PersonaPlex disables the silence-nudge controls. Nudge configuration serializes as a `user.speech.timeout` hook with `say`, `max_count`, and `reset_mode: "on_user_speech"`. |
| Advanced prompt guidance | Unclear audio, Pacing & brevity, Language pinning, Entity capture, Safety & escalation switches append prompt sections. |
| Advanced model/audio | Max response tokens, Temperature, Max session duration (60–14400 seconds), Interruption sensitivity (default/low/high), End-of-turn delay (0–5 seconds), Background noise filter. Some generic controls remain visible even when their values are not serialized for the selected provider. |
| I/O | Structured input fields with Form/JSON modes and Add field. This is `structured_input_fields`, not speech input/STT configuration. |
| Skills / Tools / Logs | Add skill; End call switch and Add Tool; session logs. None supplies independent STT, ordinary text LLM, or TTS stages. |

The served editor's option map in `/_next/static/chunks/35m2p4gke355q.js` explicitly owns the following provider knobs. Its `patchForModelChoice` sets `modelProvider`, resets `model` to `null`, and clears the voice on provider changes. Its configuration builder emits the **internal** `model_options` shape, unlike the newer public inline-session model block.

| Provider | Editor-owned `model_options` properties | Live validation |
| --- | --- | --- |
| `cosmo_voice_personaplex` | `provider` only; no provider-specific knobs | `temperature`, `turn_endpoint_delay_seconds`, `model_id`, `stt`, `llm`, `tts`, `stt_provider`, `llm_provider`, `llm_model`, and `tts_provider` are `extra_forbidden`. |
| `cosmo_voice_ultravox` | `provider`, nullable numeric `temperature`, nullable numeric `turn_endpoint_delay_seconds` | Non-numeric values fail `float_parsing`; `temperature: -1` fails `greater_than_equal` with `ge: 0`; delay `6` fails `less_than_equal` with `le: 5`. Editor input validation bounds temperature to 0–2 and delay to 0–5. The same stage/model keys above are `extra_forbidden`. |

These are observed editor-owned fields plus live validation results, not a downloaded complete private OpenAPI schema. The public `docs/openapi/realtime.json` still omits both native providers from the inline `model` discriminated object, although session-record/usage provider enums include them. Its five public branches were recorded in the earlier table below.

#### Voice catalog is real, but does not select a text LLM

`GET /api/v1/realtime-playground/agents/voices?provider=<provider>` is sent when selecting a provider; both responses were HTTP 200. Voice response entries have `voice_id`, `name`, `description`, `gender`, `language_label`, `provider`, `ownership`, `external`, and `tunable`.

PersonaPlex returns **18** voices, all `external: null`, `tunable: false`: `NATF0.pt`–`NATF3.pt`, `VARF0.pt`–`VARF4.pt`, `NATM0.pt`–`NATM3.pt`, `VARM0.pt`–`VARM4.pt`. Their provider is `cosmo_voice_personaplex`; descriptions distinguish natural/conversational and varied/expressive voices.

Ultravox returns **195** voices, with provider labels `Inworld`, `Cartesia`, and `Eleven Labs`; **43** are marked tunable. The picker offers Provider default, language-grouped voices, and Tunable only. A tunable entry exposes a Tune voice button. Clicking it in this browser did not materialize additional controls, so no tuning mutation or successful tuning request is claimed. The returned `external` descriptors nevertheless expose exact TTS metadata:

| Voice | Returned `external` descriptor |
| --- | --- |
| Jacqueline_v2 | `provider: "cartesia"`, `provider_voice_id: "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"`, `model: "sonic-3.5"`, `tunable_knobs: ["speed"]` |
| Elise | `provider: "elevenlabs"`, `provider_voice_id: "EST9Ui6982FZPSi7gCHi"`, `model: "eleven_turbo_v2_5"`, `tunable_knobs: ["stability", "similarity_boost", "style", "use_speaker_boost", "speed"]` |
| Carmen | `voice_id: "bb256405-fe57-45c1-9d7c-4599dd43471d"`, `provider: "Inworld"`, `ownership: "public"`, `external: null`, `tunable: false`. No concrete TTS model is reported for this entry. |

These voice IDs are public catalog identifiers, not credentials. A TTS voice/model descriptor does not supply STT or regular text-LLM selection.

#### Saved configuration evidence

One temporary native agent, `contract-evidence`, was persisted during a create-request capture attempt: the first interception handled string fetch inputs but missed the platform client's `Request` object. A subsequent GET confirmed the PersonaPlex configuration below. The create body shape is independently visible in the served editor: `{"name": "contract-evidence", "display_name": "Cosmo contract evidence", "config": <config>, "workspace_id": null, "created_by": null}`. The create response/status was not captured; do not label it an intercepted or failed save.

The same agent was updated through the editor to Ultravox. **The actual PATCH request and HTTP 200 response were captured**, and the response echoed the exact model/voice/options. Both configurations use the same surrounding fields:

```json
{
  "instructions": "Configuration evidence only. Wait for the user.",
  "model": null,
  "model_options": {"provider": "cosmo_voice_personaplex"},
  "voice": {"name": "NATF0.pt", "speaking_style": null},
  "audio": {"output": true, "noise_cancellation": true},
  "greeting": null,
  "hooks": null,
  "structured_input_fields": null,
  "tool_ids": null,
  "server_tools": null,
  "skills": null,
  "max_session_seconds": null
}
```

The Ultravox `PATCH /api/v1/realtime-playground/agents/<temporary-agent-id>` body was `{"display_name":"Cosmo contract evidence","config":<the same surrounding fields>}`, with exactly these replacements:

```json
{
  "model_options": {
    "provider": "cosmo_voice_ultravox",
    "temperature": 0.4,
    "turn_endpoint_delay_seconds": 1.5
  },
  "voice": {
    "name": "bb256405-fe57-45c1-9d7c-4599dd43471d",
    "speaking_style": null
  }
}
```

Both the PersonaPlex GET and Ultravox PATCH response add `interruption_sensitivity: "default"`; the PATCH request omitted that default. Response metadata includes workspace/name/display name, IDs, ownership, timestamps, library flags, and `config`; private user/artifact IDs are intentionally omitted here. Neither response identifies an ordinary text-LLM model or separate STT engine. These saves prove configuration persistence, not inference or stage execution.

#### Reflected validation and session-start payloads

Two sets of deliberately invalid internal create requests used an empty handle so they could not create agents. One supplied `model_options: {"provider": <native-provider>, "temperature": "invalid", "turn_endpoint_delay_seconds": "invalid", "stt": {"provider":"probe"}, "llm": {"provider":"probe"}, "tts": {"provider":"probe"}}`. Both returned **HTTP 422**, reflecting the supplied body and errors:

```text
body.name: value_error (machine-safe handle required)
body.config.model_options.cosmo_voice_personaplex.{temperature,turn_endpoint_delay_seconds,stt,llm,tts}:
  extra_forbidden / Extra inputs are not permitted
body.config.model_options.cosmo_voice_ultravox.{temperature,turn_endpoint_delay_seconds}:
  float_parsing / Input should be a valid number
body.config.model_options.cosmo_voice_ultravox.{stt,llm,tts}:
  extra_forbidden / Extra inputs are not permitted
```

The second set established the numeric bounds and rejected the alternate flat stage/model keys listed above. Extra top-level stage keys in the **internal** config did not produce errors in that second response; no claim is made that internal config forbids every unknown field. Rejecting the provider-option fields, the editor's actual serialization, and the stricter public inline contract together establish the missing supported stage route.

The editor's **Talk** button uses platform SDK **0.7.1**, while Murmur remains pinned to **0.6.0**. After both saved provider selections, a Request-aware fetch interceptor captured Talk before network dispatch. Both serialized the same catalog launch:

```json
{
  "type": "session-config",
  "sdk": {"name": "cosmo-ai", "version": "0.7.1"},
  "agent": {"type": "catalog", "name": "contract-evidence", "tools": []},
  "session": {"store_recording": true}
}
```

`tools: []` above abbreviates the actual ride-along list, not the actual array: 11 client specs (`file_read`, `file_write`, `file_list`, `file_search`, `clipboard_read`, `clipboard_write`, `open_file`, `get_current_screen`, `end_session`, `cosmo_sdk_draw_box`, `cosmo_sdk_draw_point`) plus `web_search` and `examine_image`. Full client entries carry `kind`, `name`, `description`, and `parameters`. No tools executed. There were **no inline provider/stage overrides** in Talk; it launches the stored catalog configuration. `store_recording: true` was the platform request default, but interception prevented a session or recording. The synthetic interception response is not server acceptance or rejection evidence.

The actual start endpoint is **`POST /api/v1/external/realtime/session/start`**. With the platform's existing start-scoped credential held only in browser memory, `POST /api/v1/external/sessions` returned **HTTP 405, `{"detail":"Method Not Allowed"}`**. It is not the start route. A deliberately invalid request to the actual start route returned **HTTP 422**:

```json
{
  "type": "invalid-schema-probe",
  "agent": {
    "type": "inline",
    "model": {"provider": "cosmo_voice_ultravox"},
    "stt": {}, "llm": {}, "tts": {}
  }
}
```

The sanitized response was `error.type: "validation_error"`, with `body.type: literal_error` (must be `session-config`), `body.sdk: missing`, `body.agent.inline.model: union_tag_invalid` (only `gemini`, `openai`, `openai_mini`, `openai_live`, `grok` are valid object tags), and `body.agent.inline.{stt,llm,tts}: extra_forbidden`. Thus the public inline route supplies neither native option objects nor independent stages; the captured editor route for native agents is catalog launch. No arbitrary model-string alias was tested as a cascade.

#### Cleanup, authentication distinction, and next dependency

Deleted the sole temporary `contract-evidence` agent with **HTTP 200**. Fresh internal workspace-agent and library-agent GETs both returned **HTTP 200, `[]`**. The earlier authenticated external catalog **HTTP 200, `[]`** remains the lane-2 fact; cleanup did not reverify it with the same workspace-user credential. Reusing the platform Talk credential instead returned external-agents **HTTP 403** (acts-as-user required) and capabilities **HTTP 403** (missing `realtime:read`). Those are credential-scope refusals, not catalog or provider-availability changes. Plain-cookie requests to external start/sessions returned HTTP 401 before the existing Talk header was reused in-memory.

No temporary token/key was minted in this continuation, and none needs revocation. The previous lane's leaked credential was already revoked according to its handoff. No cookie/key/token values were printed or written; the borrowed header was cleared from browser memory, and navigation removed the capture hooks. No new inference session, frontend wiring, or vendor contact occurred. Verification consisted of live HTTP status/body evidence, editor controls, served configuration-building source, the persisted config echoes, intercepted Talk payloads, and successful cleanup.

**Next dependency:** a vendor-supported workspace-available cascade contract that names distinct STT, ordinary text LLM, and TTS stages. The architecture guide's generic cascade claim, OpenAI availability, and rich TTS catalog do not provide that contract. This lane ends with verified configuration evidence; the current native lab is not completion of the cascade requirement.

### Workspace configuration discovery, 2026-10-27

**Historical first-pass status: blocked on workspace-user authentication, not evidence of a Gemini-only workspace.** The authenticated continuation above supersedes this blocker and its next-step recommendation. The dedicated worktree's `.env` key was loaded in-process and used only as an HTTPS bearer credential. The key value was never emitted or saved in evidence. The following were real API requests, with no new inference session, token mint, agent mutation, or vendor message:

| Request | Result | Exact finding |
| --- | --- | --- |
| `GET https://platform.askcosmo.ai/api/v1/external/realtime/verify` | HTTP 200 | `credential: "api_key"`, `can_start_sessions: true`, `realtime_voice_available: true`. Relevant granted scopes: `realtime:start`, `realtime:read`, `realtime:logs`, `user_tokens:mint`. These scopes do not grant workspace-user catalog CRUD. |
| `GET https://platform.askcosmo.ai/api/v1/external/agents` | HTTP 403 | `{"error":{"type":"api_error","message":"This endpoint requires an acts-as-user access token"}}`. This is a credential-kind refusal, not an empty catalog. No workspace or built-in catalog handles were obtained. |
| `GET https://platform.askcosmo.ai/api/v1/external/sessions/capabilities` | HTTP 200 | `{"openai_provider_available":true}`. The current schema describes this as permission to select `openai`, `openai_mini`, and `openai_live`; it does not enumerate concrete model IDs or establish a cascaded inference route. |

The gstack browser had no authenticated Cosmo session: opening the dashboard path `/developer` redirected through `/select-workspace?tried_workspace=developer` to `/login`. A domain-scoped `cookie-import-browser chrome --domain platform.askcosmo.ai` imported zero cookies. No login code or broader cookie import was requested. The saved agents guide already states that `/api/v1/external/agents` authenticates as a workspace user rather than a realtime key; the live 403 confirms that distinction. Minting an external end-user token is not documented as granting workspace-user CRUD, so it was not used as an authorization substitute.

Re-read all four requested saved pages: `var/cosmo-home-2026-09-17.html`, `var/cosmo-reference-doc.html`, `var/cosmo-architecture-2026-09-17.html`, and `var/cosmo-agents-doc.html`. The home advertises pipelined STT -> LLM -> TTS, but its provider examples use only `model: "gemini"` and `model: "openai"`. The architecture guide claims cascade selection through `model`/`model_options` without a runnable ID. The agents guide shows only illustrative `support-triage`, explains the reserved `cosmo-` library prefix, and supplies no cascade handle. None of those examples proves an agent is available in this workspace.

Downloaded the current public [OpenAPI document](https://platform.askcosmo.ai/docs/openapi/realtime.json), linked by the [API explorer](https://platform.askcosmo.ai/docs/reference/api/explorer). It differs from the September SDK 0.6.0 contract: `InlineAgentConfig.model` now accepts a string or a discriminated provider block. Its `additionalProperties: false` object has **no `model_options` field**. This is current published schema evidence, not a live rejection test of the pinned SDK; compatibility of its old serialized request remains unverified after this discovery. Do not copy the newer wire shape into the pinned SDK without a version-specific adapter review.

The model-block properties in that exact document are:

| Schema / `provider` | Properties in addition to the required `provider` | Documented inference meaning |
| --- | --- | --- |
| `GeminiModel` / `gemini` | `model_id`, `cosmo_vad`, `end_of_speech_sensitivity`, `include_thoughts`, `max_output_tokens`, `prefix_padding_ms`, `silence_duration_ms`, `temperature`, `thinking_level`, `tool_response_overrides`, `tool_response_policy`, `turn_detection` | Gemini realtime; no distinct STT, ordinary text LLM, or TTS selection. |
| `OpenAIModel` / `openai` | `model_id`, `eagerness`, `prefix_padding_ms`, `silence_duration_ms`, `turn_detection` | OpenAI Realtime; no separate stage selector. |
| `OpenAIMiniModel` / `openai_mini` | `model_id` | OpenAI Realtime mini tier. |
| `OpenAILiveModel` / `openai_live` | `model_id`, `delegation`, `max_output_tokens`, `parallel_tool_calls`, `reasoning_effort`, `responses_instructions`, `responses_model`, `service_tier`, `tool_choice`, `verbosity` | GPT Live full-duplex voice with delegated Responses work. `responses_model` selects delegated reasoning/tool work, not replacement of the native voice model with an STT -> text LLM -> TTS pipeline. |
| `GrokModel` / `grok` | `model_id`, `idle_timeout_ms`, `prefix_padding_ms`, `reasoning_effort`, `silence_duration_ms`, `speed`, `turn_detection` | xAI Grok Voice. Public schema presence does not establish workspace entitlement. |

All five model objects forbid extra properties and require `provider`. Their optional `model_id` fields are strings; the document supplies no concrete model-ID allowlist. `VoiceConfig` forbids extra properties and exposes only `name` (maximum 128 characters) and `speaking_style` (maximum 8192 characters). There are no separate STT provider/model or ordinary text-LLM provider/model fields on the inline agent. `CatalogAgentConfig` requires `type: "catalog"` and `name`; accepted ride-alongs are `inputs`, `tools`, and `voice`, with stored `model` unavailable as an override.

The document also defines `TtsProvider` with enum `elevenlabs | cartesia` and `TtsVoice` with required `provider` and `voice_id`, optional `model`, `similarity_boost`, `speed`, `stability`, `style`, and `use_speaker_boost`. Its description explicitly says “External TTS providers a speech-to-speech model can render.” `VoiceConfig` does not reference `TtsVoice`, and none of the five published model blocks exposes it as a stage field. These schemas therefore do not supply a runnable cascaded request or establish ElevenLabs/Cartesia access for this workspace. No STT schema or cascade provider branch was found.

**Concrete model IDs:** the only resolved lab model ID remains the historical usage result `gemini-3.1-flash-live-preview` below. No fresh inference was started and no separate STT, text-LLM, or TTS model ID was resolved in this discovery. Provider aliases and model-block discriminators are not concrete model IDs. In particular, neither `openai_live.responses_model` nor native speech plus external TTS satisfies the requested architecture.

Local, ignored evidence is `var/cosmo-discovery-2026-10-27.json` (sanitized API results; SHA-256 `188a6e21e180b4ecab041f73234b7ee5ef9718d157ee83dd783743f1955b1070`) and `var/cosmo-openapi-2026-10-27.json` (public schema; SHA-256 `7c9e7965bbe0d6ce942cbcdd3d44d14ad2050daaea91a2de56ce022e28b29650`). The tables above preserve the relevant evidence in versioned documentation without relying on ignored artifacts.

Next dependency: an authenticated workspace-user dashboard session or supported acts-as-user token, then catalog enumeration and inspection of stored stage configuration. A vendor-documented cascade selector with its options and resolved stage identities would also unblock the configuration question. Until then the result is **no usable cascade discovered with available credentials**, not **workspace only offers native Gemini Live**. Cascade implementation and acceptance remain pending.

On 2026-09-13, read the usage records for two completed lab probe sessions through the documented `GET /api/v1/external/sessions/{id}/usage` endpoint, authenticated as the probe user. Both returned HTTP 200, `usage_status: recorded`, `provider: gemini`, and `model: gemini-3.1-flash-live-preview`. These are observed session resolutions, not a guarantee about future workspace defaults. Our agent selects Gemini options but leaves the concrete `model` and `voice` unset.

Google describes that model as [audio-to-audio with native audio output](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview). Cosmo's public [transcript contract](https://platform.askcosmo.ai/docs/concepts/transcripts) does not identify a separate transcription engine, and the usage schema does not report one or the resolved voice. Do not infer Deepgram, Whisper, or ElevenLabs from generic STT/TTS event names.

SDK suggestion: expose resolved model, voice, and effective turn-detector settings at session readiness, plus transcription provenance when a separate engine is used. The existing usage API already provides model/provider visibility; the proposed addition makes the remaining configuration observable during a live experiment. This is an observability request, not a confirmed defect or explanation for early replies.

## Application-owned turn detection and interruption

The user's next request is to own turn detection and interruption in Murmur, beyond tuning a silence window. Checked the installed `cosmo-ai@0.6.0` and current public upstream commit `f448c5d0654f92865e8592aebed7de00b8725c78` on 2026-09-13; that upstream package remains 0.6.0. This section is a reviewable integration request, not a claim that application-owned control is implemented.

Murmur has an existing Silero-backed speech detector in `web/src/hooks/use-vad.ts` and a native-audio Smart Turn analyzer in `backend/murmur/voice/smart_turn.py`. The desired flow is shared microphone capture, fast speech-start detection, semantic/acoustic completion checks during gaps, one committed user turn, and response-generation ownership. Speech resumption must invalidate pending completion checks. An interruption must stop local playback immediately, cancel the matching provider response, discard its queued audio, and reconcile the unheard assistant portion before a replacement response is admitted. A stopped microphone, expired call, or late classifier result must not commit a turn in a different call.

| Required boundary | Verified Cosmo public support |
| --- | --- |
| Own one input capture | `publishMicrophone: false`, readiness, then `startAudioStream(MediaStream)`; retain local capture ownership and publish a clone/derived stream so SDK teardown cannot destroy the detector's capture unexpectedly. |
| Own playback | `attachAudioElement()` accepts a host-owned audio element. Muting it is local playback containment, not provider response cancellation. |
| Commit a user turn | `sendActivityEnd()` emits `activity-end`. Its existence does not disable automatic endpointing. |
| Disable automatic endpointing | No exposed manual/none option in current Gemini or OpenAI model options. Omission retains defaults. |
| Cancel one response and acknowledge it | No public cancel/interrupt response method or outbound protocol variant. `end()` closes the whole session. |
| Clear queued audio and reconcile unheard context | No public per-response clear/truncate contract. `setOutputBlocked()` describes autoplay state only. |

The source boundaries are [model configuration](https://github.com/socratic-ai/cosmo-ai/blob/f448c5d0654f92865e8592aebed7de00b8725c78/typescript/src/core/agent.ts#L146), [session methods](https://github.com/socratic-ai/cosmo-ai/blob/f448c5d0654f92865e8592aebed7de00b8725c78/typescript/src/core/session.ts), [the outbound message union](https://github.com/socratic-ai/cosmo-ai/blob/f448c5d0654f92865e8592aebed7de00b8725c78/typescript/src/transport/envelope.ts#L60), and [custom audio publication](https://github.com/socratic-ai/cosmo-ai/blob/f448c5d0654f92865e8592aebed7de00b8725c78/typescript/src/transport/livekit_transport.ts#L415). The [turn-taking guide](https://platform.askcosmo.ai/docs/concepts/turn-taking) describes mute plus `activity-end` for push-to-talk, but does not explain exclusive manual endpointing during an open input interval. This is a contract/documentation gap; it is not proof that the backend can never support manual control. The [wire guide](https://platform.askcosmo.ai/docs/reference/wire-protocol) says unknown client message types are silently dropped, so delivery of an invented control message would prove nothing.

A concrete maintainer request, prepared locally and not sent: “Can the external realtime API expose a supported mode that disables automatic user-turn completion while audio continues streaming, with explicit activity-start/activity-end acknowledgements? We also need interruption of one identified assistant response, a terminal cancellation acknowledgement, output-buffer clearing, and a way to truncate its context to the audio actually played. Which of these are already supported server-side, and what is the documented TypeScript contract?”

Acceptance for an eventual adapter: silence may exceed the provider's usual endpoint window without starting a response until the host commits; an incomplete semantic decision holds while resumed speech invalidates it; genuine interruption stops playback, reaches terminal provider cancellation, and cannot replay old audio or complete old tools; a brief false detection follows a documented recovery policy; turn IDs and playback progress keep unheard context out of the next response; end/unmount closes capture, VAD/model work, and provider resources. Timers remain bounded fallbacks, not the semantic decision itself.

No local playback-only shim was added as a substitute for this ownership contract, and the running lab still uses Cosmo's managed semantic detector. The existing legacy Smart Turn session is not a drop-in adapter: it expects Deepgram callbacks, and its fallback/inference work needs explicit cancellation when new speech arrives. A supported provider handoff is required before wiring it as the authority for this Cosmo path.

## Other SDK findings

| Priority | Finding | Classification | Confidence |
| --- | --- | --- | --- |
| 1 | Ending during the start POST can leave a late room and microphone unowned | SDK correctness bug | High; reproduced with mocked browser/media dependencies |
| 2 | Plain tool specs bypass the builder's schema check despite documented equivalence | Documentation inconsistency and SDK preflight gap | High; reproduced locally; main integration saw HTTP 422 |
| 3 | Observer sessions report microphone permission as requesting/granted | SDK state bug | High; reproduced with the public transport factory |
| 4 | Background jobs cannot observe session closure through their job handle | Feature request | High for the missing API; benefit grounded in our board workflow |

**1. Make cancellation during startup prevent late connection and media acquisition.**

Murmur has an End action while connecting. If the application takes the early `onSession` handle and calls `end()` while the session-start POST is pending, the SDK reports a disconnected session. A successful late response nevertheless creates a room, connects, and enables the microphone. `start()` then rejects, but a second `end()` does not disconnect that room.

The installed `dist/chunk-UHC3AJPJ.mjs` explains the sequence: `_connectInner` awaits the POST and proceeds without a cancellation check (871–905); `LiveKitTransport.disconnect` returns while neither room nor audio element exists (1152–1154); `SessionEngine.teardown` clears its transport reference (3483–3486); the cancelled `start` catch skips another teardown (2555–2564), and shutdown is latched (2679–2682). The POST has no abort signal or timeout (1407–1411). [Official transport source](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/transport/livekit_transport.ts) and [session engine source](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/core/session_engine.ts).

Reproduction used the installed session engine and exact installed transport class, with networking, LiveKit, DOM, and wire helpers stubbed. Hold fetch's successful response, start with `publishMicrophone: true`, capture the session in `onSession`, await its `end()` after fetch begins, release fetch, await the rejected start, then call `end()` again. Observe the Room and microphone spies:

```text
fetch (signalPresent=false)
end resolved (state=disconnected)
release delayed successful POST response
Room.connect
setMicrophoneEnabled(true)
publishData
start rejected: NotReadyError: Session was ended before start completed.
second end resolved
Room.disconnect calls: 0
```

Suggested behavior: add `start({ signal })`, pass cancellation into auth/start fetch, check a cancellation latch after each setup await, and release any late-created transport resources before start settles. `end()`/`close()` during setup should establish the same guarantee. Test deferred POST, room join, and microphone acquisition separately; none may publish media after cancellation completes. A timeout must perform cleanup, not merely reject a raced promise.

Local handling is in [sdk.ts](../web/src/features/cosmo/sdk.ts): take the session in `onSession`, but defer its first `end()` until `start()` settles. This preserves the transport owner for cleanup. It does **not** provide immediate cancellation: a pending request can delay End, and microphone acquisition may occur briefly before deferred cleanup. The UI must stay ending until cleanup completes. The reproduction proves SDK control flow, not actual browser hardware behavior.

**2. Run the same dialect check for plain specs and builder-created tools.**

Our initial `pin_thought` and `read_thoughts` definitions used `additionalProperties: false`. That schema is invalid for Cosmo's restricted dialect: the installed README explicitly excludes strict objects (184–190). The SDK builder rejects it locally. We should use the supported dialect rather than request server acceptance of the original schema.

The gap is consistency: the README says raw parameters still receive a construction-time dialect check and describes the plain spec-object form as equivalent (197–200). In practice `client.agent({ tools: [spec] })` accepts the same invalid schema. `RealtimeAgent.start` passes plain parameters through `toWireTool`; the builder's `checkSchemaDialect` runs only through `tool()` (`dist/tool.mjs`, 49–57). Both public spec types expose `parameters: Record<string, unknown>` (`dist/realtime_client-DGwiayLq.d.mts`, 1266–1289). [Official README](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/README.md) and [tool builder](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/tool/index.ts).

Minimal provider-free reproduction, run from `web/` with Node ESM:

```js
import { RealtimeClient } from 'cosmo-ai';
import { tool } from 'cosmo-ai/tool';
const spec = {
  kind: 'client', name: 'read_thoughts', description: 'Read visible thoughts',
  parameters: { type: 'object', properties: {}, additionalProperties: false },
  handler: async () => ({ thoughts: [] }),
};
new RealtimeClient({ getAuthHeaders: () => ({}) }).agent({ tools: [spec] }); // accepts
tool(spec); // throws ToolSchemaError: forbidden_key
```

The main integration agent separately observed session/start HTTP 422, `invalid_tool_config`, naming `additionalProperties` on both tools. That live rejection was reported to this review, not independently replayed here. It failed before a call opened.

Suggested behavior: validate plain client specs at agent construction or start, with tool name and schema path, and document exactly which validation belongs to the builder. Clarify the README's equivalence claim and explain that invalid tool configuration can reject startup with HTTP 422 rather than appear in `ready.rejectedTools`. Local workaround: remove the unsupported keyword and construct raw-parameter specs through `tool({...})` to get preflight checks without adding a schema dependency.

**3. Preserve truthful microphone state when `publishMicrophone` is false.**

With `publishMicrophone: false`, a transport reporting no input stream, and no permission/media calls, the SDK emits `unknown → requesting → granted`; its snapshot ends at `mediaState.mic === 'granted'`. This was reproduced using `RealtimeClient({ getAuthHeaders: () => ({}), transportFactory })`, a no-op connected transport, and an `onSession` media listener. `SessionEngine.start` unconditionally writes the requesting and granted states (`dist/chunk-UHC3AJPJ.mjs`, 2506–2507 and 2558–2559), while `LiveKitTransport` skips microphone publication when false (901–906). [Official engine source](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/core/session_engine.ts).

Impact: consumers cannot derive microphone permission or publication truth from the snapshot in observer/text sessions. Suggested behavior: represent an unpublished/disabled microphone explicitly, or separate permission from publication state; do not claim permission was granted when it was never requested. Add an observer-session state test. Murmur already masks this with the requested microphone option in [controller.ts](../web/src/features/cosmo/controller.ts), so this is not blocking the lab.

**4. Give background jobs a cooperative cancellation signal.**

The background primitive is useful: `pin_thought` acknowledges immediately and completes only after our browser render receipt. The SDK correctly drops a terminal result after session closure. However, `ClientToolJobSink.close()` only sets a closed flag, and `ClientToolJob` exposes no `AbortSignal` (`dist/chunk-XFE2FGB3.mjs`, 259–286 and 293–360; public job type at `dist/realtime_client-DGwiayLq.d.mts`, 567–620). Handler work continues. This is documented behavior, not a violation of the current contract. [Official job source](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/core/client_tool_jobs.ts).

Suggested API: `job.signal`, aborted on session termination, plus explicit host cancellation by job ID with an ended/superseded reason. Handlers could pass it into fetch, reasoning tasks, or render waits. Do not cancel every job on ordinary speech interruption: background work intentionally survives continued conversation. Cancellation can stop cooperative work but cannot undo a completed external effect.

Local workaround exists in [thought-board.ts](../web/src/features/cosmo/thought-board.ts): call-scoped cancellation rejects pending render waits and blocks stale completion. A future reasoner/canvas adapter still needs task ownership and explicit supersession checks even if the SDK adds a signal.

**Already supported; do not ask Cosmo to add these again.** `start({ onSession })` installs listeners before connection begins; only `ready` and `lifecycle` replay to late event listeners. The async iterator buffers early wire events separately. Our adapter now uses `onSession`, so losing the greeting through late listener registration was an integration issue. The SDK also provides `waitUntilReady()`, `transportFactory` for provider-free tests, typed session-start errors, and browser-safe minted-token support. Keep feedback focused on the findings above. [Agent API](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/core/agent.ts).

**Follow-up: confirmed transcript grouping discrepancy under interruption.**

Classification: documentation inconsistency that led to a local integration bug; the SDK already exports a reducer that handles the observed sequence. Confidence is high: the main integration captured an allowlisted live wire trace, and this side review replayed all 42 events from the failing run through the installed SDK with a fake transport and no provider calls. The table below records that failing sequence; the earlier visible symptom remains in [failure-transcript.json](../var/cosmo-smoke/turn-taking/failure-transcript.json). [wire-events.json](../var/cosmo-smoke/turn-taking/wire-events.json) is overwritten by each probe and now contains the latest run, not an immutable copy of the original failure.

The normalized event type tells consumers to coalesce by `(turnId, role)` (`dist/core/events.d.mts`, 19–30), matching the [transcript concepts page](https://platform.askcosmo.ai/docs/concepts/transcripts). However, the engine has one shared `currentTurnId`, advances it on either role's `turn-complete`, and sets `append` from the immediately preceding transcript role (`dist/chunk-UHC3AJPJ.mjs`, 3111–3122, 3151–3163, 3173–3183). Those fields cannot reliably identify a speaker's complete utterance during overlapping speech.

The SDK's own reducer documentation explicitly warns against both `(turnId, role)` grouping and the `append` hint. It selects the most recent still-open bubble for the event's role; a final closes that bubble, and the next same-role event opens a new one. See installed `dist/core/transcript_reducer.d.mts`, 12–19, and `dist/core/transcript_fold.d.mts`, 4–19; [official reducer](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/core/transcript_reducer.ts) and [fold contract](https://github.com/socratic-ai/cosmo-ai/blob/typescript/v0.6.0/typescript/src/core/transcript_fold.ts).

The saved live sequence explains the failure without assuming a stale upstream transcript. Times are the probe's local receipt clock; T1/T2 are illustrative labels assigned during replay, not IDs present on the wire:

| Receipt time | Wire event | Normalized result and pre-fix UI effect |
| --- | --- | --- |
| 16175.4 ms | USER nonfinal: “A dragon who learns algebra. Take about a minute to tell the whole story.” | T1; opens user bubble. |
| 20555.0 ms | ASSISTANT `turn-complete` | Advances the shared ID to T2. |
| 20656.4 ms | USER cumulative final of that same text | T2; our old reducer creates a duplicate finalized user bubble. |
| 27627.2 ms | USER nonfinal: “Wait, stop the story. I changed my mind. Just say banana and nothing else.” | Still T2; our old reducer drops this because its T2 user bubble was finalized. |

Replaying the full capture into the pre-fix reducer exactly reproduced duplicate continuation text and the missing interruption. Replaying into `reduceTranscript` from `cosmo-ai/core/transcript_reducer` preserved one continuation bubble and the complete interruption, followed by the assistant's “banana.” Minimal normalized reproduction:

```js
import { reduceTranscript } from 'cosmo-ai/core/transcript_reducer';
const continuation = 'A dragon who learns algebra. Take about a minute to tell the whole story.';
const interruption = 'Wait, stop the story. I changed my mind. Just say banana and nothing else.';
const events = [
  { id: 'p', turnId: 'T1', role: 'user', text: continuation, isFinal: false, append: false },
  { id: 'f', turnId: 'T2', role: 'user', text: continuation, isFinal: true, append: false },
  { id: 'i', turnId: 'T2', role: 'user', text: interruption, isFinal: false, append: true },
];
const items = events.reduce((items, event) => reduceTranscript(items, event, 80), []);
// Expected text: [continuation, interruption].
// The old role:turnId grouping produced [continuation, continuation].
```

Suggested SDK/documentation change: reconcile the normalized event comments and concepts page with the exported reducer, explain that a speaker's final may cross another speaker's turn boundary, and point custom UIs to the shared folding helper. A new server-correlated utterance ID could make ownership explicit in a future protocol, but no SDK feature is required for the demonstrated local fix. Local mitigation: use the helper or equivalent grouping, keep append-nonfinal/replace-final text semantics, and preserve distinct finalized segments. Do not discard every nonfinal that reuses a finalized `turnId`.

Evidence limit: the earlier prefix “Please tell me a long story about” does not appear anywhere in the saved wire transcript. The grouping fix cannot restore text never received. Its absence is **not** established as a Cosmo/server bug; the main integration is investigating fixture publication, capture readiness, and greeting overlap. The raw trace also contains no final for the interruption before capture stops, so preserving its nonfinal words matters. A thought pause may span distinct transcription segments; preserving all received words is a better probe assertion than requiring one UI bubble.

Follow-up: after requiring a completed greeting plus quiet decoded audio before injecting speech, the live probe received the prefix and continuation, retained both correctly, and preserved the interruption. This supports treating the earlier missing prefix as an inconclusive fixture-startup observation rather than a reported SDK defect. It does not establish exactly why the earlier prefix was absent.

**Thought-summary setting and transcript boundaries.** `modelOptions: { provider: 'gemini', includeThoughts: false, ... }` is supported and serializes to `include_thoughts: false`. The type describes it as controlling streamed thought summaries, with an unspecified server default when omitted (`dist/realtime_client-DGwiayLq.d.mts`, 1350–1354; serialization at `dist/chunk-UHC3AJPJ.mjs`, 471–472). It is appropriate for a lab that does not display those summaries. [Type reference](https://platform.askcosmo.ai/docs/reference/typescript/types).

The official transcript contract separates `model-text`—which can contain unspoken planning or function-call narration—from `transcript`, intended to represent audio transcription. Murmur subscribes only to `transcript` for its conversation UI. Disabling thought summaries is not a proven fix for planning prose that is already emitted as `transcript` or spoken aloud; distinguish wire channel, recorded audio, and UI content before attributing that behavior. [Transcript channel guidance](https://platform.askcosmo.ai/docs/concepts/transcripts).
