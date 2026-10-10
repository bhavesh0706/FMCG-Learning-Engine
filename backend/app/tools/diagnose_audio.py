"""
Why can't Whisper recognise this recording? Try the likely fixes side by side.

Usage (from the backend folder):
    python tools\\diagnose_audio.py "C:\\path\\to\\video.mp4"
    python tools\\diagnose_audio.py "C:\\path\\to\\video.mp4" --models small medium

For each model it reports:
  1. Whisper's top 5 language guesses over ALL languages (no restriction),
     with and without the silence filter (VAD), on the original audio and
     on a noise-cleaned copy.
  2. What comes out when transcribing as English, Yoruba and Hausa
     (and in Whisper's own top guess), with the silence filter off.

Nothing in the app is changed. "medium" downloads about 1.5 GB the first time.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from faster_whisper import WhisperModel, decode_audio

SAMPLE_RATE = 16000
FORCED_LANGUAGES = ["en", "yo", "ha"]


def clean_copy(src: Path, out_dir: Path):
    """Noise-reduced, volume-normalised copy of the audio (needs ffmpeg in PATH)."""
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found in PATH, skipping the noise-cleaned test.")
        return None
    out = out_dir / "cleaned.wav"
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE),
        # cut rumble and hiss, reduce steady noise, even out loudness
        "-af", "highpass=f=100,lowpass=f=7500,afftdn=nf=-25,loudnorm",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True)
        return out
    except subprocess.CalledProcessError as e:
        print(f"Couldn't make a cleaned copy: {e}")
        return None


def top_languages(model, audio, vad: bool, n=5):
    try:
        _, _, probs = model.detect_language(audio=audio, vad_filter=vad, language_detection_segments=4)
    except Exception:
        _, info = model.transcribe(audio, vad_filter=vad, beam_size=1)
        probs = info.all_language_probs or [(info.language, info.language_probability)]
    probs = sorted(probs, key=lambda kv: kv[1], reverse=True)[:n]
    return ", ".join(f"{code} {p:.0%}" for code, p in probs), probs[0][0] if probs else "en"


def transcribe_text(model, audio, language: str, task: str = "transcribe") -> str:
    segments, _ = model.transcribe(
        audio,
        language=language,
        task=task,
        beam_size=5,
        vad_filter=False,  # keep everything, in case the filter is cutting speech out
        condition_on_previous_text=False,
    )
    text = " ".join(s.text.strip() for s in segments if s.text.strip())
    return text or "(nothing)"


def show(label: str, text: str, width: int = 160):
    text = text.replace("\n", " ")
    print(f"  {label:<26} {text[:width]}{'…' if len(text) > width else ''}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--models", nargs="+", default=["small"])
    args = parser.parse_args()

    src = Path(args.file)
    if not src.exists():
        print(f"File not found: {src.resolve()}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        cleaned_path = clean_copy(src, Path(tmp))
        variants = {"original": decode_audio(str(src), sampling_rate=SAMPLE_RATE)}
        if cleaned_path:
            variants["noise-cleaned"] = decode_audio(str(cleaned_path), sampling_rate=SAMPLE_RATE)

        for name in args.models:
            print("\n" + "=" * 78)
            print(f"MODEL: {name}  (loading…)")
            print("=" * 78)
            t0 = time.time()
            model = WhisperModel(name, device="cpu", compute_type="int8")
            print(f"loaded in {time.time() - t0:.0f}s")

            for vname, audio in variants.items():
                print(f"\n--- {vname} audio ---")
                with_vad, _ = top_languages(model, audio, vad=True)
                without_vad, best = top_languages(model, audio, vad=False)
                print(f"  Language guesses, silence filter ON : {with_vad}")
                print(f"  Language guesses, silence filter OFF: {without_vad}")

                print("  Transcripts (silence filter off):")
                tried = []
                for lang in [best] + FORCED_LANGUAGES:
                    if lang in tried:
                        continue
                    tried.append(lang)
                    label = f"as '{lang}'" + (" (top guess)" if lang == best else "")
                    show(label, transcribe_text(model, audio, lang))
                if best != "en":
                    show(f"'{best}' -> English", transcribe_text(model, audio, best, task="translate"))

    print("\nHow to read this:")
    print("  - A sensible sentence in one row shows which language/setting works.")
    print("  - Text with the filter OFF but empty in the app means the silence filter cut the speech.")
    print("  - Better results on 'noise-cleaned' mean background noise is the problem.")
    print("  - Better results on 'medium' mean the small model is the limit.")


if __name__ == "__main__":
    main()