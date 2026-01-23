/**
 * Simple notification sounds using Web Audio API
 * No external files needed - generates tones programmatically
 */

let audioContext: AudioContext | null = null;

function getAudioContext(): AudioContext {
  if (!audioContext) {
    audioContext = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
  }
  return audioContext;
}

/**
 * Play a simple tone
 */
function playTone(frequency: number, duration: number, type: OscillatorType = "sine", volume: number = 0.3) {
  try {
    const ctx = getAudioContext();
    const oscillator = ctx.createOscillator();
    const gainNode = ctx.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(ctx.destination);

    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency, ctx.currentTime);

    // Envelope: quick attack, sustain, quick release
    gainNode.gain.setValueAtTime(0, ctx.currentTime);
    gainNode.gain.linearRampToValueAtTime(volume, ctx.currentTime + 0.02);
    gainNode.gain.setValueAtTime(volume, ctx.currentTime + duration - 0.05);
    gainNode.gain.linearRampToValueAtTime(0, ctx.currentTime + duration);

    oscillator.start(ctx.currentTime);
    oscillator.stop(ctx.currentTime + duration);
  } catch (e) {
    console.warn("[Sounds] Failed to play tone:", e);
  }
}

/**
 * Connection ready sound - pleasant ascending two-tone chime
 */
export function playReadySound() {
  const ctx = getAudioContext();
  // Two quick ascending notes (like a "ding ding" ready chime)
  playTone(523.25, 0.15, "sine", 0.25); // C5
  setTimeout(() => {
    playTone(659.25, 0.2, "sine", 0.25); // E5
  }, 120);
}

/**
 * Session disconnected sound - descending tone
 */
export function playDisconnectSound() {
  // Two descending notes (gentle disconnect indication)
  playTone(440, 0.12, "sine", 0.2); // A4
  setTimeout(() => {
    playTone(349.23, 0.15, "sine", 0.2); // F4
  }, 100);
}

/**
 * Error sound - quick low buzz
 */
export function playErrorSound() {
  playTone(220, 0.2, "square", 0.15); // A3 square wave for buzzy effect
}
