#!/usr/bin/env python3

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from snac import SNAC
import soundfile as sf
import numpy as np

CODE_START_TOKEN_ID = 128257
CODE_END_TOKEN_ID = 128258
CODE_TOKEN_OFFSET = 128266
SNAC_MIN_ID = 128266
SNAC_MAX_ID = 156937
SNAC_TOKENS_PER_FRAME = 7

SOH_ID = 128259
EOH_ID = 128260
SOA_ID = 128261
BOS_ID = 128000
TEXT_EOT_ID = 128009


def build_prompt(tokenizer, description: str, text: str) -> str:
    """Build formatted prompt for Maya1."""
    soh_token = tokenizer.decode([SOH_ID])
    eoh_token = tokenizer.decode([EOH_ID])
    soa_token = tokenizer.decode([SOA_ID])
    sos_token = tokenizer.decode([CODE_START_TOKEN_ID])
    eot_token = tokenizer.decode([TEXT_EOT_ID])
    bos_token = tokenizer.bos_token
    
    formatted_text = f'<description="{description}"> {text}'
    
    prompt = (
        soh_token + bos_token + formatted_text + eot_token +
        eoh_token + soa_token + sos_token
    )
    
    return prompt


def extract_snac_codes(token_ids: list) -> list:
    """Extract SNAC codes from generated tokens."""
    try:
        eos_idx = token_ids.index(CODE_END_TOKEN_ID)
    except ValueError:
        eos_idx = len(token_ids)
    
    snac_codes = [
        token_id for token_id in token_ids[:eos_idx]
        if SNAC_MIN_ID <= token_id <= SNAC_MAX_ID
    ]
    
    return snac_codes


def unpack_snac_from_7(snac_tokens: list) -> list:
    """Unpack 7-token SNAC frames to 3 hierarchical levels."""
    if snac_tokens and snac_tokens[-1] == CODE_END_TOKEN_ID:
        snac_tokens = snac_tokens[:-1]
    
    frames = len(snac_tokens) // SNAC_TOKENS_PER_FRAME
    snac_tokens = snac_tokens[:frames * SNAC_TOKENS_PER_FRAME]
    
    if frames == 0:
        return [[], [], []]
    
    l1, l2, l3 = [], [], []
    
    for i in range(frames):
        slots = snac_tokens[i*7:(i+1)*7]
        l1.append((slots[0] - CODE_TOKEN_OFFSET) % 4096)
        l2.extend([
            (slots[1] - CODE_TOKEN_OFFSET) % 4096,
            (slots[4] - CODE_TOKEN_OFFSET) % 4096,
        ])
        l3.extend([
            (slots[2] - CODE_TOKEN_OFFSET) % 4096,
            (slots[3] - CODE_TOKEN_OFFSET) % 4096,
            (slots[5] - CODE_TOKEN_OFFSET) % 4096,
            (slots[6] - CODE_TOKEN_OFFSET) % 4096,
        ])
    
    return [l1, l2, l3]


def main():
    
    # Load the best open source voice AI model
    print("\n[1/3] Loading Maya1 model...")
    model = AutoModelForCausalLM.from_pretrained(
        "/Users/swayam.gupta/Downloads/maya-model", 
        torch_dtype=torch.bfloat16, 
        device_map="auto",
        trust_remote_code=True
    )
    tokenizer = AutoTokenizer.from_pretrained(
        "/Users/swayam.gupta/Downloads/maya-model",
        trust_remote_code=True
    )
    print(f"Model loaded: {len(tokenizer)} tokens in vocabulary")
    
    # Load SNAC audio decoder (24kHz)
    print("\n[2/3] Loading SNAC audio decoder...")
    snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval()
    if torch.cuda.is_available():
        snac_model = snac_model.to("cuda")
    print("SNAC decoder loaded")
    
    # Design your voice with natural language
    description = "Realistic male voice in the 30s age with american accent. Normal pitch, warm timbre, conversational pacing."
    text = "Hello! This is Maya1 <laugh_harder> the best open source voice AI model with emotions."
    
    print("\n[3/3] Generating speech...")
    print(f"Description: {description}")
    print(f"Text: {text}")
    
    # Create prompt with proper formatting
    prompt = build_prompt(tokenizer, description, text)
    
    # Debug: Show prompt details
    print(f"\nPrompt preview (first 200 chars):")
    print(f"   {repr(prompt[:200])}")
    print(f"   Prompt length: {len(prompt)} chars")
    
    # Generate emotional speech
    inputs = tokenizer(prompt, return_tensors="pt")
    print(f"   Input token count: {inputs['input_ids'].shape[1]} tokens")
    device = next(model.parameters()).device  # Get model's device (mps/cuda/cpu)
    inputs = {k: v.to(device) for k, v in inputs.items()}
    print(f"   Input device: {device}")
    
    with torch.inference_mode():
        outputs = model.generate(
            **inputs, 
            max_new_tokens=2048,  # Increase to let model finish naturally
            min_new_tokens=28,  # At least 4 SNAC frames
            temperature=0.4, 
            top_p=0.9, 
            repetition_penalty=1.1,  # Prevent loops
            do_sample=True,
            eos_token_id=CODE_END_TOKEN_ID,  # Stop at end of speech token
            pad_token_id=tokenizer.pad_token_id,
        )
    
    # Extract generated tokens (everything after the input prompt)
    generated_ids = outputs[0, inputs['input_ids'].shape[1]:].tolist()
    
    print(f"Generated {len(generated_ids)} tokens")
    
    # Debug: Check what tokens we got
    print(f"   First 20 tokens: {generated_ids[:20]}")
    print(f"   Last 20 tokens: {generated_ids[-20:]}")
    
    # Check if EOS was generated
    if CODE_END_TOKEN_ID in generated_ids:
        eos_position = generated_ids.index(CODE_END_TOKEN_ID)
        print(f" EOS token found at position {eos_position}/{len(generated_ids)}")
    
    # Extract SNAC audio tokens
    snac_tokens = extract_snac_codes(generated_ids)
    
    print(f"Extracted {len(snac_tokens)} SNAC tokens")
    
    # Debug: Analyze token types
    snac_count = sum(1 for t in generated_ids if SNAC_MIN_ID <= t <= SNAC_MAX_ID)
    other_count = sum(1 for t in generated_ids if t < SNAC_MIN_ID or t > SNAC_MAX_ID)
    print(f"   SNAC tokens in output: {snac_count}")
    print(f"   Other tokens in output: {other_count}")
    
    # Check for SOS token
    if CODE_START_TOKEN_ID in generated_ids:
        sos_pos = generated_ids.index(CODE_START_TOKEN_ID)
        print(f"   SOS token at position: {sos_pos}")
    else:
        print(f"   No SOS token found in generated output!")
    
    if len(snac_tokens) < 7:
        print("Error: Not enough SNAC tokens generated")
        return
    
    # Unpack SNAC tokens to 3 hierarchical levels
    levels = unpack_snac_from_7(snac_tokens)
    frames = len(levels[0])
    
    print(f"Unpacked to {frames} frames")
    print(f"   L1: {len(levels[0])} codes")
    print(f"   L2: {len(levels[1])} codes")
    print(f"   L3: {len(levels[2])} codes")
    
    # Convert to tensors
    device = "cuda" if torch.cuda.is_available() else "cpu"
    codes_tensor = [
        torch.tensor(level, dtype=torch.long, device=device).unsqueeze(0)
        for level in levels
    ]
    
    # Generate final audio with SNAC decoder
    print("\n[4/4] Decoding to audio...")
    with torch.inference_mode():
        z_q = snac_model.quantizer.from_codes(codes_tensor)
        audio = snac_model.decoder(z_q)[0, 0].cpu().numpy()
    
    # Trim warmup samples (first 2048 samples)
    if len(audio) > 2048:
        audio = audio[2048:]
    
    duration_sec = len(audio) / 24000
    print(f"Audio generated: {len(audio)} samples ({duration_sec:.2f}s)")
    
    # Save your emotional voice output
    output_file = "output.wav"
    sf.write(output_file, audio, 24000)
    print(f"\nVoice generated successfully!")


if __name__ == "__main__":
    main()
