# Product Context — Deep Reference

## The Pitch (One Paragraph)
Voice AI Canvas is a platform where users create personalized AI agents through a conversational interface. Each agent gets three superpowers: background processing, web search, and code sandbox. Users describe who they are ("I'm a 7th class CBSE physics student"), the system builds an agent with the right prompt, gathers relevant resources (textbooks, papers), and creates a persistent companion that teaches via voice + real-time canvas visualization. The agent remembers across sessions, can quiz from loaded question banks, and exports summarized notes as PDF.

## Founder Context
- Solo founder building everything with Claude Code
- Previous direction: general-purpose voice AI with canvas
- New direction (March 2026): agent creation platform
- Values: ship fast, keep it simple, no over-engineering
- Aesthetic sensibility: "Murmur" design language, hand-drawn blackboard feel
- Decision Intelligence thinking is foundational but deferred

## Competitive Landscape (Implied)
- ChatGPT: text-first, no persistent canvas, no agent creation by users
- NotebookLM: document-based, no real-time canvas, no voice-first
- Cursor/Copilot: developer-focused, not education
- Khan Academy Khanmigo: education but no canvas, no agent creation
- Gap: voice + canvas + user-created domain-specific agents

## The v2 Agent Platform — Key Questions to Resolve
1. How does agent creation actually work? (conversational? form? hybrid?)
2. How are resources stored? (vector DB? file storage? embedded in prompt?)
3. What's the memory model per agent? (separate context per agent? shared user profile?)
4. How does the agent prompt get generated? (template + customization? full LLM generation?)
5. What's the minimum viable agent? (prompt + system instructions only? or must have resources?)
6. How do assessments work? (agent has access to question bank? generates questions from resources?)
7. What's the PDF export format? (canvas snapshots + text summaries? structured notes?)
8. Multi-agent: can agents talk to each other? Or strictly isolated?
9. Background agent: what can it do autonomously? When does it run?
10. Web search: real-time during conversation? Or pre-session resource gathering?

## Tech Debt & Gaps for v2
- No user auth system (needed for persistent agents)
- No file/resource storage beyond SQLite
- Scene Kit Phase 3 (visual polish) and Phase 4 (hardening) not started
- No PDF generation capability
- Web search not implemented
- Background agent infrastructure doesn't exist
- Sandbox is RestrictedPython only — may need something more capable
