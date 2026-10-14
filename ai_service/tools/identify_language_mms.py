"""
Which language is spoken in this recording?

Uses Meta's MMS language-identification model, trained on far more languages
than Whisper, including many West African ones Whisper doesn't cover
(Igbo, Twi/Akan, Nigerian Pidgin, Ewe, Ga, ...).

One-time setup (in the ai_service folder, venv active):
    pip install transformers torch

Usage:
    python app\\tools\\identify_language_mms.py "C:\\path\\to\\video.mp4"

The first run downloads the model (about 3.9 GB). Nothing in the app changes.
"""

import sys
from pathlib import Path

from faster_whisper import decode_audio

MODEL = "facebook/mms-lid-256"  # identifies 256 languages
SAMPLE_RATE = 16000

# Readable names for codes likely to come up (ISO 639-3).
NAMES = {
    "eng": "English", "pcm": "Nigerian Pidgin", "yor": "Yoruba", "ibo": "Igbo",
    "hau": "Hausa", "twi": "Twi (Akan)", "aka": "Akan", "ewe": "Ewe", "gaa": "Ga",
    "fan": "Fang", "ful": "Fula", "fuv": "Nigerian Fulfulde", "efi": "Efik",
    "ibb": "Ibibio", "tiv": "Tiv", "urh": "Urhobo", "bin": "Edo", "kri": "Krio",
    "fra": "French", "por": "Portuguese", "swh": "Swahili", "lin": "Lingala",
    "hin": "Hindi", "urd": "Urdu", "ben": "Bengali", "tam": "Tamil", "tel": "Telugu",
    "mar": "Marathi", "guj": "Gujarati", "kan": "Kannada", "mal": "Malayalam",
    "pan": "Punjabi", "cmn": "Mandarin Chinese", "arb": "Arabic",
}


def main():
    if len(sys.argv) < 2:
        print('Usage: python app\\tools\\identify_language_mms.py "C:\\path\\to\\video.mp4"')
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path.resolve()}")
        sys.exit(1)

    try:
        from transformers import pipeline
    except ImportError:
        print("Missing packages. Run:  pip install transformers torch")
        sys.exit(1)

    # Read the audio first, so a missing or unreadable file fails right away.
    try:
        audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    except Exception as e:
        print(f"Couldn't read the audio from {path}: {e}")
        sys.exit(1)
    print(f"Audio length: {len(audio) / SAMPLE_RATE:.1f} s")

    print(f"Loading {MODEL} (first run downloads about 3.9 GB)…")
    classifier = pipeline("audio-classification", model=MODEL)
    print()

    # Whole clip, then each 10-second piece: if the pieces agree, the answer is solid.
    def top(chunk, k=5):
        return classifier({"raw": chunk, "sampling_rate": SAMPLE_RATE}, top_k=k)

    print("Whole recording, top 5 languages:")
    for r in top(audio):
        code = r["label"]
        print(f"  {NAMES.get(code, code):<22} ({code})  {r['score']:.0%}")

    step = 10 * SAMPLE_RATE
    if len(audio) > step:
        print("\nEach 10-second piece, top guess:")
        for i in range(0, len(audio), step):
            chunk = audio[i:i + step]
            if len(chunk) < 3 * SAMPLE_RATE:
                break
            r = top(chunk, k=1)[0]
            code = r["label"]
            print(f"  {i // SAMPLE_RATE:>3}-{min(len(audio), i + step) // SAMPLE_RATE:<3}s  "
                  f"{NAMES.get(code, code):<22} ({code})  {r['score']:.0%}")

    print("\nHow to read this:")
    print("  - One language high (60%+) and the pieces agree: that's very likely the language.")
    print("  - Low scores or pieces disagreeing: too noisy or mixed to tell; ask a native speaker.")


if __name__ == "__main__":
    main()