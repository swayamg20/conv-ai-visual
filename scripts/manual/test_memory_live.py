"""
Test script for 4-layer memory architecture.
"""

import asyncio

from murmur.llm import LLMPipeline


async def test_memory():
    print("=" * 60)
    print("4-LAYER MEMORY ARCHITECTURE TEST")
    print("=" * 60)

    # Initialize pipeline with memory
    llm = LLMPipeline(user_id="swayam_test", enable_memory=True)

    # ========== Layer 4: User Profile (Canonical Identity) ==========
    print("\n[Layer 4] Setting user profile...")
    llm.set_user_profile(name="Swayam", timezone="IST")
    llm.add_user_preference("theme", "dark_mode")
    llm.add_user_preference("verbosity", "concise")
    llm.add_user_fact("occupation", "AI Engineer")
    llm.add_user_fact("language", "Python")

    profile = llm.get_user_profile()
    print(f"Profile: {profile}")

    # ========== Layer 1 + 3: Chat with Context + Semantic Memory ==========
    print("\n[Layer 1 + 3] Starting conversation...")

    # First message - establishes facts
    response = await llm.chat(
        "Hi! I prefer using FastAPI for my APIs and I love async programming."
    )
    print("User: Hi! I prefer using FastAPI for my APIs and I love async programming.")
    print(f"Assistant: {response}\n")

    # Second message - continues conversation
    response = await llm.chat("What frameworks would you recommend for a real-time voice app?")
    print("User: What frameworks would you recommend for a real-time voice app?")
    print(f"Assistant: {response}\n")

    # Third message
    response = await llm.chat("I'm building it with WebRTC for audio streaming.")
    print("User: I'm building it with WebRTC for audio streaming.")
    print(f"Assistant: {response}\n")

    # ========== Layer 3: Check Semantic Memory ==========
    print("\n[Layer 3] Searching semantic memories...")
    memories = llm.search_memories("programming preferences")
    for mem in memories[:5]:
        print(f"  - {mem.get('memory', mem)}")

    # ========== Layer 2: Generate and Save Episodic Summary ==========
    print("\n[Layer 2] Generating session summary...")
    summary = await llm.generate_session_summary()
    print(f"Summary: {summary}")

    # End session with summary
    llm.end_session(summary)
    print("Session ended and saved to episodic memory.")

    # ========== New Session - Test Memory Recall ==========
    print("\n" + "=" * 60)
    print("NEW SESSION - Testing Memory Recall")
    print("=" * 60)

    # New pipeline instance (simulates new session)
    llm2 = LLMPipeline(
        user_id="swayam_test",  # Same user
        enable_memory=True,
    )

    # This should recall from all memory layers
    response = await llm2.chat("What do you remember about me and my project?")
    print("\nUser: What do you remember about me and my project?")
    print(f"Assistant: {response}")

    # Check episodic memory
    print("\n[Layer 2] Past conversation summaries:")
    summaries = llm2.get_conversation_summaries(limit=3)
    for s in summaries:
        print(f"  - [{s.get('created_at', '')[:10]}] {s.get('summary', '')}")

    print("\n✅ Memory test complete!")


if __name__ == "__main__":
    asyncio.run(test_memory())
