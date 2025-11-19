from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.websockets import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from av import AudioFrame
import numpy as np
import os
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV1SocketClientResponse
import threading

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get Deepgram API key from environment
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "dea381e9d217d2451a3ef550b95b2735e58f101b")

class AudioProcessor(MediaStreamTrack):
    """
    Custom audio track to process incoming audio with Deepgram STT
    """
    kind = "audio"
    
    def __init__(self, track, websocket, loop):
        super().__init__()
        self.track = track
        self.websocket = websocket
        self.loop = loop  # Store the asyncio event loop
        self.deepgram_client = None
        self.dg_connection = None
        self.dg_context = None  # Store the context manager
        self.lock_exit = threading.Lock()
        self.exit = False
        self.connection_open = False
        self.frame_count = 0
        self.setup_deepgram()
        
    def setup_deepgram(self):
        """Initialize Deepgram connection"""
        try:
            # Create Deepgram client
            self.deepgram_client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
            
            # Create websocket connection context manager - use keyword arguments, not dict
            self.dg_context = self.deepgram_client.listen.v1.connect(
                model="nova-2",
                language="en-US",
                smart_format=True,
                encoding="linear16",
                sample_rate=48000,
                channels=1,
                interim_results=True,
                punctuate=True
            )
            
            # Enter the context manager to get the actual connection object
            self.dg_connection = self.dg_context.__enter__()
            
            # Set up event handl   ers (closures that capture self)
            def on_open(*args, **kwargs):
                print("✅ Deepgram connection opened")
                self.connection_open = True
            
            def on_message(*args, **kwargs):
                try:
                    # Get the message from args
                    message = args[0] if args else kwargs.get('result')
                    
                    # Debug: always log that we got a message
                    print(f"📨 Deepgram message received: {type(message)}")
                    
                    if message and hasattr(message, 'channel') and hasattr(message.channel, 'alternatives'):
                        transcript = message.channel.alternatives[0].transcript
                        print(f"   Transcript length: {len(transcript)}")
                        if len(transcript) > 0:
                            is_final = getattr(message, 'is_final', True)
                            speech_final = getattr(message, 'speech_final', False)
                            print(f"🎤 Transcript ({'FINAL' if is_final else 'interim'}): {transcript}")
                            
                            # Send to frontend - schedule in the asyncio event loop from thread
                            asyncio.run_coroutine_threadsafe(
                                self.websocket.send_text(json.dumps({
                                    "type": "transcript",
                                    "text": transcript,
                                    "is_final": is_final,
                                    "speech_final": speech_final
                                })),
                                self.loop
                            )
                        else:
                            print(f"   Empty transcript (silence)")
                    else:
                        print(f"   Message structure unexpected: {message}")
                        
                except Exception as e:
                    print(f"❌ Error in on_message: {e}")
                    import traceback
                    traceback.print_exc()
            
            def on_error(*args, **kwargs):
                error = args[0] if args else kwargs.get('error', 'Unknown error')
                print(f"❌ Deepgram error: {error}")
                self.connection_open = False
            
            def on_close(*args, **kwargs):
                print("🔴 Deepgram connection closed")
                self.connection_open = False
            
            self.dg_connection.on(EventType.OPEN, on_open)
            self.dg_connection.on(EventType.MESSAGE, on_message)
            self.dg_connection.on(EventType.ERROR, on_error)
            self.dg_connection.on(EventType.CLOSE, on_close)
            
            # Start listening thread (required per official docs)
            def listening_thread():
                try:
                    self.dg_connection.start_listening()
                except Exception as e:
                    print(f"❌ Error in listening thread: {e}")
            
            listen_thread = threading.Thread(target=listening_thread, daemon=True)
            listen_thread.start()
            
            print("⏳ Deepgram connection created, waiting for open event...")
            
            # Wait for connection to open (with timeout)
            import time
            timeout = 5
            start = time.time()
            while not self.connection_open and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            if self.connection_open:
                print("✅ Deepgram connection ready!")
            else:
                print("⚠️  Warning: Deepgram connection didn't open within timeout")
            
        except Exception as e:
            print(f"❌ Deepgram setup error: {e}")
            import traceback
            traceback.print_exc()
        
    async def recv(self):
        frame = await self.track.recv()
        
        # Convert audio frame to bytes and send to Deepgram
        try:
            self.lock_exit.acquire()
            should_exit = self.exit
            self.lock_exit.release()
            
            if should_exit:
                return frame
            
            if self.dg_connection and self.connection_open:
                # Get audio data from frame
                audio_array = frame.to_ndarray()
                
                # Debug: print format info and audio levels
                if self.frame_count == 0:
                    print(f"📊 Audio: dtype={audio_array.dtype}, shape={audio_array.shape}, rate={frame.sample_rate}Hz")
                    print(f"   Audio levels - Min: {audio_array.min()}, Max: {audio_array.max()}, RMS: {np.sqrt(np.mean(audio_array**2)):.2f}")
                    self.frame_count += 1
                elif self.frame_count % 50 == 0:
                    # Every 50 frames (~1 second), check audio levels
                    rms = np.sqrt(np.mean(audio_array**2))
                    print(f"📡 Frame {self.frame_count}: Audio RMS={rms:.2f}, Min={audio_array.min()}, Max={audio_array.max()}")
                    if rms < 10:
                        print(f"   ⚠️ AUDIO TOO QUIET! Mic may not be working or permission denied.")
                
                # Handle different audio formats
                # WebRTC typically sends float32 in range [-1.0, 1.0] or int16
                if audio_array.dtype == np.float32 or audio_array.dtype == np.float64:
                    # Apply gain (amplification) - increase volume by 10x
                    audio_array = audio_array * 10.0
                    # Clip to [-1.0, 1.0] range and convert to int16
                    audio_array = np.clip(audio_array, -1.0, 1.0)
                    audio_array = (audio_array * 32767).astype(np.int16)
                elif audio_array.dtype != np.int16:
                    # Convert other types to int16
                    audio_array = audio_array.astype(np.int16)
                else:
                    # Already int16, apply gain (amplify by 10x)
                    audio_array = np.clip(audio_array.astype(np.float32) * 10.0, -32768, 32767).astype(np.int16)
                
                # Handle stereo -> mono conversion
                if len(audio_array.shape) > 1:
                    # Shape is (channels, samples) or (samples, channels)
                    if audio_array.shape[0] < audio_array.shape[1]:
                        # Shape is (channels, samples) - transpose or just take first channel
                        if audio_array.shape[0] > 1:
                            # Multiple channels - average them
                            audio_array = audio_array.mean(axis=0).astype(np.int16)
                        else:
                            # Single channel - just flatten
                            audio_array = audio_array.flatten()
                    else:
                        # Shape is (samples, channels) - take first channel or average
                        if audio_array.shape[1] > 1:
                            audio_array = audio_array.mean(axis=1).astype(np.int16)
                        else:
                            audio_array = audio_array.flatten()
                
                # Convert to bytes (little-endian 16-bit PCM)
                audio_bytes = audio_array.tobytes()
                
                # Send to Deepgram - use send_media() per official docs
                try:
                    self.dg_connection.send_media(audio_bytes)
                    self.frame_count += 1
                except Exception as send_err:
                    if "1011" not in str(send_err):  # Don't spam on known error
                        print(f"❌ Error sending to Deepgram: {send_err}")
                    self.connection_open = False
                
        except Exception as e:
            print(f"❌ Error processing audio: {e}")
            import traceback
            traceback.print_exc()
        
        return frame
    
    def cleanup(self):
        """Cleanup Deepgram connection"""
        try:
            self.lock_exit.acquire()
            self.exit = True
            self.lock_exit.release()
            
            # Exit the context manager - this handles closing the connection
            if self.dg_context:
                try:
                    self.dg_context.__exit__(None, None, None)
                    print("✅ Deepgram connection closed")
                except Exception as e:
                    print(f"⚠️  Error closing Deepgram: {e}")
                    
        except Exception as e:
            print(f"❌ Error in cleanup: {e}")

# Store peer connections
peer_connections = {}

@app.get("/test")
async def test():
    return FileResponse("test.html", media_type="text/html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    pc = RTCPeerConnection()
    client_id = id(websocket)
    peer_connections[client_id] = pc
    audio_processor = None
    
    print(f"Client {client_id} connected")
    
    @pc.on("track")
    async def on_track(track):
        nonlocal audio_processor
        print(f"Track received: {track.kind}")
        
        if track.kind == "audio":
            print("Audio track received! Starting Deepgram processing...")
            # Get the current event loop
            loop = asyncio.get_event_loop()
            audio_processor = AudioProcessor(track, websocket, loop)
            
            # Process audio frames
            try:
                while True:
                    frame = await audio_processor.recv()
            except Exception as e:
                print(f"Audio processing ended: {e}")
            finally:
                if audio_processor:
                    audio_processor.cleanup()
        
        @track.on("ended")
        async def on_ended():
            print(f"Track {track.kind} ended")
            if audio_processor:
                audio_processor.cleanup()
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print(f"Connection state: {pc.connectionState}")
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            await pc.close()
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get('type')
            
            print(f"Received {msg_type}")
            
            if msg_type == 'offer':
                # Receive offer from client
                offer = RTCSessionDescription(
                    sdp=message['sdp']['sdp'],
                    type=message['sdp']['type']
                )
                
                await pc.setRemoteDescription(offer)
                print("Remote description set")
                
                # Create answer
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                print("Local description set")
                
                # Send answer back to client
                await websocket.send_text(json.dumps({
                    "type": "answer",
                    "sdp": {
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type
                    }
                }))
                print("Answer sent")
                
            elif msg_type == 'ice_candidate':
                candidate = message.get('candidate')
                if candidate:
                    print(f"Adding ICE candidate")
                    
    except WebSocketDisconnect:
        print(f"Client {client_id} disconnected")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if audio_processor:
            audio_processor.cleanup()
        await pc.close()
        if client_id in peer_connections:
            del peer_connections[client_id]
