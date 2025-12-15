import os
import logging
from typing import Optional, AsyncIterator
from elevenlabs import AsyncElevenLabs
from elevenlabs.types import VoiceSettings

logger = logging.getLogger("tts-pipeline")


class TTSPipeline:
    """
    Handles Text-to-Speech pipeline with ElevenLabs.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Default: Rachel voice
        model_id: str = "eleven_turbo_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        use_speaker_boost: bool = True
    ):
        """
        Initialize TTS pipeline.
        
        Args:
            api_key: ElevenLabs API key (defaults to ELEVENLABS_API_KEY env var)
            voice_id: Voice ID to use for synthesis
            model_id: Model ID (eleven_turbo_v2_5 for low latency)
            stability: Stability setting (0.0 - 1.0)
            similarity_boost: Clarity/similarity boost (0.0 - 1.0)
            style: Style exaggeration (0.0 - 1.0)
            use_speaker_boost: Enable speaker boost for better quality
        """
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError("ElevenLabs API key not provided")
        
        self.client = AsyncElevenLabs(api_key=self.api_key)
        self.voice_id = voice_id
        self.model_id = model_id
        
        self.voice_settings = VoiceSettings(
            stability=stability,
            similarity_boost=similarity_boost,
            style=style,
            use_speaker_boost=use_speaker_boost
        )
        
        logger.info(f"TTS pipeline initialized with voice: {voice_id}, model: {model_id}")
    
    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None
    ) -> AsyncIterator[bytes]:
        """
        Convert text to speech with streaming output.
        
        Args:
            text: Text to synthesize
            voice_id: Override default voice ID
            model_id: Override default model ID
            
        Yields:
            Audio chunks as bytes
        """
        try:
            audio_stream = self.client.text_to_speech.convert(
                text=text,
                voice_id=voice_id or self.voice_id,
                model_id=model_id or self.model_id,
                voice_settings=self.voice_settings,
                output_format="pcm_16000"  # 16kHz PCM for WebRTC compatibility
            )
            
            async for chunk in audio_stream:
                yield chunk
                
        except Exception as e:
            logger.exception(f"Error in TTS streaming: {e}")
            raise
    
    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None
    ) -> bytes:
        """
        Convert text to speech and return complete audio.
        
        Args:
            text: Text to synthesize
            voice_id: Override default voice ID
            model_id: Override default model ID
            
        Returns:
            Complete audio as bytes
        """
        try:
            # ElevenLabs returns an async generator, so we need to collect all chunks
            audio_chunks = []
            audio_stream = self.client.text_to_speech.convert(
                text=text,
                voice_id=voice_id or self.voice_id,
                model_id=model_id or self.model_id,
                voice_settings=self.voice_settings,
                output_format="pcm_16000"
            )
            
            async for chunk in audio_stream:
                audio_chunks.append(chunk)
            
            return b''.join(audio_chunks)
            
        except Exception as e:
            logger.exception(f"Error in TTS conversion: {e}")
            raise
    
    async def get_available_voices(self):
        """
        Get list of available voices.
        
        Returns:
            List of voice objects
        """
        try:
            voices = await self.client.voices.get_all()
            return voices.voices
        except Exception as e:
            logger.exception(f"Error fetching voices: {e}")
            raise
    
    async def get_voice_info(self, voice_id: Optional[str] = None):
        """
        Get information about a specific voice.
        
        Args:
            voice_id: Voice ID to query (uses default if not provided)
            
        Returns:
            Voice information object
        """
        try:
            voice = await self.client.voices.get(voice_id or self.voice_id)
            return voice
        except Exception as e:
            logger.exception(f"Error fetching voice info: {e}")
            raise

