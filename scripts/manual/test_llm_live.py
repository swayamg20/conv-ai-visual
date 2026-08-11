#!/usr/bin/env python3
"""
Test script for LLM pipeline.
"""

import asyncio
import os

from funcs.llm_pipeline import LLMPipeline


async def test_llm_pipeline():
    """Test the LLM pipeline with a simple conversation."""

    # Check if API key is set
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable not set")
        print("Set it with: export OPENAI_API_KEY=your_key_here")
        return

    # Initialize pipeline
    print("Initializing LLM pipeline...")
    llm = LLMPipeline(
        model="gpt-4o-mini",
        system_prompt="You are a helpful voice assistant. Keep responses brief and conversational.",
    )
    print("✓ LLM pipeline initialized\n")

    # Create conversation context
    context = llm.create_conversation_context()

    # Test conversation
    test_messages = [
        "Hello! What's the weather like?",
        "Can you tell me a joke?",
        "What's 25 * 4?",
    ]

    for user_msg in test_messages:
        print(f"User: {user_msg}")

        try:
            context, assistant_response = await llm.process_user_input(
                context, user_msg, temperature=0.7
            )
            print(f"Assistant: {assistant_response}\n")
        except Exception as e:
            print(f"Error: {e}\n")
            break

    print(f"Conversation context length: {len(context)} messages")


if __name__ == "__main__":
    asyncio.run(test_llm_pipeline())
