# Voice AI — What We're Building

A real-time voice assistant that doesn't just talk back — it **thinks visually**.

You speak. It listens, reasons, calls tools, draws on a shared canvas, and animates its thinking — all in real time. Not a chatbot with a mic bolted on. A new interaction surface where voice, visuals, and agency converge.

## The Core Idea

Most voice assistants are invisible. You ask, you get an answer, it disappears. There's no workspace, no persistence, no way to *see* the AI think.

Voice AI flips this. Every conversation has a **live canvas** — an infinite visual surface where the AI places diagrams, cards, charts, and animations as it talks. When you ask "explain how DNS works", you don't just hear an explanation — you watch it build a diagram, animate packet flows, and label each step. When you say "compare these two laptops", product cards appear side by side with specs highlighted.

The voice isn't the product. The voice is the interface. The canvas is where the work happens.

## How It Works

A spoken sentence travels through a pipeline designed for low latency at every stage:

1. **WebRTC** captures audio from the browser
2. **Deepgram** transcribes speech in real time
3. **Silero VAD + Smart Turn Detection** figures out when you're actually done talking (not just pausing)
4. **LLM** (OpenAI / Gemini, swappable) reasons about what to do — respond, call a tool, draw something
5. **Tool execution** runs in a sandbox — the AI can fetch data, search, compute, draw on the canvas
6. **ElevenLabs TTS** streams the response back as speech, sentence by sentence (not waiting for the full response)
7. **Canvas updates** flow as SSE events — Rough.js renders hand-drawn-style shapes, GSAP animates them

You can interrupt mid-sentence. The system detects your voice via server-side VAD, cancels TTS playback, and pivots. Natural conversation, not turn-taking with a robot.

## What Makes This Different

**Voice + Canvas is an underexplored design space.** Text chat with AI is solved. Voice chat with AI exists. But voice chat where the AI *draws its thinking in real time on a shared workspace* — that's new territory.

A few things we care about:

- **Latency is everything.** Sentence-pipelined TTS, smart turn detection, and streaming at every stage. The goal is for the AI to feel present, not buffering.
- **Tools are first-class.** The AI doesn't just generate text — it calls tools, executes code, fetches live data, and renders results visually. Tools are stored in a database and executed in a RestrictedPython sandbox.
- **The canvas is not a gimmick.** It's the primary output surface. Rough.js gives it a hand-drawn aesthetic. GSAP animations make sequences feel intentional, not dumped. The LLM controls what appears, where, and when.
- **Observation over declaration.** We're building toward a Decision Intelligence layer — instead of asking users what they want, we infer it from how they hesitate, choose, and react. The real preference data is behavioral, not stated.

## Where This Is Going

The immediate roadmap is about making the canvas richer and the tools more capable:

- **Rich widgets** on canvas — product cards, map embeds, charts, timelines, code blocks
- **Interactive elements** — click a card to drill deeper, drag to reorder, hover for details
- **Image placement** — the AI finds and places relevant images while explaining
- **LLM routing** — different models for different tasks (fast classifier → heavy reasoner)
- **Live web search** — the AI researches visibly on the canvas, not behind the scenes

Longer term, the vision is **multiplayer voice + canvas rooms** — a group of people and an AI on a shared workspace, planning a trip, debugging a problem, brainstorming a pitch. The AI listens to the group, updates the canvas in real time, and reduces decision paralysis by modeling the group's actual preferences (not their stated ones).

## The Bet

The bet is that the next leap in AI interfaces isn't better text generation — it's **multimodal agency with spatial awareness**. An AI that can talk, listen, draw, animate, fetch, compute, and remember — all at once, all in real time, all on a shared surface.

We're building the infrastructure for that.
