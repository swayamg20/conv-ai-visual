`uvicorn main:app --reload`
`uvicorn main:app --host localhost --port 3000 --reload`
webrtc based realtime pipeline:

STT -> Turn Detection (VAD) -> LLM -> TTS

VAD -> 
TEN Turn Detection (https://github.com/TEN-framework/ten-turn-detection?tab=readme-ov-file#license)
TEN VAD (https://github.com/TEN-framework/ten-vad?tab=readme-ov-file)