"""Per-stage checks for the offline voice pipeline.

Run each stage on its own so failures are easy to localise:

    python test_voice.py mic        # list input/output devices, record 3s
    python test_voice.py wake       # score live mic frames against the wake word
    python test_voice.py vad        # VAD speech/silence decisions on live mic
    python test_voice.py stt        # transcribe a wav (--wav FILE) or the live mic
    python test_voice.py tts        # speak one sentence
    python test_voice.py tts-stream # streamed sentence playback (timing shown)
    python test_voice.py agent      # mic -> STT -> LLM -> TTS, one full cycle
    python test_voice.py all        # everything above except `agent`

Everything is offline. Paths come from config.json.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config


def _cfg():
    return Config()


def _hr(title):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def test_mic(cfg, seconds=3):
    import sounddevice as sd

    _hr("MIC")
    print("--- input devices ---")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}  in={d['max_input_channels']} "
                  f"sr={int(d['default_samplerate'])}")
    print("--- output devices ---")
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            print(f"  [{i}] {d['name']}  out={d['max_output_channels']}")

    from core.voice.audio_io import MicSource

    mic = MicSource(sample_rate=cfg.voice_sample_rate,
                    device=cfg.voice_mic_device)
    print(f"\nrecording {seconds}s from device {cfg.voice_mic_device!r} ...")
    n = 0
    peak = 0
    for frame in mic.stream():
        n += 1
        peak = max(peak, int(np.abs(frame.astype(np.int32)).max()))
        if n * mic.block_ms >= seconds * 1000:
            break
    mic.close()
    peak_db = 20 * np.log10(max(peak, 1) / 32768)
    print(f"got {n * mic.block_ms / 1000:.1f}s, peak {peak} ({peak_db:.1f} dBFS)")
    print("RESULT: mic OK" if peak > 500 else
          "RESULT: mic opened but SILENT (check input device / permissions)")


def test_wake(cfg, seconds=10):
    import time as _t

    _hr("WAKE WORD")
    from core.voice.audio_io import MicSource
    from core.voice.wakeword import WakeWordDetector

    w = WakeWordDetector(cfg.voice_wakeword_model, cfg.voice_wakeword_threshold,
                         cfg.voice_cooldown_s)
    print(f"model   : {cfg.voice_wakeword_model}")
    print(f"keys    : {w.prediction_keys}")
    print(f"threshold: {w.threshold}   cooldown: {w.cooldown_s}s")
    mic = MicSource(sample_rate=cfg.voice_sample_rate, device=cfg.voice_mic_device)
    print(f"\nlistening {seconds}s -- say the wake word out loud")
    hi, n = 0.0, 0
    t0 = _t.monotonic()
    for frame in mic.stream():
        s = w.predict(frame)
        hi = max(hi, s)
        n += 1
        if s >= w.threshold:
            print(f"  *** FIRED at {n * mic.block_ms / 1000:.1f}s  score={s:.3f}")
        if _t.monotonic() - t0 >= seconds:
            break
    mic.close()
    print(f"peak score {hi:.3f} over {n} frames")
    print("RESULT: fired" if hi >= w.threshold else
          f"RESULT: never fired (peak {hi:.3f} < {w.threshold}); lower voice_wakeword_threshold")


def test_vad(cfg, seconds=10):
    import time as _t

    _hr("VAD")
    from core.voice.audio_io import MicSource
    from core.voice.vad import SileroVad

    v = SileroVad(cfg.voice_vad_model, cfg.voice_vad_threshold,
                  cfg.voice_silence_timeout_s)
    mic = MicSource(sample_rate=cfg.voice_sample_rate, device=cfg.voice_mic_device)
    print(f"threshold {v.threshold}, silence_end {v.silence_end_s}s")
    print(f"listening {seconds}s -- speak normally")
    t0, n, hits = _t.monotonic(), 0, 0
    for frame in mic.stream():
        n += 1
        s = v.is_speech(frame.astype(np.float32) / 32768.0)
        hits += int(s)
        if s:
            print(f"  speech @ {n * mic.block_ms / 1000:.1f}s")
        if _t.monotonic() - t0 >= seconds:
            break
    mic.close()
    print(f"speech windows: {hits}/{n}")
    print("RESULT: vad OK" if hits else "RESULT: no speech detected")


def test_stt(cfg, wav=None, seconds=6):
    import soundfile as sf

    _hr("STT")
    from core.voice.stt import SttEngine

    stt = SttEngine(cfg.voice_whisper_model, cfg.voice_whisper_device,
                     cfg.voice_whisper_compute_type)
    print(f"model: {cfg.voice_whisper_model}")
    print(f"device={cfg.voice_whisper_device} compute={cfg.voice_whisper_compute_type}")
    t0 = time.monotonic()
    stt.load()
    print(f"loaded in {time.monotonic() - t0:.1f}s")

    if wav:
        audio, sr = sf.read(wav, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        from core.voice.audio_io import _resample
        audio = _resample(audio, sr, 16000)
        print(f"\ntranscribing {wav} ({len(audio) / sr:.1f}s)")
    else:
        from core.voice.audio_io import MicSource
        from core.voice.vad import SileroVad

        v = SileroVad(cfg.voice_vad_model, cfg.voice_vad_threshold,
                      cfg.voice_silence_timeout_s)
        mic = MicSource(sample_rate=cfg.voice_sample_rate, device=cfg.voice_mic_device)
        print(f"\nrecording up to {seconds}s -- speak now")
        audio = mic.record_until_speech(v, max_record_s=cfg.voice_max_record_s,
                                         no_speech_timeout_s=seconds)
        mic.close()
        if audio is None:
            print("RESULT: no speech captured")
            return

    t0 = time.monotonic()
    text, lang = stt.transcribe(audio)
    print(f"transcribed in {time.monotonic() - t0:.1f}s (lang={lang})")
    print(f"TEXT: {text!r}")
    print("RESULT: stt OK" if text else "RESULT: empty transcript")


def test_tts(cfg, text="Hello. This is Zars speaking offline.", wav=None):
    _hr("TTS")
    from core.voice.audio_io import Speaker
    from core.voice.tts import KokoroTts

    tts = KokoroTts(cfg.voice_kokoro_model, cfg.voice_kokoro_voices,
                    cfg.voice_kokoro_voice, cfg.voice_kokoro_speed)
    t0 = time.monotonic()
    tts.load()
    print(f"loaded in {time.monotonic() - t0:.1f}s")
    print(f"voice: {cfg.voice_kokoro_voice}  speed: {cfg.voice_kokoro_speed}")

    samples, sr = tts.synthesize(text)
    print(f"synthesized {len(samples) / sr:.2f}s audio @ {sr} Hz "
          f"(peak {float(np.abs(samples).max()):.3f})")
    if wav:
        import soundfile as sf
        sf.write(wav, samples, sr)
        print(f"saved: {wav}  -> feed it back with: python test_voice.py stt --wav {wav}")
    else:
        Speaker(device=cfg.voice_speaker_device).play(samples, sr)
    print("RESULT: tts OK")


def test_tts_stream(cfg):
    _hr("TTS (streamed)")
    import asyncio

    from core.voice.tts import KokoroTts, split_sentences

    text = ("Zars is running fully offline. "
            "Wake word detection happens locally. "
            "Speech recognition uses faster whisper. "
            "This sentence should start speaking before the last one is synthesized.")
    sents = split_sentences(text)
    print(f"{len(sents)} sentences:")
    for i, s in enumerate(sents, 1):
        print(f"  {i}. {s}")

    tts = KokoroTts(cfg.voice_kokoro_model, cfg.voice_kokoro_voices,
                    cfg.voice_kokoro_voice, cfg.voice_kokoro_speed)
    tts.load()
    k = tts._kokoro

    async def run():
        t0 = time.monotonic()
        for sentence in sents:
            first = None
            async for samples, sr in k.create_stream(sentence, voice=cfg.voice_kokoro_voice,
                                                     speed=cfg.voice_kokoro_speed, lang="en-us"):
                if first is None:
                    first = time.monotonic()
                    print(f"  first chunk for: {sentence[:45]}")
                print(f"     chunk @ {time.monotonic() - t0:5.2f}s "
                      f"({len(samples) / sr:.2f}s audio)")
    asyncio.run(run())
    print("RESULT: stream OK")


def test_agent(cfg):
    _hr("FULL CYCLE (mic -> STT -> LLM -> TTS)")
    from rich.console import Console

    from core.agent import Agent
    from core.registry import load_registry
    from core.macros import MacroManager
    from core.voice.orchestrator import VoiceLoop

    console = Console()
    registry = load_registry()
    for e in registry.errors:
        console.print(f"[yellow]registry: {e}[/yellow]")
    agent = Agent(cfg, registry, console, macro_manager=MacroManager())
    loop = VoiceLoop(cfg, agent, console=console)
    console.print("say the wake word, then speak your request. Ctrl+C to stop.")
    try:
        loop.run(max_cycles=1)
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    print("RESULT: cycle complete")


STAGES = {
    "mic": lambda c, a: test_mic(c),
    "wake": lambda c, a: test_wake(c),
    "vad": lambda c, a: test_vad(c),
    "stt": lambda c, a: test_stt(c, wav=a.wav),
    "tts": lambda c, a: test_tts(c, text=a.text, wav=a.wav),
    "tts-stream": lambda c, a: test_tts_stream(c),
    "agent": lambda c, a: test_agent(c),
}


def main():
    ap = argparse.ArgumentParser(description="per-stage voice pipeline checks")
    ap.add_argument("stage", choices=list(STAGES) + ["all"])
    ap.add_argument("--wav", help="wav file for the stt stage, or where tts saves one")
    ap.add_argument("--text", default="Hello. This is Zars speaking offline.",
                    help="text for the tts stage")
    args = ap.parse_args()

    cfg = _cfg()
    if args.stage == "all":
        print("Running every stage. Stages that open the mic WAIT FOR YOU TO SPEAK.")
        order = ["tts", "tts-stream", "mic", "vad", "wake", "stt"]
    else:
        order = [args.stage]

    failures = []
    for name in order:
        try:
            STAGES[name](cfg, args)
        except KeyboardInterrupt:
            print("\ninterrupted")
            break
        except Exception as e:
            print(f"STAGE {name} FAILED: {type(e).__name__}: {e}")
            failures.append(name)

    print(f"\n{'=' * 60}")
    print("done." + (f" FAILED: {', '.join(failures)}" if failures else " all stages OK"))
    print("final stage: python test_voice.py agent   (needs the wake word + ollama)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())