"""
The AI half of the old /video/upload function, moved here unchanged.

Steps (same order, same models, same messages as before):
    1. read the real media duration and check the 10 s .. 35 min limits
    2. extract the audio (FFmpeg)
    3. split it into 3-minute parts
    4. for every part: Whisper (with noise cleanup, main-speaker isolation and
       translation to English) and a Qwen part summary
    5. one overall Qwen summary of the whole transcript
    6. delete the temporary audio

Nothing here touches MySQL. Matching aspects to the feedback_aspects table,
rating rules, the overall rating and saving the record stay in the backend,
which receives the raw results below.
"""

from __future__ import annotations

import time
from pathlib import Path

from services.ffmpeg_service import (
    extract_audio,
    split_audio,
    get_video_duration,
    CHUNK_SECONDS,
    MIN_VIDEO_SECONDS,
    MAX_VIDEO_SECONDS,
)
from services.whisper_service import transcribe
from services.ollama_service import analyze_feedback, summarize_chunk


# ============================================================
# NO-SPEECH RESULTS (moved from app/api/video.py)
# ============================================================
# When Whisper finds no recognisable speech, Qwen must not be asked to
# analyze an empty transcript: it would invent a review from nothing.

NO_SPEECH_PART_SUMMARY = {
    "summary": "No recognisable speech was found in this part.",
    "sentiment": "Neutral",
    "key_point": "",
    "feedback": [],
}

NO_SPEECH_SUMMARY = {
    "summary": (
        "No recognisable speech was found in this recording, so it "
        "could not be analyzed. The speaker may be using a language "
        "the system does not support yet, or the audio may be too "
        "noisy or quiet."
    ),
    "sentiment": "Neutral",
}


class MediaError(Exception):
    """A problem with the uploaded file itself (shown to the user, HTTP 400)."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def format_seconds(seconds: int):
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _unreadable_reason(read_error: Exception) -> str:
    details = str(read_error).lower()
    if "moov atom not found" in details:
        return (
            "This file is incomplete: it's cut off before the end, so it can't be "
            "played or analyzed. It probably didn't finish copying, downloading or "
            "recording. Get the file again from the phone or source and upload it again."
        )
    if "invalid data" in details or "could not read" in details:
        return (
            "This file is damaged or isn't a valid video or audio file, so it can't "
            "be analyzed. Try exporting it again, or convert it to MP4 and upload it again."
        )
    return "This file couldn't be read. Try a different file, or convert it to MP4."


def analyze_media(media_path: Path, audio_dir: Path) -> dict:
    """
    Run the full AI analysis on one saved media file.

    Returns plain JSON-safe data. Raises MediaError for files that can't be
    analyzed (unreadable, too short, too long); any other exception is an
    analysis failure.
    """
    started = time.time()
    media_path = Path(media_path)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # Real duration
    # --------------------------------------------------------
    try:
        duration = get_video_duration(str(media_path))
    except Exception as read_error:
        print(f"Unreadable upload: {read_error!r}"[:500])
        raise MediaError("unreadable", _unreadable_reason(read_error))

    duration_seconds = duration["seconds"]
    duration_formatted = duration["formatted"]
    print(f"Video Length: {duration_formatted}")

    if duration_seconds < MIN_VIDEO_SECONDS:
        raise MediaError(
            "too_short",
            f"Video is shorter than the minimum supported duration of {MIN_VIDEO_SECONDS} seconds.",
        )
    if duration_seconds > MAX_VIDEO_SECONDS:
        raise MediaError(
            "too_long",
            "Video is longer than the maximum supported duration of 35 minutes.",
        )

    # --------------------------------------------------------
    # Extract and split audio
    # --------------------------------------------------------
    audio_path = audio_dir / f"{media_path.stem}.wav"
    chunks = []
    try:
        print("\nExtracting audio...")
        extract_audio(str(media_path), str(audio_path))
        print("Audio extraction completed.")

        print(f"\nSplitting video into {CHUNK_SECONDS // 60}-minute parts...")
        chunks = split_audio(str(audio_path))
        total_parts = len(chunks)
        print(f"Created {total_parts} parts.")

        transcript = []
        complete_text = []
        parts = []

        print("\n" + "=" * 70)
        print(f"STARTING ANALYSIS ({total_parts} PARTS)")
        print("=" * 70)

        for index, chunk in enumerate(chunks):
            part_start_time = time.time()
            part_number = index + 1
            start_seconds = index * CHUNK_SECONDS
            end_seconds = min(start_seconds + CHUNK_SECONDS, duration_seconds)
            progress = int((part_number / total_parts) * 100)

            print(f"\n[{part_number}/{total_parts}] {progress}%")
            print(f"Time range: {format_seconds(start_seconds)} - {format_seconds(end_seconds)}")
            print(f"Audio file: {chunk.name}")

            # Whisper transcription
            print("Transcribing...")
            result = transcribe(chunk)

            chunk_text = []
            for segment in result:
                text = segment["text"].strip()
                if not text:
                    continue
                transcript.append(
                    {
                        "start": round(segment["start"] + start_seconds, 2),
                        "end": round(segment["end"] + start_seconds, 2),
                        "text": text,
                    }
                )
                chunk_text.append(text)
                complete_text.append(text)

            part_text = " ".join(chunk_text)

            # Qwen part summary
            print(f"Analyzing Part {part_number}...")
            if not part_text.strip():
                # No recognisable speech: don't ask Qwen, it would invent feedback.
                print("No recognisable speech in this part, skipping AI analysis.")
                part_summary = dict(NO_SPEECH_PART_SUMMARY)
            else:
                part_summary = summarize_chunk(part_text)

            parts.append(
                {
                    "part": part_number,
                    "start": start_seconds,
                    "end": end_seconds,
                    "transcript": part_text,
                    # Raw Qwen output; the backend matches aspects and ratings.
                    "part_summary": part_summary,
                }
            )
            print(f"Part {part_number} completed in {round(time.time() - part_start_time, 2)}s")

        # --------------------------------------------------------
        # Overall summary
        # --------------------------------------------------------
        print("\n" + "=" * 70)
        print("GENERATING OVERALL AI SUMMARY...")
        print("=" * 70)

        complete_transcript = "\n".join(complete_text)
        speech_detected = bool(complete_transcript.strip())

        if not speech_detected:
            print("No recognisable speech in the recording, skipping AI summary.")
            summary = dict(NO_SPEECH_SUMMARY)
        else:
            summary = analyze_feedback(complete_transcript)

        return {
            "duration_seconds": duration_seconds,
            "duration_formatted": duration_formatted,
            "part_duration_seconds": CHUNK_SECONDS,
            "total_parts": total_parts,
            "speech_detected": speech_detected,
            "transcript": transcript,
            "complete_transcript": complete_transcript,
            "parts": parts,
            "summary": summary,
            "ai_processing_seconds": round(time.time() - started, 2),
        }

    finally:
        # ----------------------------------------------------
        # Clean temporary audio (as before)
        # ----------------------------------------------------
        print("\nCleaning temporary audio...")
        try:
            audio_path.unlink(missing_ok=True)
            for chunk in chunks:
                chunk.unlink(missing_ok=True)
            chunk_folder = audio_path.parent / f"{audio_path.stem}_parts"
            if chunk_folder.exists():
                chunk_folder.rmdir()
        except Exception as cleanup_error:
            print(f"Cleanup warning: {cleanup_error}")
