"""
Test script for Mem0 Cloud memory integration.
Requires MEM0_API_KEY environment variable.
"""
import asyncio
import os
from funcs import LLMPipeline

async def test_memory():
    # Check for API key
    if not os.getenv("MEM0_API_KEY"):
        print("ERROR: Set MEM0_API_KEY environment variable")
        print("Get your key from: https://app.mem0.ai/")
        return
    
    # Initialize with memory enabled
    llm = LLMPipeline(
        user_id="test_user_1",
        enable_memory=True
    )
    
    print("=== Testing Memory Integration ===\n")
    
    # First conversation - establish some facts
    context = llm.create_conversation_context()
    
    print("User: My name is Swayam and I prefer dark mode interfaces")
    context, response = await llm.process_user_input(
        context, 
        "My name is Swayam and I prefer dark mode interfaces"
    )
    print(f"Assistant: {response}\n")
    
    print("User: I work as a software engineer and I love Python")
    context, response = await llm.process_user_input(
        context,
        "I work as a software engineer and I love Python"
    )
    print(f"Assistant: {response}\n")
    
    # Check what memories were stored
    print("=== Stored Memories ===")
    memories = llm.get_memories()
    for mem in memories:
        print(f"  - {mem}")
    
    # New conversation - should recall memories
    print("\n=== New Conversation (should recall memories) ===\n")
    
    # Create fresh context
    new_context = llm.create_conversation_context("What do you know about me?")
    
    print("User: What do you know about me?")
    new_context, response = await llm.process_user_input(
        new_context,
        "What do you know about me?"
    )
    print(f"Assistant: {response}\n")
    
    # Search specific memories
    print("=== Searching for 'programming' memories ===")
    results = llm.get_memories(query="programming", limit=3)
    for r in results:
        print(f"  - {r}")

if __name__ == "__main__":
    asyncio.run(test_memory())


