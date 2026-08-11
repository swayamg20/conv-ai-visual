# Engineering follow-ups

Verified deferred work after the August 2026 modularization. This file lists known limitations, not completed work or speculative roadmap ideas.

## Move synchronous database I/O off the event loop

Repository calls use synchronous SQLModel sessions. That is simple and adequate for the current local/small-pilot shape, but calls made from async chat and voice flows can block under concurrent load.

Choose either bounded thread offloading at service boundaries or an async SQLAlchemy/SQLModel engine. Preserve repository ownership and add concurrent session tests before changing the access model.

## Introduce versioned schema migrations before production data evolves

Startup currently uses `SQLModel.metadata.create_all()`. It creates missing tables but cannot safely rename columns, transform data, or roll forward an existing schema.

Adopt a guarded, append-only migration mechanism before the first incompatible production schema change. Include backup/restore instructions and an upgrade test from the oldest supported schema.

## Add a credentialed end-to-end staging suite

CI proves contracts with fake providers and an in-memory database. It does not currently prove a real Firebase login, Deepgram stream, LLM response, TTS stream, and synchronized browser canvas in one environment.

Build a separately triggered staging suite with tightly scoped credentials, cost ceilings, captured latency, and cleanup. Keep it out of pull-request CI.

## Replace keyword-only resource retrieval when quality data justifies it

Agent resource retrieval currently relies on SQLite keyword matching. Evaluate embeddings or hybrid retrieval against a curated tutoring question set before adopting another storage service. Ship only if relevance gains are measured.

## Harden arbitrary tool execution before exposing authoring

Inline tools use RestrictedPython and bounded waiting, but they are not process or container isolation. Tool rows are therefore trusted-operator configuration. Any future user-authored tool feature requires a separate execution boundary, strict network policy, resource limits, and adversarial tests.
