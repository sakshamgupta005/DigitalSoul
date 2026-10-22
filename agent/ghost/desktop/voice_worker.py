"""Bounded, opt-in local faster-whisper listener. Audio exists only in RAM."""
import json
import os
import sys
import time
from pathlib import Path


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    json.loads(sys.stdin.readline())  # Reserved configuration; never evaluated as code.
    try:
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel
    except ImportError:
        emit({"kind": "error", "code": "dependencies_missing", "message": "Voice is not set up yet. Run .\\scripts\\setup-voice.ps1 once in PowerShell, then click the mic again."})
        return
    model_path = Path(os.environ.get("DIGITALSOUL_WHISPER_MODEL", ".ghost/models/faster-whisper-small.en"))
    if not model_path.is_dir():
        emit({"kind": "error", "code": "model_missing", "message": "Download the local voice model first: .\\.venv\\Scripts\\python.exe -m agent.ghost.desktop.voice_worker --download (or run .\\scripts\\setup-voice.ps1)."})
        return
    try:
        emit({"kind": "loading", "message": "Loading local speech model…"})
        model = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
        rate, block = 16000, 1600
        # Fixed chunks keep RAM bounded. Silence closes an utterance after 0.7 seconds.
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=block) as mic:
            emit({"kind": "ready", "engine": "faster-whisper", "model": model_path.name})
            deadline = time.monotonic() + 300
            frames, prefix, silence, voiced = [], [], 0, 0
            last_signal = last_status = time.monotonic()
            while time.monotonic() < deadline:
                audio, overflow = mic.read(block)
                if overflow:
                    frames, prefix, silence, voiced = [], [], 0, 0
                    continue
                audio = audio[:, 0].copy()
                active = float(np.sqrt(np.mean(audio * audio))) > 0.008
                now = time.monotonic()
                if active:
                    last_signal = now
                if now - last_status >= 1:
                    emit({"kind": "signal", "active": active, "quiet_seconds": round(now - last_signal)})
                    last_status = now
                if not frames and not active:
                    prefix = (prefix + [audio])[-3:]
                    continue
                if not frames:
                    frames = prefix
                    prefix = []
                frames.append(audio)
                voiced += int(active)
                silence = 0 if active else silence + 1
                if silence < 7 and len(frames) < 100:
                    continue
                utterance = np.concatenate(frames)
                frames, silence, count = [], 0, voiced
                voiced = 0
                if count < 3:
                    continue
                emit({"kind": "processing", "message": "Transcribing your voice..."})
                segments, _ = model.transcribe(utterance, language="en", beam_size=1,
                    vad_filter=True, condition_on_previous_text=False)
                text = " ".join(s.text.strip() for s in segments
                                if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0).strip()
                if text:
                    emit({"kind": "transcript", "text": text[:512]})
                else:
                    emit({"kind": "no_speech", "message": "Couldn't understand that. Speak a command again."})
                emit({"kind": "listening", "message": "Listening · say Hey Jarvis and your command"})
    except Exception as exc:
        emit({"kind": "error", "message": f"Local voice unavailable ({type(exc).__name__}): {str(exc)[:180]}. Check your Windows input device and microphone access."})


if __name__ == "__main__":
    if sys.argv[1:] == ["--download"]:
        from huggingface_hub import snapshot_download
        target = Path(os.environ.get("DIGITALSOUL_WHISPER_MODEL", ".ghost/models/faster-whisper-small.en"))
        snapshot_download("Systran/faster-whisper-small.en", local_dir=target)
        print(f"Voice model saved to {target.resolve()}")
    else:
        main()
