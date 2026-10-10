"""
Speech-to-text for customer feedback, in the customer's own language.

Flow for every recording:
    1. Detect the spoken language from Whisper's full detection distribution
    2. Transcribe in that language    -> original_transcript
    3. Translate that text to English with a translation model
       (IndicTrans2 / NLLB-200 / Google, see translation_service.py)
                                      -> english_transcript (same timestamps style)
       If no translation model is available, Whisper's own translation is used.

Better recognition for Indian languages (no translation needed from Whisper):
    $env:FMCG_WHISPER_MODEL = "large-v3-turbo"

`transcribe()` keeps its old return type (a list of {start, end, text}) and now
returns English, so the existing analysis pipeline works without changes.
Use `transcribe_multilingual()` when you also need the language and the
original-language transcript.

Quick test without the web app:
    python -m app.services.whisper_service path/to/audio.wav
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional

from faster_whisper import WhisperModel, decode_audio

from app.services.audio_cleanup import prepare_audio
# Translation runs in a separate helper process, so a crash or stall there can never
# break the analysis (the recording then uses Whisper's own translation).
from app.services.translation_runner import translate_segments

# Language Whisper actually used in its latest transcription (when not forced).
_last_language: Optional[str] = None

# Same model as before (multilingual "small", CPU, int8).
MODEL_NAME = os.environ.get("FMCG_WHISPER_MODEL", "small").strip() or "small"
print(f"Loading Whisper model: {MODEL_NAME}")
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")

SAMPLE_RATE = 16000

# Display names for languages Whisper can recognise (used in messages too,
# so an unsupported language can be named, e.g. "Yoruba").
LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "bn": "Bengali", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi",
    "ur": "Urdu", "as": "Assamese", "ne": "Nepali", "sa": "Sanskrit", "sd": "Sindhi",
    "si": "Sinhala", "my": "Burmese", "yo": "Yoruba", "ha": "Hausa", "sw": "Swahili",
    "ar": "Arabic", "fa": "Persian", "fr": "French", "es": "Spanish", "pt": "Portuguese",
    "de": "German", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "id": "Indonesian",
    "th": "Thai", "vi": "Vietnamese", "ru": "Russian", "tr": "Turkish", "nn": "Norwegian Nynorsk",
    "cy": "Welsh",
}

# Languages customers are expected to speak. Detection only chooses from these.
# Default: English + Indian languages. Change it without editing code, e.g. in
# PowerShell before starting uvicorn:
#   $env:FMCG_LANGUAGES = "en,hi,ta,te,mr"
DEFAULT_LANGUAGES = ["en", "hi", "ta", "te", "mr", "bn", "gu", "kn", "ml", "pa", "ur"]
_configured = [c.strip().lower() for c in os.environ.get("FMCG_LANGUAGES", "").split(",") if c.strip()]
SUPPORTED_LANGUAGES = {c: LANGUAGE_NAMES.get(c, c) for c in (_configured or DEFAULT_LANGUAGES)}

# If Whisper is at least this sure the speech is a language outside the list,
# the result is flagged as possibly unsupported instead of being trusted.
UNSUPPORTED_WARNING_PROBABILITY = 0.60

# Spoken Hindi is often detected as Urdu (same spoken language, different script).
# Merge Urdu into Hindi so the transcript comes out in Devanagari.
MERGE_INTO = {"ur": "hi"}

# Below this (after re-scoring among supported languages) the result is flagged
# as uncertain so it can be shown or reviewed.
MIN_CONFIDENCE = 0.50

# If Whisper's own top guess (over all languages) is below this, it did not
# really recognise the speech, e.g. "my" at 27%. Treated as uncertain.
MIN_RAW_PROBABILITY = 0.35

# If almost none of the probability is on a supported language, the audio is
# most likely silence or noise.
MIN_SUPPORTED_MASS = 0.05
FALLBACK_LANGUAGE = "en" if "en" in SUPPORTED_LANGUAGES else next(iter(SUPPORTED_LANGUAGES))

# Number of 30-second windows sampled for detection (not only the first one).
DETECTION_WINDOWS = 4

# Brand and product names Whisper should listen for, so "Dove Daily Shine
# Shampoo" isn't written as "D'Auf" or "Delishein Shape". Keep the list short
# and relevant: a long list of unrelated names can make results worse.
# Override without editing code, e.g. in PowerShell before starting uvicorn:
#   $env:FMCG_BRAND_HINTS = "Dove, Daily Shine, Sunsilk, Clinic Plus"
DEFAULT_BRAND_HINTS = [
    "Dove", "Daily Shine", "Sunsilk", "Clinic Plus", "Pantene", "Head & Shoulders",
    "Surf Excel", "Ariel", "Tide", "Rin", "Lux", "Lifebuoy", "Dettol", "Colgate",
    "Maggi", "Amul", "Tata Sampann", "Britannia", "Parle-G", "Nivea", "shampoo", "detergent",
]
BRAND_HINTS = [
    w.strip() for w in os.environ.get("FMCG_BRAND_HINTS", "").split(",") if w.strip()
] or DEFAULT_BRAND_HINTS
HOTWORDS = ", ".join(BRAND_HINTS)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def choose_language(all_language_probs) -> dict:
    """
    Choose the language Whisper itself considers most likely.

    We do NOT restrict detection to the FMCG/Indian language list.
    Whisper's detected language is preserved so that non-Indian languages
    can also be transcribed and translated into English.
    """
    probs = {
        code: float(prob)
        for code, prob in (all_language_probs or [])
    }

    if not probs:
        return {
            "language": FALLBACK_LANGUAGE,
            "language_name": LANGUAGE_NAMES.get(
                FALLBACK_LANGUAGE,
                FALLBACK_LANGUAGE
            ),
            "confidence": 0.0,
            "low_confidence": True,
            "reason": "no language probabilities returned",
            "raw_language": FALLBACK_LANGUAGE,
            "raw_probability": 0.0,
            "top_candidates": [],
            "possibly_unsupported": False,
            "unsupported_language": None,
            "unsupported_language_name": None,
        }

    # Merge Urdu into Hindi as before.
    for src, dst in MERGE_INTO.items():
        if src in probs:
            probs[dst] = probs.get(dst, 0.0) + probs.pop(src)

    # IMPORTANT:
    # Choose from Whisper's COMPLETE language distribution,
    # not only SUPPORTED_LANGUAGES.
    ranked = sorted(
        probs.items(),
        key=lambda kv: kv[1],
        reverse=True
    )

    raw_top = ranked[0]
    raw_language = raw_top[0]
    raw_probability = raw_top[1]

    total_probability = sum(probs.values())

    confidence = (
        raw_probability / total_probability
        if total_probability > 0
        else 0.0
    )

    language_name = LANGUAGE_NAMES.get(
        raw_language,
        raw_language
    )

    possibly_unsupported = (
        raw_language not in SUPPORTED_LANGUAGES
    )

    low_confidence = (
        confidence < MIN_CONFIDENCE
        or raw_probability < MIN_RAW_PROBABILITY
    )

    if raw_probability < MIN_RAW_PROBABILITY:
        reason = "Whisper did not clearly recognise the language."
    elif possibly_unsupported:
        reason = (
            f"Whisper detected {language_name}, "
            "which is outside the configured FMCG language list."
        )
    else:
        reason = ""

    return {
        "language": raw_language,
        "language_name": language_name,
        "confidence": round(confidence, 4),
        "low_confidence": low_confidence,
        "reason": reason,
        "raw_language": raw_language,
        "raw_probability": round(raw_probability, 4),
        "top_candidates": [
            (code, round(prob / total_probability, 4))
            for code, prob in ranked[:5]
        ],
        "possibly_unsupported": possibly_unsupported,
        "unsupported_language": (
            raw_language
            if possibly_unsupported
            else None
        ),
        "unsupported_language_name": (
            language_name
            if possibly_unsupported
            else None
        ),
    }


def detect_language(audio_path_or_array) -> dict:
    """Detect the spoken language from Whisper's full distribution."""
    audio = _load(audio_path_or_array)
    try:
        _, _, all_probs = model.detect_language(
            audio=audio,
            vad_filter=True,  # ignore silence, which otherwise looks like random languages
            language_detection_segments=DETECTION_WINDOWS,
        )
    except (AttributeError, TypeError):
        # Older faster-whisper: detection still runs inside transcribe(), before any text.
        _, info = model.transcribe(audio, vad_filter=True, beam_size=1)
        all_probs = info.all_language_probs or [(info.language, info.language_probability)]

    result = choose_language(all_probs)

    print("\n========== WHISPER LANGUAGE DETECTION ==========")
    print(f"Whisper's raw guess : {result['raw_language']} ({result['raw_probability']:.2%})")
    print(f"Chosen language     : {result['language_name']} ({result['language']})")
    print(f"Confidence          : {result['confidence']:.2%}"
          + ("  <- uncertain" if result["low_confidence"] else ""))
    if result["top_candidates"]:
        print("Top candidates      : " + ", ".join(f"{c} {p:.0%}" for c, p in result["top_candidates"]))
    if result["reason"]:
        print(f"Note                : {result['reason']}")
    if result.get("possibly_unsupported"):
        print("WARNING             : results for this recording may be unreliable")
    print("================================================\n")
    return result


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def _load(audio_path_or_array):
    if isinstance(audio_path_or_array, (str, bytes)) or hasattr(audio_path_or_array, "__fspath__"):
        return decode_audio(str(audio_path_or_array), sampling_rate=SAMPLE_RATE)
    return audio_path_or_array  # already a numpy array


_HINT_WORDS = {
    w for name in BRAND_HINTS for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 1
}


def looks_like_hint_echo(segments: list[dict]) -> bool:
    """
    True when the transcript is mostly the brand-hint words themselves.
    Whisper does this when it can't understand the speech: it repeats its
    hints ("Dettol, Parle-G, Nivea, ...") instead of real words.
    """
    words = re.findall(r"[a-z0-9]+", " ".join(s["text"] for s in segments).lower())
    if len(words) < 4 or not _HINT_WORDS:
        return False
    hinted = sum(1 for w in words if w in _HINT_WORDS)
    return hinted / len(words) >= 0.4


def transcript_is_usable(segments: list[dict]) -> bool:
    """
    Reject obviously unreliable Whisper output using both:
    1. transcript structure
    2. Whisper's confidence metadata
    """
    if not segments:
        return False

    valid_texts = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        avg_logprob = float(segment.get("avg_logprob", -10.0))
        no_speech_prob = float(segment.get("no_speech_prob", 1.0))
        compression_ratio = float(segment.get("compression_ratio", 0.0))

        # Very poor Whisper confidence.
        if avg_logprob < -1.2:
            continue
        # Whisper thinks this is probably not speech.
        if no_speech_prob > 0.75 and avg_logprob < -0.8:
            continue
        # Abnormal compression ratio often means looping/hallucination.
        if compression_ratio > 2.4:
            continue

        valid_texts.append(text)

    if not valid_texts:
        return False

    full_text = " ".join(valid_texts)
    # \w matches letters of every script (Devanagari, Gujarati, Tamil, ...), not only a-z.
    words = re.findall(r"\w+", full_text.lower())
    unique_words = set(words)
    
    # Reject empty or extremely short results
    if len(words) < 4:
        return False
        
    # Reject a transcript where one word dominates almost everything.
    if unique_words:
        most_common_count = max(
            words.count(word)
            for word in unique_words
        )
        if len(words) >= 6 and (
            most_common_count / len(words)
        ) >= 0.60:
            return False

    # Require a reasonable amount of actual text.
    if len(full_text) < 12:
        return False
        
    return True


def retry_transcription(audio, task: str) -> list[dict]:
    """
    Second-pass Whisper attempt for a bad/uncertain transcript.
    This deliberately does NOT force a language because the first
    language detection was already unreliable.
    """
    print(
        f"Retrying Whisper transcription "
        f"(task={task}, automatic language detection)..."
    )
    global _last_language
    segments, info = model.transcribe(
        audio,
        language=None,
        task=task,
        beam_size=8,
        vad_filter=False,
        condition_on_previous_text=True,
        temperature=0.0,
    )
    results = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        results.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": text,
            "avg_logprob": float(s.avg_logprob),
            "no_speech_prob": float(s.no_speech_prob),
            "compression_ratio": float(s.compression_ratio),
        })
    if task == "transcribe":
        _last_language = getattr(info, "language", None)
    return results


def _run(audio, language: str, task: str, use_hints: bool = True) -> list[dict]:
    segments = _transcribe_once(audio, language, task, HOTWORDS if use_hints else None)
    if use_hints and HOTWORDS and looks_like_hint_echo(segments):
        print("Transcript repeated the brand hints instead of real speech; redoing without hints.")
        segments = _transcribe_once(audio, language, task, None)
    return segments


def _transcribe_once(audio, language: str, task: str, hotwords) -> list[dict]:
    global _last_language
    segments, info = model.transcribe(
        audio,
        language=language,          # fixed, so Whisper doesn't re-guess per window
        task=task,                  # "transcribe" (same language) or "translate" (to English)
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,  # stops repeated / invented lines
        hotwords=hotwords or None,  # brand names to listen for
    )
    results = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        results.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": text,
            # Whisper confidence / quality signals
            "avg_logprob": float(s.avg_logprob),
            "no_speech_prob": float(s.no_speech_prob),
            "compression_ratio": float(s.compression_ratio),
        })
    if task == "transcribe":
        _last_language = language or getattr(info, "language", None)
    return results


# Unicode ranges of the script each language is written in.
SCRIPTS = {
    "devanagari": (0x0900, 0x097F), "bengali": (0x0980, 0x09FF), "gurmukhi": (0x0A00, 0x0A7F),
    "gujarati": (0x0A80, 0x0AFF), "oriya": (0x0B00, 0x0B7F), "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F), "kannada": (0x0C80, 0x0CFF), "malayalam": (0x0D00, 0x0D7F),
    "sinhala": (0x0D80, 0x0DFF), "arabic": (0x0600, 0x06FF), "myanmar": (0x1000, 0x109F),
    "cjk": (0x3040, 0x9FFF), "hangul": (0xAC00, 0xD7AF), "cyrillic": (0x0400, 0x04FF), "thai": (0x0E00, 0x0E7F),
}
LANGUAGE_SCRIPT = {
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari", "sa": "devanagari",
    "bn": "bengali", "as": "bengali", "pa": "gurmukhi", "gu": "gujarati", "or": "oriya",
    "ta": "tamil", "te": "telugu", "kn": "kannada", "ml": "malayalam", "si": "sinhala",
    "ur": "arabic", "sd": "arabic", "ar": "arabic", "fa": "arabic", "my": "myanmar",
    "zh": "cjk", "ja": "cjk", "ko": "hangul", "ru": "cyrillic", "uk": "cyrillic", "th": "thai",
}


def _script_of(ch: str) -> Optional[str]:
    code = ord(ch)
    for name, (lo, hi) in SCRIPTS.items():
        if lo <= code <= hi:
            return name
    return "latin" if ch.isascii() and ch.isalpha() else None


def transcript_problem(segments: list[dict], lang: Optional[str]) -> str:
    """
    Why Whisper's own-language transcript can't be trusted for translation, or "".
    Catches the typical hallucinations of a small model on a language it writes badly:
    the wrong script (Gujarati detected, Telugu letters written), mixed foreign scripts,
    and long run-on "words" without spaces.
    """
    text = " ".join(str(s.get("text", "")) for s in segments).strip()
    if not text:
        return "empty transcript"
    letters = [ch for ch in text if ch.isalpha() or 0x0900 <= ord(ch) <= 0x0DFF]
    if len(letters) < 8:
        return "too little text"
    counts = {}
    for ch in letters:
        sc = _script_of(ch)
        if sc:
            counts[sc] = counts.get(sc, 0) + 1
    total = sum(counts.values()) or 1
    expected = LANGUAGE_SCRIPT.get(lang or "")
    # English words inside Indian-language speech (brands, "packaging") are normal:
    # judge the script only on the non-English letters.
    native = total - counts.get("latin", 0)
    if expected:
        if native == 0:
            return "written only in English letters"
        share = counts.get(expected, 0) / native
        if share < 0.6:
            others = {k: v for k, v in counts.items() if k != "latin"}
            main = max(others, key=others.get) if others else "unknown"
            return f"written in {main} script, not {expected} ({share:.0%})"
    foreign = [sc for sc, n in counts.items() if sc not in (expected, "latin") and n / max(1, native) > 0.03]
    if expected and foreign:
        return f"mixed scripts ({', '.join(foreign)})"
    long_words = [w for w in text.split() if len(w) > 40]
    if long_words and sum(len(w) for w in long_words) / max(1, len(text)) > 0.3:
        return "run-on text without word breaks"
    return ""


def _to_english(original: list[dict], lang: Optional[str], audio, use_hints: bool, whisper_lang) -> tuple[list[dict], str]:
    """
    Original-language transcript -> English.
    A translation model first (accurate); Whisper's own translation only as a fallback.
    """
    if not original:
        return [], "none"
    lang = MERGE_INTO.get(lang, lang) if lang else None
    if lang == "en":
        return original, "none"
    problem = transcript_problem(original, lang)
    if problem:
        # Translating a garbled transcript only produces nonsense. Whisper's direct
        # speech-to-English translation is more reliable in this case.
        print(f"Transcript unreliable ({problem}); using Whisper's direct translation instead.")
        return _run(audio, whisper_lang, "translate", use_hints), "whisper"
    if lang:
        translated = translate_segments(original, lang)
        if translated is not None:
            english, engine = translated
            if english:
                return english, engine
    print("Using Whisper's built-in translation.")
    return _run(audio, whisper_lang, "translate", use_hints), "whisper"


def transcribe_multilingual(audio_path, language: Optional[str] = None) -> dict:
    """
    Full result for one audio file.

    language: pass a code (e.g. "hi") to skip detection, for example to reuse the
              language detected on the first part for every later part.
    """
    # Measure background noise and remove it if the recording is noisy
    # (see audio_cleanup.py; controlled by FMCG_DENOISE).
    if isinstance(audio_path, (str, bytes)) or hasattr(audio_path, "__fspath__"):
        try:
            audio, cleanup = prepare_audio(audio_path)
        except Exception as e:  # never let cleaning break transcription
            print(f"Noise removal skipped: {e}")
            audio, cleanup = _load(audio_path), {"method": "none"}
    else:
        audio, cleanup = _load(audio_path), {"method": "none"}

    if language:
        language = MERGE_INTO.get(language, language)
        detection = {
            "language": language,
            "language_name": SUPPORTED_LANGUAGES.get(language, LANGUAGE_NAMES.get(language, language)),
            "confidence": 1.0,
            "low_confidence": False,
            "possibly_unsupported": False,
            "unsupported_language_name": None,
        }
    else:
        detection = detect_language(audio)

    lang = detection["language"]
    translation_engine = "none"
    # Brand hints help only when the language is clear. On speech Whisper
    # doesn't recognise, it tends to repeat the hints instead of real words.
    use_hints = not detection["low_confidence"]
    if not use_hints:
        print(
            "Language uncertain: letting Whisper auto-detect "
            "during transcription and translation."
        )
        # Do NOT force Whisper to use the weak language guess.
        # Let Whisper determine the language again while decoding.
        original = _run(
            audio,
            None,
            "transcribe",
            False
        )
        if not transcript_is_usable(original):
            print(
                "Initial transcription failed the quality check."
            )
            # -----------------------------------------------------
            # SECOND ATTEMPT
            # -----------------------------------------------------
            original = retry_transcription(
                audio,
                "transcribe"
            )
        if not transcript_is_usable(original):
            print(
                "Retry transcription also failed the quality check."
            )
            # Do not send garbage to translation/Qwen.
            english = []
        else:
            # -----------------------------------------------------
            # TRANSLATE ONLY AFTER ORIGINAL TRANSCRIPT PASSES
            # -----------------------------------------------------
            # The language Whisper actually wrote the transcript in.
            used_lang = _last_language or lang
            if used_lang and used_lang != lang:
                print(f"Whisper transcribed the speech as {LANGUAGE_NAMES.get(used_lang, used_lang)} ({used_lang}).")
                lang = used_lang
            english, translation_engine = _to_english(original, used_lang, audio, False, None)
            if translation_engine == "whisper" and not transcript_is_usable(english):
                print(
                    "English translation failed the quality check."
                )
                # Retry translation with stronger decoding.
                english = retry_transcription(
                    audio,
                    "translate"
                )
            if not english or (translation_engine == "whisper" and not transcript_is_usable(english)):
                print(
                    "English translation still failed the quality check."
                )
                english = []
    else:
        original = _run(
            audio,
            lang,
            "transcribe",
            True
        )
        english, translation_engine = _to_english(original, lang, audio, True, lang)

    print("\n========== TRANSCRIPTION RESULT ==========")
    print(
        f"Detected language: "
        f"{detection['language_name']} "
        f"({detection['language']})"
    )
    print("\n--- ORIGINAL LANGUAGE TRANSCRIPT ---")
    for segment in original:
        print(
            f"[{segment['start']:06.2f} - "
            f"{segment['end']:06.2f}] "
            f"{segment['text']}"
        )
    print(f"\n--- ENGLISH TRANSLATION ({translation_engine}) ---")
    for segment in english:
        print(
            f"[{segment['start']:06.2f} - "
            f"{segment['end']:06.2f}] "
            f"{segment['text']}"
        )
    print("==========================================\n")

    return {
        "language": lang,
        "language_name": detection["language_name"],
        "language_confidence": detection["confidence"],
        "low_confidence": detection["low_confidence"],
        "possibly_unsupported": detection.get("possibly_unsupported", False),
        "audio_cleanup": cleanup,
        "unsupported_language_name": detection.get("unsupported_language_name"),
        "original_transcript": original,
        "english_transcript": english,
        "translation_engine": translation_engine,
    }


def transcribe(audio_path, language: Optional[str] = None) -> list[dict]:
    """
    Backward compatible: same return type as before, a list of
    {"start", "end", "text"}, now always in English, so the existing
    Qwen analysis keeps working for Hindi, Tamil, Telugu, Marathi, etc.
    """
    return transcribe_multilingual(audio_path, language=language)["english_transcript"]


# ---------------------------------------------------------------------------
# Manual test:  python -m app.services.whisper_service path/to/audio.wav
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.whisper_service <audio-file> [language-code]")
        sys.exit(1)

    from pathlib import Path

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"File not found: {target.resolve()}")
        print("Pass the path of a real audio or video file, for example one in ..\\uploads\\videos")
        sys.exit(1)

    result = transcribe_multilingual(target, language=sys.argv[2] if len(sys.argv) > 2 else None)

    print(f"Language: {result['language_name']} ({result['language']}), "
          f"confidence {result['language_confidence']:.0%}"
          + (" (uncertain)" if result["low_confidence"] else ""))
    if result["possibly_unsupported"]:
        print(f"WARNING: speech sounds like {result['unsupported_language_name']}, "
              "which is not in the supported list. Results may be unreliable.")
    print("\n--- Original transcript ---")
    for s in result["original_transcript"]:
        print(f"[{s['start']:7.2f}] {s['text']}")
    if result["language"] != "en":
        print("\n--- English translation ---")
        for s in result["english_transcript"]:
            print(f"[{s['start']:7.2f}] {s['text']}")