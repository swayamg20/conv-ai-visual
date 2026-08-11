#!/usr/bin/env python3
"""
Test script for TTS pipeline.
"""

import asyncio
import os
from pathlib import Path

from funcs.tts_pipeline import TTSPipeline


async def test_tts_pipeline():
    """Test the TTS pipeline with sample text."""

    # Check if API key is set
    if not os.getenv("ELEVENLABS_API_KEY"):
        print("Error: ELEVENLABS_API_KEY environment variable not set")
        print("Set it with: export ELEVENLABS_API_KEY=your_key_here")
        return

    # Initialize pipeline
    print("Initializing TTS pipeline...")
    tts = TTSPipeline()
    print("✓ TTS pipeline initialized\n")

    # Get voice info
    try:
        voice = await tts.get_voice_info()
        print(f"Using voice: {voice.name}")
        print(f"Voice ID: {voice.voice_id}\n")
    except Exception as e:
        print(f"Could not fetch voice info: {e}\n")

    # Test synthesis
    test_texts = [
        "Hello! This is a test of the text to speech system.",
        "I can speak naturally and clearly.",
        "How are you doing today?",
    ]

    for i, text in enumerate(test_texts, 1):
        print(f"Test {i}: {text}")

        try:
            audio_bytes = await tts.text_to_speech(text)
            print(f"✓ Generated audio: {len(audio_bytes)} bytes")

            # Save to file
            output_file = f"test_tts_output_{i}.pcm"
            await asyncio.to_thread(Path(output_file).write_bytes, audio_bytes)
            print(f"✓ Saved to {output_file}")
            print(f"  Play with: ffplay -f s16le -ar 16000 -ac 1 {output_file}\n")

        except Exception as e:
            print(f"✗ Error: {e}\n")
            break

    # Test streaming
    print("\n--- Testing Streaming TTS ---")
    stream_text = "This is a streaming test to see how fast we can generate audio."
    print(f"Text: {stream_text}")

    try:
        chunks = []
        async for chunk in tts.text_to_speech_stream(stream_text):
            chunks.append(chunk)
            print(f"  Received chunk: {len(chunk)} bytes")

        total_bytes = sum(len(c) for c in chunks)
        print(f"✓ Streaming complete: {len(chunks)} chunks, {total_bytes} total bytes")

        # Save streamed audio
        output_file = "test_tts_stream.pcm"
        await asyncio.to_thread(Path(output_file).write_bytes, b"".join(chunks))
        print(f"✓ Saved to {output_file}\n")

    except Exception as e:
        print(f"✗ Streaming error: {e}\n")


if __name__ == "__main__":
    asyncio.run(test_tts_pipeline())
