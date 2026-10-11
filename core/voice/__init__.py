"""Offline voice pipeline for ZARS.

Each stage is a small independent object so it can be swapped or tested alone:

    audio_io  mic capture (16 kHz mono int16) + playback with a mute gate
    wakeword  openWakeWord detector, 80 ms frames
    vad       Silero endpointing: end-of-speech, max length, no-speech timeout
    stt       faster-whisper, local model directory only
    tts       Kokoro, streamed sentence by sentence
    orchestrator  the IDLE/LISTENING/TRANSCRIBING/THINKING/SPEAKING state machine

Nothing in here touches the network at runtime.
"""