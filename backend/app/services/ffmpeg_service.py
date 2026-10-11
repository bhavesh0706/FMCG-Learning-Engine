from pathlib import Path
import subprocess
import json
import shutil
import wave
import re


# ============================================================
# CONFIGURATION
# ============================================================

# Each video is divided into 3-minute parts
CHUNK_SECONDS = 180

# Minimum supported media duration: 10 seconds
MIN_VIDEO_SECONDS = 10

# Maximum supported video duration
MAX_VIDEO_SECONDS = 35 * 60


# ============================================================
# FIND FFMPEG
# ============================================================

def get_ffmpeg_path():
    """
    Find the FFmpeg executable.
    """

    path = shutil.which("ffmpeg")

    if path:
        return path

    raise RuntimeError(
        "FFmpeg was not found in PATH. "
        "Run 'where.exe ffmpeg' in PowerShell."
    )


# ============================================================
# FIND FFPROBE
# ============================================================

def get_ffprobe_path():
    """
    Find the FFprobe executable.

    FFprobe is normally located in the same directory
    as FFmpeg.
    """

    # First try PATH
    path = shutil.which("ffprobe")

    if path:
        return path

    # Then look beside FFmpeg
    ffmpeg_path = shutil.which("ffmpeg")

    if ffmpeg_path:

        ffprobe_path = Path(ffmpeg_path).with_name(
            "ffprobe.exe"
        )

        if ffprobe_path.exists():
            return str(ffprobe_path)

    return None


# ============================================================
# FORMAT DURATION
# ============================================================

def format_duration(total_seconds):
    """
    Convert seconds into a clean duration object.

    Examples:

    300  -> 05:00
    600  -> 10:00
    1200 -> 20:00
    2100 -> 35:00
    """

    total_seconds = int(round(total_seconds))

    hours = total_seconds // 3600

    minutes = (
        total_seconds % 3600
    ) // 60

    seconds = (
        total_seconds % 60
    )

    if hours > 0:

        formatted = (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    else:

        formatted = (
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return {
        "seconds": total_seconds,
        "formatted": formatted
    }


# ============================================================
# GET VIDEO DURATION
# ============================================================

def get_video_duration(video_path):
    """
    Detect the duration of the uploaded video.

    Detection order:

    1. FFprobe
    2. FFmpeg metadata
    3. Temporary WAV fallback

    This supports MP4, MOV, AVI, MKV, WEBM
    and other formats that FFmpeg can read.
    """

    video_path = str(video_path)

    # --------------------------------------------------------
    # METHOD 1
    # FFPROBE
    # --------------------------------------------------------

    ffprobe = get_ffprobe_path()

    if ffprobe:

        try:

            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    video_path
                ],
                capture_output=True,
                text=True,
                check=True
            )

            data = json.loads(
                result.stdout
            )

            duration = (
                data
                .get("format", {})
                .get("duration")
            )

            if duration is not None:

                duration = float(duration)

                if duration > 0:

                    print(
                        "Duration detected using FFprobe: "
                        f"{duration:.2f} seconds"
                    )

                    return format_duration(
                        duration
                    )

        except Exception as error:

            print(
                "FFprobe duration detection failed: "
                f"{error}"
            )

    # --------------------------------------------------------
    # METHOD 2
    # FFMPEG METADATA
    # --------------------------------------------------------

    try:

        ffmpeg = get_ffmpeg_path()

        result = subprocess.run(
            [
                ffmpeg,
                "-i",
                video_path
            ],
            capture_output=True,
            text=True
        )

        output = result.stderr

        match = re.search(
            r"Duration:\s*"
            r"(\d+):"
            r"(\d+):"
            r"(\d+(?:\.\d+)?)",
            output
        )

        if match:

            hours = int(
                match.group(1)
            )

            minutes = int(
                match.group(2)
            )

            seconds = float(
                match.group(3)
            )

            total_seconds = (
                hours * 3600
                + minutes * 60
                + seconds
            )

            if total_seconds > 0:

                print(
                    "Duration detected using FFmpeg: "
                    f"{total_seconds:.2f} seconds"
                )

                return format_duration(
                    total_seconds
                )

    except Exception as error:

        print(
            "FFmpeg duration detection failed: "
            f"{error}"
        )

    # --------------------------------------------------------
    # METHOD 3
    # TEMPORARY AUDIO FALLBACK
    # --------------------------------------------------------

    temp_audio = (
        Path(video_path)
        .with_suffix(".duration_test.wav")
    )

    try:

        ffmpeg = get_ffmpeg_path()

        print(
            "Using audio-duration fallback..."
        )

        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(temp_audio)
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )

        # Read duration from WAV
        with wave.open(
            str(temp_audio),
            "rb"
        ) as wav:

            frames = wav.getnframes()

            sample_rate = (
                wav.getframerate()
            )

            if sample_rate <= 0:

                raise RuntimeError(
                    "Invalid WAV sample rate."
                )

            duration = (
                frames /
                float(sample_rate)
            )

        if duration <= 0:

            raise RuntimeError(
                "Extracted audio has zero duration."
            )

        print(
            "Duration detected from audio: "
            f"{duration:.2f} seconds"
        )

        return format_duration(
            duration
        )

    except subprocess.CalledProcessError as error:

        ffmpeg_error = ""

        if error.stderr:

            ffmpeg_error = (
                error.stderr[-3000:]
            )

        raise RuntimeError(
            "FFmpeg could not read the video.\n"
            f"{ffmpeg_error}"
        )

    except Exception as error:

        raise RuntimeError(
            "Could not determine video duration: "
            f"{error}"
        )

    finally:

        try:

            if temp_audio.exists():
                temp_audio.unlink()

        except Exception:
            pass


# ============================================================
# EXTRACT AUDIO
# ============================================================

def extract_audio(
    video_path,
    audio_path
):
    """
    Extract the audio track from the video.

    Output:

    16 kHz
    Mono
    WAV
    PCM
    """

    ffmpeg = get_ffmpeg_path()

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            audio_path
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )


# ============================================================
# SPLIT AUDIO INTO 3-MINUTE PARTS
# ============================================================

def split_audio(
    audio_path,
    chunk_seconds=CHUNK_SECONDS
):
    """
    Split the extracted audio into 3-minute parts.

    Example:

    20 minutes
    -> 7 parts

    25 minutes
    -> 9 parts

    30 minutes
    -> 10 parts

    35 minutes
    -> 12 parts
    """

    audio_path = Path(
        audio_path
    )

    output_dir = (
        audio_path.parent
        / f"{audio_path.stem}_parts"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # REMOVE OLD PARTS
    # --------------------------------------------------------

    for old_file in output_dir.glob(
        "part_*.wav"
    ):

        try:

            old_file.unlink()

        except Exception:

            pass

    # --------------------------------------------------------
    # OUTPUT FILE PATTERN
    # --------------------------------------------------------

    pattern = (
        output_dir
        / "part_%03d.wav"
    )

    # --------------------------------------------------------
    # SPLIT USING FFMPEG
    # --------------------------------------------------------

    ffmpeg = get_ffmpeg_path()

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(audio_path),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            "-c:a",
            "pcm_s16le",
            str(pattern)
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )

    # --------------------------------------------------------
    # FIND CREATED PARTS
    # --------------------------------------------------------

    chunks = sorted(
        output_dir.glob(
            "part_*.wav"
        )
    )

    if not chunks:

        raise RuntimeError(
            "FFmpeg did not create any audio parts."
        )

    print(
        f"Created {len(chunks)} audio parts."
    )

    return chunks