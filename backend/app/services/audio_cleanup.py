"""
Background-noise removal before transcription.

The recording is measured first. Clean recordings are passed through untouched,
because Whisper is trained on real-world audio and unnecessary cleaning can
make transcripts worse. Noisy recordings are cleaned with one of:

  deepfilter  DeepFilterNet (default for noisy audio): removes fans, AC, traffic,
           wind, street and crowd noise while keeping the voice. Runs as a
           standalone program (no Python packages), downloaded once (~36 MB).
  full     Demucs voice separation (removes music/TV) followed by DeepFilterNet.
           Strongest, slowest. Needs:  pip install demucs
  rnnoise  RNNoise neural noise filter, built into FFmpeg (filter "arnndn").
           Fast and light. Best for steady noise: fans, AC, traffic, street,
           crowd hum. Model file (~300 KB) is downloaded once to backend/models.
  strong   RNNoise twice, plus extra noise reduction and a gate that silences
           the background in pauses between words. Use for very noisy
           recordings; on soft-spoken people it can clip the start of words.
  voice    Demucs voice separation: keeps the voice, drops music/TV/background.
           Slower. Needs:  pip install demucs   (uses the torch you installed).

Choose with an environment variable before starting uvicorn (PowerShell):
    $env:FMCG_DENOISE = "auto"      # default: clean only noisy recordings (DeepFilterNet)
    $env:FMCG_DENOISE = "deepfilter" # always apply DeepFilterNet
    $env:FMCG_DENOISE = "full"      # music/TV removal + DeepFilterNet
    $env:FMCG_DENOISE = "voice"     # always separate the voice (Demucs)
    $env:FMCG_DENOISE = "rnnoise"   # always apply RNNoise
    $env:FMCG_DENOISE = "strong"    # always apply the strong cleaning
    $env:FMCG_DENOISE = "off"       # never clean

After noise removal, other people's voices are silenced so only the main
speaker reaches Whisper (see speaker_isolation.py; FMCG_MAIN_SPEAKER_ONLY).

Listen to the difference on any file (writes WAV files you can play):
    python -m app.services.audio_cleanup "C:\\path\\to\\video.mp4"
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
from faster_whisper import decode_audio

from app.services.speaker_isolation import keep_main_speaker

SAMPLE_RATE = 16000

MODE = os.environ.get("FMCG_DENOISE", "auto").strip().lower() or "auto"

# Below this estimated signal-to-noise ratio a recording counts as noisy.
# Quiet-room recordings with natural pauses usually measure 30 dB or more;
# your noisy shop and street test clips measured about 19-22 dB.
# Tune without editing code:  $env:FMCG_DENOISE_SNR = "25"
NOISY_BELOW_DB = float(os.environ.get("FMCG_DENOISE_SNR", "25"))

# The noise estimate is rough, so "auto" doesn't use a hard on/off line:
#   below NOISY_BELOW_DB          -> full DeepFilterNet
#   NOISY_BELOW_DB..CLEAN_ABOVE_DB -> moderate: background reduced by up to MODERATE_ATTEN_DB
#   CLEAN_ABOVE_DB and above       -> left untouched (studio-clean)
CLEAN_ABOVE_DB = float(os.environ.get("FMCG_CLEAN_SNR", "40"))
MODERATE_ATTEN_DB = 20

BACKEND_DIR = Path(__file__).resolve().parents[2]
MODELS_DIR = BACKEND_DIR / "models"
RNNOISE_MODEL = MODELS_DIR / "rnnoise-sh.rnnn"
# "sh" (somnolent-hogwash) is the general-purpose RNNoise model for speech in noise.
RNNOISE_URL = "https://raw.githubusercontent.com/richardpl/arnndn-models/master/sh.rnnn"


# ---------------------------------------------------------------------------
# Measuring noise
# ---------------------------------------------------------------------------

def estimate_snr_db(audio: np.ndarray, frame_ms: int = 30) -> float:
    """
    Rough signal-to-noise ratio: loud frames (speech) vs. the quietest frames
    (the background that is always there). Good enough to tell a quiet room
    from a noisy shop; not a lab measurement.
    """
    frame = int(SAMPLE_RATE * frame_ms / 1000)
    if audio is None or len(audio) < frame * 10:
        return 99.0
    usable = len(audio) // frame * frame
    rms = np.sqrt(np.mean(audio[:usable].reshape(-1, frame) ** 2, axis=1) + 1e-12)
    speech = np.percentile(rms, 90)
    floor = max(np.percentile(rms, 10), 1e-5)
    return float(20 * np.log10(speech / floor))


# ---------------------------------------------------------------------------
# Cleaning methods
# ---------------------------------------------------------------------------

def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ensure_rnnoise_model() -> Path | None:
    if RNNOISE_MODEL.exists():
        return RNNOISE_MODEL
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        print("Downloading RNNoise model (one time, ~300 KB)…")
        urllib.request.urlretrieve(RNNOISE_URL, RNNOISE_MODEL)
        return RNNOISE_MODEL
    except Exception as e:
        print(f"Couldn't download the RNNoise model ({e}); using FFmpeg's basic noise filter instead.")
        RNNOISE_MODEL.unlink(missing_ok=True)
        return None


def _run_ffmpeg(src: str, dst: Path, audio_filter: str, cwd: Path | None = None, rate: int = SAMPLE_RATE) -> bool:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        print("FFmpeg not found in PATH; skipping noise removal.")
        return False
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-af", audio_filter, "-ar", str(rate), str(dst)]
    try:
        subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg noise removal failed: {(e.stderr or '').strip()[:300]}")
        return False


def _clean_rnnoise(src: str, out_dir: Path) -> Path | None:
    dst = out_dir / "cleaned_rnnoise.wav"
    model = _ensure_rnnoise_model()
    if model:
        # arnndn works at 48 kHz. Run from the models folder so the model path has
        # no drive letter (Windows "C:" would break FFmpeg's filter syntax).
        chain = f"aresample=48000,highpass=f=80,arnndn=m={model.name},loudnorm"
        if _run_ffmpeg(src, dst, chain, cwd=model.parent):
            return dst
    # Fallback: FFmpeg's built-in FFT noise reduction (no model needed).
    if _run_ffmpeg(src, dst, "highpass=f=80,lowpass=f=7500,afftdn=nf=-25,loudnorm"):
        return dst
    return None


DEEPFILTER_VERSION = "0.5.6"
DEEPFILTER_BIN = MODELS_DIR / ("deep-filter.exe" if platform.system() == "Windows" else "deep-filter")


def _deepfilter_url() -> str | None:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        name = f"deep-filter-{DEEPFILTER_VERSION}-x86_64-pc-windows-msvc.exe"
    elif system == "Linux" and machine in ("x86_64", "amd64"):
        name = f"deep-filter-{DEEPFILTER_VERSION}-x86_64-unknown-linux-musl"
    elif system == "Darwin":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        name = f"deep-filter-{DEEPFILTER_VERSION}-{arch}-apple-darwin"
    else:
        return None
    return f"https://github.com/Rikorose/DeepFilterNet/releases/download/v{DEEPFILTER_VERSION}/{name}"


def _ensure_deepfilter() -> Path | None:
    if DEEPFILTER_BIN.exists():
        return DEEPFILTER_BIN
    url = _deepfilter_url()
    if not url:
        print("DeepFilterNet isn't available for this system; using RNNoise.")
        return None
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        print("Downloading DeepFilterNet (one time, ~36 MB)…")
        tmp = DEEPFILTER_BIN.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(DEEPFILTER_BIN)
        if platform.system() != "Windows":
            DEEPFILTER_BIN.chmod(0o755)
        return DEEPFILTER_BIN
    except Exception as e:
        print(f"Couldn't download DeepFilterNet ({e}); using RNNoise.")
        return None


def _clean_deepfilter(src: str, out_dir: Path, atten_db: float | None = None) -> Path | None:
    """
    DeepFilterNet3 with its post-filter: strong noise removal that keeps the voice.
    atten_db limits how much the background is reduced (None = no limit).
    """
    exe = _ensure_deepfilter()
    if not exe:
        return _clean_rnnoise(src, out_dir)
    wav48 = out_dir / "df_input.wav"
    if not _run_ffmpeg(src, wav48, "highpass=f=70", rate=48000):
        return _clean_rnnoise(src, out_dir)
    df_dir = out_dir / "df_out"
    try:
        subprocess.run(
            [str(exe), "--pf", "--compensate-delay"]
            + (["--atten-lim-db", str(atten_db)] if atten_db else [])
            + ["-o", str(df_dir), str(wav48)],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        print(f"DeepFilterNet failed ({detail.strip()[:200]}); using RNNoise.")
        return _clean_rnnoise(src, out_dir)
    enhanced = df_dir / wav48.name
    dst = out_dir / "cleaned_deepfilter.wav"
    if enhanced.exists() and _run_ffmpeg(str(enhanced), dst, "loudnorm"):
        return dst
    return _clean_rnnoise(src, out_dir)


def _clean_full(src: str, out_dir: Path) -> Path | None:
    """Music/TV removal (Demucs) first, then DeepFilterNet on the separated voice."""
    voice = _clean_voice(src, out_dir)
    if not voice:
        return _clean_deepfilter(src, out_dir)
    final_dir = out_dir / "full"
    final_dir.mkdir(exist_ok=True)
    cleaned = _clean_deepfilter(str(voice), final_dir)
    if not cleaned:
        return voice
    dst = out_dir / "cleaned_full.wav"
    shutil.copy(cleaned, dst)
    return dst


def _clean_strong(src: str, out_dir: Path) -> Path | None:
    """RNNoise twice + FFT denoise + a gate that silences background between words."""
    dst = out_dir / "cleaned_strong.wav"
    model = _ensure_rnnoise_model()
    if model:
        chain = (
            f"aresample=48000,highpass=f=100,lowpass=f=7000,"
            f"arnndn=m={model.name},arnndn=m={model.name},"
            f"afftdn=nf=-30:tn=1,"
            f"agate=threshold=0.03:ratio=6:attack=5:release=300,"
            f"loudnorm"
        )
        if _run_ffmpeg(src, dst, chain, cwd=model.parent):
            return dst
    return _clean_rnnoise(src, out_dir)


def _clean_voice(src: str, out_dir: Path) -> Path | None:
    """Separate the voice from the background with Demucs (two stems: vocals / rest)."""
    try:
        import demucs  # noqa: F401
    except ImportError:
        print("Demucs is not installed (pip install demucs); using RNNoise instead.")
        return _clean_rnnoise(src, out_dir)

    sep_dir = out_dir / "demucs"
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(sep_dir), str(src)]
    print("Separating the voice from the background (Demucs)… this can take a while on CPU.")
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Demucs failed: {(e.stderr or '').strip()[-300:]}; using RNNoise instead.")
        return _clean_rnnoise(src, out_dir)

    vocals = next(sep_dir.rglob("vocals.wav"), None)
    if not vocals:
        print("Demucs produced no vocals file; using RNNoise instead.")
        return _clean_rnnoise(src, out_dir)

    dst = out_dir / "cleaned_voice.wav"
    return dst if _run_ffmpeg(str(vocals), dst, "loudnorm") else vocals


# ---------------------------------------------------------------------------
# Entry point used by whisper_service
# ---------------------------------------------------------------------------

def prepare_audio(audio_path, mode: str | None = None) -> tuple[np.ndarray, dict]:
    """
    Returns (16 kHz mono audio for Whisper, info about what was done).
    Never fails: if cleaning isn't possible, the original audio is returned.
    """
    mode = (mode or MODE).lower()
    original = decode_audio(str(audio_path), sampling_rate=SAMPLE_RATE)
    snr = estimate_snr_db(original)
    info = {"mode": mode, "snr_db": round(snr, 1), "method": "none", "noisy": snr < NOISY_BELOW_DB}

    def finish(audio):
        # Last step for every recording: silence voices other than the main speaker.
        audio, speakers = keep_main_speaker(audio)
        info["speakers"] = speakers
        return audio, info

    if mode == "off":
        return finish(original)
    atten = None
    if mode == "auto":
        if snr >= CLEAN_ABOVE_DB:
            print(f"Audio check: {snr:.1f} dB signal-to-noise, studio-clean, no noise removal.")
            return finish(original)
        method = "deepfilter"
        if not info["noisy"]:
            atten = MODERATE_ATTEN_DB  # some background: reduce it, gently
    elif mode in ("deepfilter", "full", "rnnoise", "strong", "voice"):
        method = mode
    else:
        print(f"Unknown FMCG_DENOISE value '{mode}', using auto.")
        return prepare_audio(audio_path, "auto")

    strength = f", moderate, up to {atten} dB" if atten else ""
    print(f"Audio check: {snr:.1f} dB signal-to-noise{' (noisy)' if info['noisy'] else ''}, removing background noise ({method}{strength})…")
    with tempfile.TemporaryDirectory() as tmp:
        cleaner = {
            "deepfilter": _clean_deepfilter,
            "full": _clean_full,
            "voice": _clean_voice,
            "strong": _clean_strong,
        }.get(method, _clean_rnnoise)
        cleaned_path = (
            _clean_deepfilter(str(audio_path), Path(tmp), atten)
            if method == "deepfilter"
            else cleaner(str(audio_path), Path(tmp))
        )
        if not cleaned_path:
            return finish(original)
        cleaned = decode_audio(str(cleaned_path), sampling_rate=SAMPLE_RATE)

    # Guard: if cleaning removed nearly everything, the voice went with the noise.
    if float(np.sqrt(np.mean(cleaned ** 2))) < 0.05 * float(np.sqrt(np.mean(original ** 2))):
        print("Noise removal also removed the voice; keeping the original audio.")
        return finish(original)

    info["method"] = cleaned_path.stem.replace("cleaned_", "") if cleaned_path.stem.startswith("cleaned_") else method
    info["snr_db_after"] = round(estimate_snr_db(cleaned), 1)
    print(f"Noise removal done ({info['method']}): {snr:.1f} dB -> {info['snr_db_after']:.1f} dB.")
    return finish(cleaned)


# ---------------------------------------------------------------------------
# Listen test:  python -m app.services.audio_cleanup "C:\path\to\video.mp4"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m app.services.audio_cleanup "C:\\path\\to\\video.mp4"')
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"File not found: {src.resolve()}")
        sys.exit(1)

    out_dir = BACKEND_DIR / "noise_test"
    out_dir.mkdir(exist_ok=True)
    stem = src.stem.replace(" ", "_")

    original = decode_audio(str(src), sampling_rate=SAMPLE_RATE)
    print(f"Signal-to-noise estimate: {estimate_snr_db(original):.1f} dB "
          f"({'noisy' if estimate_snr_db(original) < NOISY_BELOW_DB else 'clean enough'})")

    _run_ffmpeg(str(src), out_dir / f"{stem}_1_original.wav", "anull")
    with tempfile.TemporaryDirectory() as tmp:
        df = _clean_deepfilter(str(src), Path(tmp))
        if df:
            shutil.copy(df, out_dir / f"{stem}_2_noise_removed.wav")
            cleaned = decode_audio(str(df), sampling_rate=SAMPLE_RATE)
            isolated, spk = keep_main_speaker(cleaned)
            import wave
            with wave.open(str(out_dir / f"{stem}_3_main_speaker_only.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes((np.clip(isolated, -1, 1) * 32767).astype(np.int16).tobytes())
            print(f"Speakers: {spk}")
        try:
            import demucs  # noqa: F401
            full = _clean_full(str(src), Path(tmp))
            if full:
                shutil.copy(full, out_dir / f"{stem}_4_music_and_noise_removed.wav")
        except ImportError:
            print("(Demucs not installed, skipping the music/TV removal version.)")

    print(f"\nListen and compare the files in: {out_dir}")