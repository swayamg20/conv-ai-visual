# Voice AI - Roadmap & Ideas

A comprehensive list of issues, features, and ideas based on our discussions.

---

## 🔴 Priority 1: Core Stability & UX

### Issue #1: Interruption Handling
**Status:** Not Started  
**Impact:** Critical for natural voice UX

When user speaks while AI is responding (TTS playing), the system doesn't handle gracefully.

**Requirements:**
- [ ] Stop TTS playback immediately when user speaks
- [ ] Cancel pending TTS chunks from server
- [ ] Clear audio buffer queue on client
- [ ] Send `interrupt` event via datachannel
- [ ] Server cancels in-progress TTS/LLM streams
- [ ] Add context to LLM: "User interrupted your previous response"
- [ ] Debounce to avoid false interrupts from echo

---

### Issue #2: LLM → TTS Sentence Streaming
**Status:** Not Started  
**Impact:** Major latency reduction

Currently waits for full LLM response before starting TTS. Should stream sentence-by-sentence.

**Requirements:**
- [ ] Detect sentence boundaries in LLM stream
- [ ] Start TTS on first complete sentence while LLM continues
- [ ] Queue subsequent sentences for TTS
- [ ] Handle edge cases (incomplete sentences, lists, code)

---

### Issue #3: Unified Sessions (Voice ↔ Chat)
**Status:** Not Started  
**Impact:** Better UX, conversation continuity

Voice and chat modes have separate sessions. User can't switch mid-conversation.

**Requirements:**
- [ ] Single session store keyed by `user_id:session_id`
- [ ] Both `/chat` and voice consumer use same pipeline
- [ ] Seamless context when switching modes
- [ ] Handle concurrent access (user in voice, opens chat tab)

---

### Issue #4: Error Recovery & Reconnection
**Status:** Not Started  
**Impact:** Production readiness

WebRTC connections drop with no auto-recovery.

**Requirements:**
- [ ] Client-side auto-reconnect with exponential backoff
- [ ] Preserve session state across reconnects
- [ ] Graceful degradation (show error, offer retry)
- [ ] Server-side connection health monitoring

---

### Issue #5: Audio Echo Handling
**Status:** Partial (browser echoCancellation only)  
**Impact:** Voice quality

TTS output can feed back into mic causing loops.

**Requirements:**
- [ ] Server-side echo detection (compare TTS timing with incoming audio)
- [ ] Optional: mute mic during TTS playback
- [ ] Detect and suppress AI's own voice in transcription

---

## 🟡 Priority 2: Canvas Evolution

### Issue #6: Migrate to tldraw/Rich Canvas
**Status:** Not Started  
**Impact:** Core differentiator

Current canvas is raw `<canvas>` with basic shapes. Need rich widgets.

**Requirements:**
- [ ] Integrate tldraw as canvas engine
- [ ] Define custom shape types (widgets)
- [ ] AI generates tldraw-compatible JSON
- [ ] Support pan/zoom/selection
- [ ] Built-in multiplayer support

---

### Issue #7: Custom Widget System
**Status:** Not Started  
**Impact:** Rich visual content

Canvas should support structured content, not just shapes.

**Widget Types to Build:**
- [ ] `ProductCard` - image, title, price, rating
- [ ] `LocationCard` - map thumbnail, address, distance
- [ ] `PersonCard` - avatar, name, title, links
- [ ] `ImageCard` - image with caption
- [ ] `LinkCard` - URL with OG preview
- [ ] `ChartWidget` - mini bar/line/pie charts
- [ ] `TimelineNode` - date, title, description
- [ ] `ChecklistItem` - checkbox, text, status
- [ ] `CodeBlock` - syntax highlighted code
- [ ] `MapEmbed` - interactive map (Mapbox/Google)
- [ ] `PriceTag` - currency, amount, comparison

---

### Issue #8: Interactive Canvas Elements
**Status:** Not Started  
**Impact:** Two-way interaction

User can click/interact with canvas elements, AI responds to context.

**Requirements:**
- [ ] Click detection on canvas elements
- [ ] Send clicked element context to LLM
- [ ] "What's this?" / "Tell me more" interactions
- [ ] Hover tooltips
- [ ] Drag to reorder (for lists/comparisons)

---

### Issue #9: Canvas Persistence
**Status:** Not Started  
**Impact:** Session continuity

Canvas state is lost on refresh.

**Requirements:**
- [ ] Save canvas state to session
- [ ] Restore on reconnect
- [ ] Export as PNG/SVG/PDF
- [ ] "Show me what we discussed" → reconstruct from memory

---

### Issue #10: Canvas Validation & Error Handling
**Status:** Not Started  
**Impact:** Reliability

LLM sometimes generates invalid canvas operations.

**Requirements:**
- [ ] Validate coordinates/dimensions before rendering
- [ ] Fallback for malformed operations
- [ ] Error messaging to LLM for self-correction

---

## 🟢 Priority 3: Platform Features

### Issue #11: Move Client to JS Framework
**Status:** Not Started  
**Impact:** Maintainability, scalability

Single 950-line HTML file is unmaintainable.

**Requirements:**
- [ ] Scaffold React + Vite + TypeScript project
- [ ] Split into components: `VoicePanel`, `ChatPanel`, `Canvas`, `Header`
- [ ] Canvas renderer as separate module
- [ ] State management for sessions
- [ ] Tailwind or CSS modules
- [ ] Environment config for API endpoints
- [ ] Hot reload dev experience

---

### Issue #12: Web Browsing / Research Agent
**Status:** Idea  
**Impact:** Major capability expansion

AI can browse web, show results on canvas.

**Requirements:**
- [ ] Browser automation (Playwright/Puppeteer on server)
- [ ] Screenshot capture and display
- [ ] Data extraction from pages
- [ ] Search integration (Google/Bing API)
- [ ] Show progress on canvas: "Searching...", "Found 5 results..."

---

### Issue #13: Multimodal Input
**Status:** Idea  
**Impact:** Richer interaction

User can share images/screenshots for AI to analyze.

**Requirements:**
- [ ] Image upload via chat
- [ ] Screenshot paste
- [ ] Vision model integration (GPT-4V)
- [ ] AI annotates on canvas explaining image

---

### Issue #14: Code Execution Sandbox
**Status:** Idea  
**Impact:** Developer use case

AI writes and runs code, shows output.

**Requirements:**
- [ ] Sandboxed code execution (E2B, Modal, or custom)
- [ ] Support Python, JavaScript, SQL
- [ ] Output displayed on canvas
- [ ] Error handling with visual debugging

---

### Issue #15: Document/PDF Ingestion
**Status:** Idea  
**Impact:** Knowledge work use case

User uploads document, AI explains with visuals.

**Requirements:**
- [ ] PDF parsing
- [ ] Chunking and embedding
- [ ] Canvas shows document structure
- [ ] AI highlights and explains sections

---

## 🔵 Priority 4: Multiplayer & Collaboration

### Issue #16: Shared Sessions / Rooms
**Status:** Idea  
**Impact:** Collaboration use case

Multiple users in same voice+canvas room.

**Requirements:**
- [ ] Room creation with shareable link
- [ ] Voice mixing for multiple speakers
- [ ] Shared canvas state (via tldraw multiplayer)
- [ ] User cursors/presence indicators
- [ ] Speaker identification in transcript

---

### Issue #17: Recording & Playback
**Status:** Idea  
**Impact:** Async collaboration, content creation

Record entire session (voice + canvas), replay later.

**Requirements:**
- [ ] Record audio stream
- [ ] Record canvas operations with timestamps
- [ ] Playback UI with timeline scrubbing
- [ ] Export as video

---

### Issue #18: Canvas Streaming / Spectator Mode
**Status:** Idea  
**Impact:** Broadcasting, demos

Share live canvas link for others to watch.

**Requirements:**
- [ ] Read-only spectator mode
- [ ] Low-latency canvas sync
- [ ] Optional: spectator chat/reactions

---

## ⚪ Priority 5: Infrastructure & Polish

### Issue #19: Session Persistence (Redis)
**Status:** Not Started  
**Impact:** Production readiness

Sessions are in-memory, lost on server restart.

**Requirements:**
- [ ] Redis for session state
- [ ] Serialize LLMPipeline state
- [ ] TTL for inactive sessions
- [ ] Graceful session restoration

---

### Issue #20: Authentication
**Status:** Placeholder only  
**Impact:** Production readiness

`get_current_user_id` is a stub.

**Requirements:**
- [ ] JWT or session-based auth
- [ ] User identification for memory isolation
- [ ] Rate limiting per user

---

### Issue #21: Observability & Metrics
**Status:** Not Started  
**Impact:** Operations

No visibility into latency, errors, usage.

**Requirements:**
- [ ] Latency tracking per stage (STT, LLM, TTS)
- [ ] Error rate monitoring
- [ ] Usage metrics (messages, voice minutes)
- [ ] Health check endpoint
- [ ] Structured logging

---

### Issue #22: Rate Limiting
**Status:** Not Started  
**Impact:** Abuse prevention

No rate limits on endpoints.

**Requirements:**
- [ ] Per-user request limits
- [ ] Token/minute limits for voice
- [ ] Graceful 429 responses

---

## 💡 Use Case Ideas (for direction)

These aren't issues, but product directions to explore:

### Trip Planning
- Group voice room + shared canvas
- AI shows map, itinerary cards, price breakdowns
- Real-time updates as group discusses

### Research / Investigation
- "Tell me about this company"
- Canvas shows: company card, founders, funding timeline, competitor map
- AI does research visibly, not behind the scenes

### Shopping / Comparison
- "Find me a laptop for video editing under 1.5L"
- Products appear as cards, specs highlighted
- Voice: "what about battery?" → view updates

### Debugging / Problem Solving
- Paste error, AI parses and visualizes
- Stack trace as diagram
- Solution steps numbered on canvas

### Meeting Notes / Standup
- AI listens, captures action items
- Canvas shows task board updating live
- "Create follow-up for this" auto-generated

### Personal Finance
- "Where's my money going?"
- Expense charts, category breakdowns
- Budget suggestions visualized

### Content Creation
- "Help me write a pitch"
- Structure appears on canvas
- AI drafts sections, shows alternatives

---

## Summary Table

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| 1 | Interruption Handling | P1 | Not Started |
| 2 | LLM→TTS Sentence Streaming | P1 | Not Started |
| 3 | Unified Sessions | P1 | Not Started |
| 4 | Error Recovery & Reconnection | P1 | Not Started |
| 5 | Audio Echo Handling | P1 | Partial |
| 6 | Migrate to tldraw | P2 | Not Started |
| 7 | Custom Widget System | P2 | Not Started |
| 8 | Interactive Canvas Elements | P2 | Not Started |
| 9 | Canvas Persistence | P2 | Not Started |
| 10 | Canvas Validation | P2 | Not Started |
| 11 | Move Client to JS Framework | P3 | Not Started |
| 12 | Web Browsing Agent | P3 | Idea |
| 13 | Multimodal Input | P3 | Idea |
| 14 | Code Execution Sandbox | P3 | Idea |
| 15 | Document Ingestion | P3 | Idea |
| 16 | Shared Sessions / Rooms | P4 | Idea |
| 17 | Recording & Playback | P4 | Idea |
| 18 | Canvas Streaming | P4 | Idea |
| 19 | Session Persistence (Redis) | P5 | Not Started |
| 20 | Authentication | P5 | Placeholder |
| 21 | Observability & Metrics | P5 | Not Started |
| 22 | Rate Limiting | P5 | Not Started |

