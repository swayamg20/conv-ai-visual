# Create a Murmur launch video with Brag

## Purpose / Big Picture

The user asked to use latent-spaces/brag in this Murmur checkout. Deliver a shareable 22-second landscape video, a selected poster, editable composition, and concise share copy. Murmur is a voice-first AI tutor: a learner asks a question and the assistant explains it while drawing on a shared canvas. The video should make that interaction concrete using source-backed UI and the existing repository screenshot.

## Progress

- [x] 2026-09-19 23:19 IST: Read Brag, the workspace instructions, the current README, product vision, design language, marketing page, voice-session UI, voice orb, and existing screenshot.
- [x] 2026-09-19 23:20 IST: Installed Brag and its Hyperframes domain skills; verified Node 22, FFmpeg, and local Chrome. Selected polished tone, landscape, music/SFX, and no narration.
- [x] 2026-09-19 23:24 IST: Wrote the nine-question creative rubric, full storyboard, and composition brief.
- [x] 2026-09-19 23:28 IST: Built the deterministic Hyperframes composition with local fonts, original screenshot, music, sound effects, and precomputed music-reactive glow.
- [x] 2026-09-19 23:30 IST: Passed the final Hyperframes gate: zero runtime/layout/motion/contrast errors, 67/67 contrast checks, and 300 motion samples. Inspected all four scenes, final hold, transition boundaries, and intermediate square-stroke poses.
- [x] 2026-09-19 23:32 IST: Rendered the MP4, selected the settled 15.2-second actual-product frame, embedded it at frame zero, verified all 660 frames/full decoding and audio preservation, and prepared the share copy and editable project.

## Surprises & Discoveries

The checkout has unrelated staged and unstaged voice-runtime work. All generated media, tool dependencies, and verification evidence for this task will stay inside ignored var/brag-output/. This plan is the only planned tracked repository addition.

The default Python downloader failed certificate verification. The skill installer's supported --method git succeeded without disabling TLS verification.

Hyperframes 0.8.51 is available and its local renderer prerequisites pass. Doctor reports optional transcription, TTS, generated-music, and Docker services missing; these do not serve this run because it uses bundled audio and local Chrome. Its current final quality preset is delivery, superseding Brag's older high example.

The README screenshot is an existing repository asset, not evidence of a newly exercised live session. The animated voice-session illustration will be identified as a recreation in the source and delivery notes. No new latency, learning-outcome, availability, or provider-quality claim is implied.

The first check found brief text intersections during crossfades. Retimed outgoing scenes to finish before incoming content; the final layout audit has zero findings. Four nonblocking lint suggestions remain about splitting the four scene containers into sub-compositions. A single 22-second timeline is intentional, and browser verification confirms correct seeking and visibility.

The source screenshot exceeds Hyperframes' 2 MB HTML-inline threshold. It is served as a local asset and loads successfully; the project must be kept with its assets directory. The keyframes static inspector cannot resolve stroke targets obtained inside a DOM forEach, so intermediate rendered snapshots at 5.0, 5.35, and 5.8 seconds provide direct evidence of the draw animation.

The managed Studio preview is running at http://localhost:3002/#project/composition and returned HTTP 200 before export.

The first render chose a separate Chrome 152 cache entry and failed before capture with EACCES. Doctor/check/snapshot had already succeeded with the cached Puppeteer Chrome 148 binary. Verified that binary's --version and set HYPERFRAMES_BROWSER_PATH for the render command, leaving global browser files and permissions unchanged.

## Decision Log

2026-09-19, Codex: Use the user's request as authorization to complete the local video workflow. No publication or external message is part of the task.

2026-09-19, Codex: Use var/brag-output/ instead of Brag's default root directory because README requires generated artifacts under ignored var/. Do not modify application code, current work in progress, or existing assets.

2026-09-19, Codex: Choose a polished, warm chalkboard treatment and one clear example: a binomial expansion. Recreate the source UI with an illustrative square diagram, then show the actual screenshot. Use the existing Inter, JetBrains Mono, amber, sage, lavender, and chalk identity.

2026-09-19, Codex: Use a pinned Hyperframes 0.8.51 toolchain, one paused GSAP timeline, bundled Brag music, sparse sound effects, local fonts, and a 22-second 1920x1080 canvas at 30 fps. The user did not request narration.

## Outcomes & Retrospective

Completed the requested local Brag workflow. var/brag-output/brag.mp4 is a 22-second 1920×1080 H.264 video at 30 fps with stereo music/SFX, no narration, and a selected poster baked into frame zero. The final file is about 4.2 MB. brag.jpg, share-copy.txt, the storyboard, composition brief, local editable assets, and verification evidence are present. The Studio preview remains available at http://localhost:3002/#project/composition.

The delivered film uses Murmur's current product identity, an explicitly illustrated voice/canvas teaching moment, and an unmodified actual repository screenshot. Application code and the pre-existing staged/unstaged voice-runtime work were not edited. The only tracked workspace addition is this ExecPlan; generated deliverables are ignored under var/. Brag and its required Hyperframes skills were installed in the user's global Codex skills directory.

The main tooling lesson is to verify the actual binary used by export: the renderer's preferred Chrome cache differed from the working doctor/preview cache. A command-scoped HYPERFRAMES_BROWSER_PATH resolved the failure. Final verification proved 22 seconds and 660 frames survived poster insertion, the audio was preserved exactly, and the output decoded cleanly.

## Context and Orientation

The current app is described in README.md and docs/VISION.md. docs/DESIGN_LANGUAGE.md defines the chalkboard identity; web/src/app/globals.css and web/src/app/layout.tsx provide live colors and fonts. web/src/app/(marketing)/page.tsx contains the headline "Your AI tutor that draws while it talks". web/src/app/(app)/session/[agentId]/page.tsx implements the voice-and-canvas layout; web/src/components/voice-orb.tsx defines listening, processing, and speaking states. docs/assets/screenshot1.png supplies an actual binomial-theorem chat/canvas image.

Brag is installed at /Users/swayam.gupta/.codex/skills/brag/. Its SKILL.md and references define inspection, storyboarding, audio selection, composition, and delivery. Hyperframes domain skills under the same skills directory define the rendering contract. A composition is a local HTML project whose timed elements and paused animation timeline can be sampled deterministically to produce video frames.

## Plan of Work

First record the product story and scene timings in var/brag-output/brag-plan.md, then specify the source material and presentation in composition-brief.md. Scaffold composition/ and include a local copy of the source screenshot, fonts, GSAP, one bundled music bed, and a few motion-matched sound effects. Implement the four beats: a question needs a canvas; voice and drawing demonstrate a binomial identity; an actual Murmur screenshot anchors the demonstration; the brand and final invitation settle on screen.

Run Hyperframes check, repair any actual layout or contrast defects, and inspect snapshots for readable settled states and scene boundaries. Launch an optional local Studio preview for review. Render with the delivery quality preset. Extract a settled product frame, use it as brag.jpg and replace only the first MP4 frame with it. Verify dimensions, duration, streams, frame count, audio signal, first frame, representative frames, and final hold. Record the outcome and link the resulting artifacts.

## Concrete Steps

From repository root, use the installed skill files to author the creative documents and composition beneath var/brag-output/. Use npx --yes hyperframes@0.8.51 for reproducible CLI commands.

    npx --yes hyperframes@0.8.51 init composition --non-interactive --resolution landscape --skill brag

Run that scaffold from var/brag-output/ with HYPERFRAMES_SKIP_SKILLS=1 because the skills have already been installed. Run subsequent commands from var/brag-output/composition/.

    npx --yes hyperframes@0.8.51 check
    npx --yes hyperframes@0.8.51 snapshot --at 1.8,7.6,11.8,15.2,20.5,21.97
    npx --yes hyperframes@0.8.51 preview --background
    npx --yes hyperframes@0.8.51 render --quality delivery --output ../brag.mp4

Use FFmpeg to select a fully settled poster and replace only video frame zero, copying audio unchanged. Inspect with ffprobe and a full decode. Keep validation logs and snapshots in the output directory. Stop any task-owned preview process if it is not useful to leave running.

## Validation and Acceptance

Acceptance is a nonempty 22-second 1920x1080 H.264 MP4 at 30 fps with an audible music/SFX track, no narration, and no missing assets or runtime errors. Hyperframes check must complete its browser audits with zero errors. Snapshots must show correctly rendered brand copy, the source-backed demonstration, actual screenshot, and readable final brand hold. The selected poster must match the delivered dimensions and appear at frame zero without changing duration or frame count. share-copy.txt must be one to three specific, truthful sentences. The composition must be editable and rerunnable with pinned dependencies. This task does not change application behavior, so application tests are not a relevant verification gate.
