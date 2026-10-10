"""
Original-language transcript -> English, with a dedicated translation model.

Why: Whisper's built-in translation (task="translate") recognises and translates
in one step and guesses badly on Indian languages ("कुरकुरीतपणा" -> "cucumber").
Here Whisper first writes down exactly what was said, then a translation model
translates the text.

Engines (choose with FMCG_TRANSLATOR, set before starting uvicorn):
  auto        (default) IndicTrans2 for Indian languages, NLLB-200 for the rest
  indictrans2 AI4Bharat IndicTrans2: built for 22 Indian languages (self-hosted)
  nllb        Meta NLLB-200: 200+ languages (self-hosted)
  google      Google Cloud Translation (needs GOOGLE_TRANSLATE_API_KEY; text leaves your server)
  whisper     old behaviour: Whisper's own translation

Install (once, in the backend's virtual environment):
  pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install transformers sentencepiece
  pip install IndicTransToolkit          # for IndicTrans2 (optional; NLLB is used without it)

Models download on first use (cached by Hugging Face):
  FMCG_INDICTRANS_MODEL  default ai4bharat/indictrans2-indic-en-dist-200M (~1 GB)
  FMCG_NLLB_MODEL        default facebook/nllb-200-distilled-600M        (~2.5 GB)
Some AI4Bharat models require accepting their license on Hugging Face and
`huggingface-cli login`; if loading fails, NLLB-200 is used automatically.

Quick test:
  python -m app.services.translation_service mr "मला याची कॉर्नची चव आणि कुरकुरीतपणा आवडला."
"""

from __future__ import annotations

import os

# PyTorch and Whisper's engine (CTranslate2) each ship Intel OpenMP. On Windows,
# loading both in one process stops it with "OMP: Error #15 ... libiomp5md.dll".
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
import re
import sys
import threading
import time
from typing import Optional

TRANSLATOR = os.environ.get("FMCG_TRANSLATOR", "auto").strip().lower() or "auto"
INDICTRANS_MODEL = os.environ.get("FMCG_INDICTRANS_MODEL", "ai4bharat/indictrans2-indic-en-dist-200M")
NLLB_MODEL = os.environ.get("FMCG_NLLB_MODEL", "facebook/nllb-200-distilled-600M")
GOOGLE_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()
NUM_BEAMS = int(os.environ.get("FMCG_TRANSLATE_BEAMS", "4") or 4)

# Whisper language code -> FLORES-200 code (used by both IndicTrans2 and NLLB).
FLORES = {
    "en": "eng_Latn", "hi": "hin_Deva", "mr": "mar_Deva", "ta": "tam_Taml", "te": "tel_Telu",
    "bn": "ben_Beng", "gu": "guj_Gujr", "kn": "kan_Knda", "ml": "mal_Mlym", "pa": "pan_Guru",
    "ur": "urd_Arab", "as": "asm_Beng", "ne": "npi_Deva", "sa": "san_Deva", "sd": "snd_Arab",
    "or": "ory_Orya", "si": "sin_Sinh", "my": "mya_Mymr", "yo": "yor_Latn", "ha": "hau_Latn",
    "sw": "swh_Latn", "ar": "arb_Arab", "fa": "pes_Arab", "fr": "fra_Latn", "es": "spa_Latn",
    "pt": "por_Latn", "de": "deu_Latn", "zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang",
    "id": "ind_Latn", "th": "tha_Thai", "vi": "vie_Latn", "ru": "rus_Cyrl", "tr": "tur_Latn",
    "cy": "cym_Latn", "it": "ita_Latn", "nl": "nld_Latn", "pl": "pol_Latn", "uk": "ukr_Cyrl",
}

# Languages IndicTrans2 handles (Indic -> English model).
INDICTRANS_LANGS = {"hi", "mr", "ta", "te", "bn", "gu", "kn", "ml", "pa", "ur", "as", "ne", "sa", "sd", "or"}

_lock = threading.Lock()
_models: dict = {}  # engine -> loaded objects, or Exception if loading failed


# ---------------------------------------------------------------------------
# Sentence grouping: translate whole sentences, keep timestamps
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.?!।॥؟。！？]\s*$")


def _num(seg: dict, key: str, default: float) -> float:
    """A segment's quality value; missing or empty means the default."""
    v = seg.get(key)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def group_segments(segments: list[dict], max_segments: int = 4, max_words: int = 40) -> list[dict]:
    """Join Whisper segments into sentence-sized groups (fragments translate badly)."""
    groups, current = [], []

    def flush():
        if not current:
            return
        n = len(current)
        groups.append({
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "text": " ".join(s["text"].strip() for s in current).strip(),
            # Carry Whisper's quality signals so later checks still work.
            "avg_logprob": sum(_num(s, "avg_logprob", -0.5) for s in current) / n,
            "no_speech_prob": max(_num(s, "no_speech_prob", 0.0) for s in current),
            "compression_ratio": sum(_num(s, "compression_ratio", 1.5) for s in current) / n,
        })
        current.clear()

    for seg in segments:
        if not str(seg.get("text", "")).strip():
            continue
        current.append(seg)
        words = sum(len(s["text"].split()) for s in current)
        if _SENTENCE_END.search(seg["text"]) or len(current) >= max_segments or words >= max_words:
            flush()
    flush()
    return groups


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def _torch_threads():
    try:
        import torch

        # Leave cores for Whisper and the API; translation is short work.
        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 2)))
    except Exception:
        pass


def _load(engine: str):
    """Load an engine once per process. Returns objects, or raises the loading error."""
    with _lock:
        if engine in _models:
            if isinstance(_models[engine], Exception):
                raise _models[engine]
            return _models[engine]
        started = time.time()
        try:
            _torch_threads()
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            if engine == "indictrans2":
                # Optional package: without it, NLLB-200 is used (see engines_for).
                try:
                    from IndicTransToolkit.processor import IndicProcessor  # pyright: ignore[reportMissingImports]
                except ImportError:
                    from IndicTransToolkit import IndicProcessor  # pyright: ignore[reportMissingImports]
                tok = AutoTokenizer.from_pretrained(INDICTRANS_MODEL, trust_remote_code=True)
                mdl = AutoModelForSeq2SeqLM.from_pretrained(INDICTRANS_MODEL, trust_remote_code=True).eval()
                _models[engine] = (tok, mdl, IndicProcessor(inference=True))
            elif engine == "nllb":
                tok = AutoTokenizer.from_pretrained(NLLB_MODEL)
                mdl = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL).eval()
                _models[engine] = (tok, mdl)
            else:
                raise ValueError(f"unknown engine {engine}")
            print(f"Translation: {engine} model loaded in {time.time() - started:.0f}s.", flush=True)
            return _models[engine]
        except Exception as e:
            _models[engine] = e  # don't retry a broken install on every recording
            print(f"Translation: {engine} unavailable ({e.__class__.__name__}: {e}).", flush=True)
            raise


def _generation_guards(inputs) -> dict:
    """
    Stop translation loops ("and weep, and weep, ..."): no 3-word phrase may repeat,
    repeats are penalised, and the output can't be much longer than the input.
    """
    in_len = int(inputs["input_ids"].shape[1])
    return {
        "max_length": min(256, int(in_len * 2.0) + 16),
        "no_repeat_ngram_size": 3,
        "repetition_penalty": 1.2,
    }


def translation_looks_broken(source: str, english: str) -> str:
    """Reason why a translation can't be trusted, or "" if it looks fine."""
    words = re.findall(r"[a-z']+", english.lower())
    if not words:
        return "empty translation"
    src_words = max(1, len(source.split()))
    if len(words) > 4 * src_words + 20:
        return f"translation much longer than the original ({len(words)} vs {src_words} words)"
    if len(words) >= 12:
        # The same short phrase over and over is a loop, not a translation.
        trigrams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
        top = max(trigrams.count(t) for t in set(trigrams))
        if top >= 4 and top / len(trigrams) > 0.15:
            return "translation repeats itself"
        if len(set(words)) / len(words) < 0.3:
            return "translation repeats the same few words"
    return ""


def _translate_indictrans2(texts: list[str], lang: str) -> list[str]:
    import torch

    tok, mdl, ip = _load("indictrans2")
    src = FLORES[lang]
    batch = ip.preprocess_batch(texts, src_lang=src, tgt_lang="eng_Latn")
    inputs = tok(batch, truncation=True, padding="longest", return_tensors="pt")
    guards = _generation_guards(inputs)
    with torch.no_grad():
        try:
            out = mdl.generate(**inputs, use_cache=True, min_length=0, num_beams=NUM_BEAMS, **guards)
        except Exception:
            # Some transformers versions break the model's cache; slower but works.
            out = mdl.generate(**inputs, use_cache=False, min_length=0, num_beams=NUM_BEAMS, **guards)
    decoded = tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True)
    return ip.postprocess_batch(decoded, lang="eng_Latn")


def _translate_nllb(texts: list[str], lang: str) -> list[str]:
    import torch

    tok, mdl = _load("nllb")
    tok.src_lang = FLORES[lang]
    inputs = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=256)
    with torch.no_grad():
        out = mdl.generate(
            **inputs,
            forced_bos_token_id=tok.convert_tokens_to_ids("eng_Latn"),
            num_beams=NUM_BEAMS,
            **_generation_guards(inputs),
        )
    return tok.batch_decode(out, skip_special_tokens=True)


def _translate_google(texts: list[str], lang: str) -> list[str]:
    import html

    import requests

    if not GOOGLE_KEY:
        raise RuntimeError("GOOGLE_TRANSLATE_API_KEY is not set")
    r = requests.post(
        "https://translation.googleapis.com/language/translate/v2",
        params={"key": GOOGLE_KEY},
        json={"q": texts, "source": lang, "target": "en", "format": "text"},
        timeout=60,
    )
    r.raise_for_status()
    return [html.unescape(t["translatedText"]) for t in r.json()["data"]["translations"]]


def engines_for(lang: str) -> list[str]:
    """Engines to try, best first, for this language and the FMCG_TRANSLATOR setting."""
    if TRANSLATOR == "whisper":
        return []
    if TRANSLATOR == "google":
        return ["google"]
    if TRANSLATOR == "indictrans2":
        return ["indictrans2", "nllb"] if lang in INDICTRANS_LANGS else ["nllb"]
    if TRANSLATOR == "nllb":
        return ["nllb"]
    # auto
    return (["indictrans2", "nllb"] if lang in INDICTRANS_LANGS else ["nllb"]) if lang in FLORES else []


_ENGINES = {"indictrans2": _translate_indictrans2, "nllb": _translate_nllb, "google": _translate_google}


def translate_texts(texts: list[str], lang: str, batch_size: int = 8) -> tuple[list[str], str]:
    """Translate to English. Returns (translations, engine used). Raises if no engine works."""
    if lang == "en":
        return list(texts), "none"
    errors = []
    for engine in engines_for(lang):
        try:
            out = []
            for i in range(0, len(texts), batch_size):
                out.extend(_ENGINES[engine](texts[i:i + batch_size], lang))
            if len(out) == len(texts) and any(t.strip() for t in out):
                return [t.strip() for t in out], engine
            errors.append(f"{engine}: empty result")
        except Exception as e:
            errors.append(f"{engine}: {e.__class__.__name__}")
    raise RuntimeError("no translation engine available (" + "; ".join(errors or ["none configured"]) + ")")


def translate_segments(segments: list[dict], lang: str) -> Optional[tuple[list[dict], str]]:
    """
    Whisper segments (original language) -> English segments with timestamps.
    Returns (segments, engine), or None so the caller can fall back to Whisper.
    """
    if not segments:
        return [], "none"
    if lang == "en":
        return segments, "none"
    groups = group_segments(segments)
    started = time.time()
    try:
        english, engine = translate_texts([g["text"] for g in groups], lang)
    except Exception as e:
        print(f"Translation: falling back to Whisper ({e}).", flush=True)
        return None
    for g, t in zip(groups, english):
        problem = translation_looks_broken(g["text"], t)
        if problem:
            print(f"Translation: {engine} output rejected ({problem}); falling back to Whisper.", flush=True)
            return None
    out = [dict(g, text=t) for g, t in zip(groups, english) if t]
    print(f"Translation: {len(groups)} sentences {lang}->en with {engine} in {time.time() - started:.1f}s.", flush=True)
    return out, engine


def serve():
    """
    Helper-process mode (used by translation_runner.py): one JSON request per line on
    stdin, one JSON answer per line on stdout. Logs go to stderr so they never mix in.
    """
    import json

    out = sys.stdout
    sys.stdout = sys.stderr  # every print() from here on is a log line
    print(f"Translation helper ready (engine setting: {TRANSLATOR}).", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            result = translate_segments(req["segments"], req["lang"])
            if result is None:
                answer = {"id": req.get("id"), "ok": False, "error": "no translation engine worked"}
            else:
                answer = {"id": req.get("id"), "ok": True, "segments": result[0], "engine": result[1]}
        except Exception as e:  # never die on one bad request
            answer = {"id": None, "ok": False, "error": f"{e.__class__.__name__}: {e}"}
        out.write(json.dumps(answer, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve()
        sys.exit(0)
    if len(sys.argv) < 3:
        print('Usage: python -m app.services.translation_service <lang> "text in that language"')
        sys.exit(1)
    result, used = translate_texts([" ".join(sys.argv[2:])], sys.argv[1])
    print(f"[{used}] {result[0]}")