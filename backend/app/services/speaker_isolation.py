"""
Keep only the main speaker's voice.

Noise filters keep every human voice, so people talking near the customer
survive them. This step tells the voices apart (speaker diarization), keeps the
person who talks the most (the customer giving the review) and silences the
others. Silencing instead of cutting keeps the timestamps matching the video.

Runs on CPU with sherpa-onnx (no torch). One-time setup:
    pip install sherpa-onnx
Models (~28 MB) download once into backend/models.

Turn off without editing code (PowerShell, before starting uvicorn):
    $env:FMCG_MAIN_SPEAKER_ONLY = "0"

Where people talk over each other at the same moment, both voices are kept:
no tool can cleanly pull overlapping voices apart.
"""

from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

MAIN_ONLY = os.environ.get("FMCG_MAIN_SPEAKER_ONLY", "1").strip().lower() not in ("0", "false", "off", "no")

# Voices count as different speakers below this similarity (0-1). Lower = merges
# more voices into one speaker; higher = splits one voice into several.
CLUSTER_THRESHOLD = float(os.environ.get("FMCG_SPEAKER_THRESHOLD", "0.5"))

# Ignore "other speakers" with less talk time than this (seconds): usually noise.
MIN_OTHER_SECONDS = 0.5

BACKEND_DIR = Path(__file__).resolve().parents[2]
MODELS_DIR = BACKEND_DIR / "models"
SEG_DIR = MODELS_DIR / "speaker-segmentation"
EMB_MODEL = MODELS_DIR / "speaker-embedding-wespeaker-voxceleb.onnx"

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEG_URL = f"{_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMB_URL = f"{_RELEASES}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"

_diarizer = None


def _download(url: str, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dst)


def _ensure_models() -> tuple[Path, Path]:
    seg_model = next(iter(sorted(SEG_DIR.glob("model*.onnx"))), None) if SEG_DIR.exists() else None
    if seg_model is None:
        print("Downloading speaker segmentation model (one time, ~6 MB)…")
        archive = MODELS_DIR / "speaker-segmentation.tar.bz2"
        _download(SEG_URL, archive)
        with tarfile.open(archive, "r:bz2") as tar:
            for member in tar.getmembers():
                name = Path(member.name).name
                if member.isfile() and name.endswith(".onnx"):
                    member.name = name  # flatten into SEG_DIR
                    tar.extract(member, SEG_DIR)
        archive.unlink(missing_ok=True)
    # Prefer the int8 version: same accuracy for this use, faster on CPU.
    seg_model = (SEG_DIR / "model.int8.onnx") if (SEG_DIR / "model.int8.onnx").exists() else (SEG_DIR / "model.onnx")

    if not EMB_MODEL.exists():
        print("Downloading speaker recognition model (one time, ~26 MB)…")
        _download(EMB_URL, EMB_MODEL)
    return seg_model, EMB_MODEL


def _get_diarizer():
    global _diarizer
    if _diarizer is not None:
        return _diarizer
    import sherpa_onnx  # imported lazily so the app still starts if it's missing

    seg_model, emb_model = _ensure_models()
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model)),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model)),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("speaker model configuration is invalid")
    _diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    return _diarizer


def _activity(spans, n: int, pad: float = 0.0) -> np.ndarray:
    active = np.zeros(n, dtype=bool)
    for start, end in spans:
        a = max(0, int((start - pad) * SAMPLE_RATE))
        b = min(n, int((end + pad) * SAMPLE_RATE))
        active[a:b] = True
    return active


def keep_main_speaker(audio: np.ndarray) -> tuple[np.ndarray, dict]:
    """
    Returns (audio with other speakers silenced, info). Never fails: if
    diarization isn't available or finds one voice, the audio is unchanged.
    """
    info = {"enabled": MAIN_ONLY, "speakers": None, "muted_seconds": 0.0}
    if not MAIN_ONLY or audio is None or len(audio) < SAMPLE_RATE * 2:
        return audio, info

    try:
        diarizer = _get_diarizer()
        segments = diarizer.process(np.ascontiguousarray(audio, dtype=np.float32)).sort_by_start_time()
    except ImportError:
        print("Speaker isolation skipped: run  pip install sherpa-onnx  to enable it.")
        info["error"] = "sherpa-onnx not installed"
        return audio, info
    except Exception as e:
        print(f"Speaker isolation skipped: {e}")
        info["error"] = str(e)
        return audio, info

    talk = {}
    for s in segments:
        talk[s.speaker] = talk.get(s.speaker, 0.0) + (s.end - s.start)
    info["speakers"] = len(talk)
    if len(talk) < 2:
        print(f"Speakers found: {len(talk)}. Nothing to remove.")
        return audio, info

    main = max(talk, key=talk.get)
    others_time = sum(t for spk, t in talk.items() if spk != main)
    info["main_speaker_seconds"] = round(talk[main], 1)
    info["other_speakers_seconds"] = round(others_time, 1)
    if others_time < MIN_OTHER_SECONDS:
        return audio, info

    n = len(audio)
    # Small padding around the main speaker so word edges aren't clipped.
    main_active = _activity([(s.start, s.end) for s in segments if s.speaker == main], n, pad=0.15)
    other_active = _activity([(s.start, s.end) for s in segments if s.speaker != main], n)
    mute = other_active & ~main_active  # overlapping speech is kept

    muted = float(mute.sum()) / SAMPLE_RATE
    info["muted_seconds"] = round(muted, 1)
    if muted < MIN_OTHER_SECONDS:
        return audio, info

    # Soft 20 ms fades at the edges so there are no clicks.
    gain = (~mute).astype(np.float32)
    win = np.hanning(int(0.02 * SAMPLE_RATE))
    gain = np.convolve(gain, win / win.sum(), mode="same").astype(np.float32)

    print(
        f"Speakers found: {len(talk)}. Keeping the main speaker ({talk[main]:.0f}s of speech) "
        f"and silencing {muted:.1f}s of other voices."
    )
    return (audio * gain).astype(np.float32), info