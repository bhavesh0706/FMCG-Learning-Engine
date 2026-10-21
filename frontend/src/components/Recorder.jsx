import { useCallback, useEffect, useRef, useState } from "react";
import fixWebmDuration from "fix-webm-duration";
import { confirmDialog } from "./ConfirmDialog";

const MAX_SECONDS = 35 * 60; // matches the 5–35 minute limit of the analysis
const SUGGESTED_MIN = 5 * 60;

const MIME_CANDIDATES = {
  video: ["video/mp4;codecs=avc1,mp4a", "video/mp4", "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"],
  audio: ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"],
};

function pickMimeType(kind) {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
  return MIME_CANDIDATES[kind].find((t) => MediaRecorder.isTypeSupported(t)) || "";
}

function extensionFor(mime, kind) {
  if (/mp4/.test(mime)) return kind === "audio" ? "m4a" : "mp4";
  if (/ogg/.test(mime)) return "ogg";
  return "webm";
}

// "audio-recording-2026-09-30-0033.webm" – the "audio-recording" prefix lets the app show an audio player later.
function recordingName(kind, mime, date = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  const stamp = `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}`;
  return `${kind}-recording-${stamp}.${extensionFor(mime, kind)}`;
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${m}:${sec}` : `${m}:${sec}`;
}

function sizeLabel(bytes) {
  const mb = (bytes || 0) / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round((bytes || 0) / 1024))} KB`;
}

function permissionMessage(err) {
  const name = err?.name || "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Camera or microphone access is blocked. Allow it from the icon at the left of the address bar, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No camera or microphone was found on this device.";
  if (name === "NotReadableError")
    return "The camera or microphone is being used by another app. Close that app and try again.";
  return err?.message || "Recording couldn't start on this device.";
}

/* ---------- Icons ---------- */

const MicIcon = ({ size = 20 }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
    <rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

const CameraIcon = ({ size = 20 }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
    <rect x="3" y="6.5" width="12.5" height="11" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M15.5 10.5l5-3v9l-5-3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
  </svg>
);

const SwitchIcon = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
    <path d="M4 9a8 8 0 0114-3l2 2M20 15a8 8 0 01-14 3l-2-2M20 4v4h-4M4 20v-4h4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/* ---------- Recorder ---------- */

// analyzeLabel: text of the main button after recording (default "Analyze now").
// onSave: optional; without it the "Save for later" button is hidden (used by the survey flow).
export default function Recorder({ online, onAnalyze, onSave, analyzeLabel = "Analyze now" }) {
  const [kind, setKind] = useState(null); // "audio" | "video"
  const [phase, setPhase] = useState("choose"); // choose | starting | ready | recording | paused | review
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [facing, setFacing] = useState("user");
  const [canSwitch, setCanSwitch] = useState(false);
  const [result, setResult] = useState(null); // { blob, url, mimeType, kind, duration, name }
  const [saveState, setSaveState] = useState(""); // "" | "saving" | "saved" | "error"

  const streamRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const previewRef = useRef(null);
  const canvasRef = useRef(null);
  const audioCtxRef = useRef(null);
  const rafRef = useRef(0);
  const tickRef = useRef(0);
  const startedAtRef = useRef(0);
  const accumulatedRef = useRef(0);

  const supported = typeof window !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

  /* ----- Stream & level meter ----- */

  const stopMeter = () => {
    cancelAnimationFrame(rafRef.current);
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
  };

  const stopStream = useCallback(() => {
    stopMeter();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const startMeter = (stream) => {
    stopMeter();
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      audioCtxRef.current = ctx;
      const data = new Uint8Array(analyser.frequencyBinCount);

      const draw = () => {
        const canvas = canvasRef.current;
        if (canvas) {
          const dpr = window.devicePixelRatio || 1;
          const w = canvas.clientWidth;
          const h = canvas.clientHeight;
          if (canvas.width !== w * dpr) {
            canvas.width = w * dpr;
            canvas.height = h * dpr;
          }
          const g = canvas.getContext("2d");
          g.setTransform(dpr, 0, 0, dpr, 0, 0);
          g.clearRect(0, 0, w, h);
          analyser.getByteFrequencyData(data);
          const bars = Math.max(12, Math.floor(w / 7));
          const gap = 3;
          const bw = (w - gap * (bars - 1)) / bars;
          g.fillStyle = getComputedStyle(canvas).color || "#0b5d52";
          for (let i = 0; i < bars; i += 1) {
            const v = data[Math.floor((i / bars) * data.length * 0.7)] / 255;
            const bh = Math.max(3, v * h);
            g.beginPath();
            g.roundRect ? g.roundRect(i * (bw + gap), (h - bh) / 2, bw, bh, bw / 2) : g.rect(i * (bw + gap), (h - bh) / 2, bw, bh);
            g.fill();
          }
        }
        rafRef.current = requestAnimationFrame(draw);
      };
      draw();
    } catch {
      /* the meter is decoration; recording still works without it */
    }
  };

  const openStream = async (nextKind, facingMode = facing) => {
    setError("");
    setPhase("starting");
    stopStream();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: nextKind === "video" ? { facingMode, width: { ideal: 1280 }, height: { ideal: 720 } } : false,
      });
      streamRef.current = stream;
      startMeter(stream);
      if (nextKind === "video") {
        const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
        setCanSwitch(devices.filter((d) => d.kind === "videoinput").length > 1);
      }
      setPhase("ready");
    } catch (err) {
      console.error(err);
      setError(permissionMessage(err));
      setPhase("choose");
    }
  };

  // Show the live camera in the preview element whenever it is on screen.
  useEffect(() => {
    if (previewRef.current && streamRef.current && kind === "video" && phase !== "review") {
      previewRef.current.srcObject = streamRef.current;
    }
  }, [phase, kind]);

  /* ----- Timer ----- */

  const currentElapsed = () =>
    accumulatedRef.current + (startedAtRef.current ? (Date.now() - startedAtRef.current) / 1000 : 0);

  const startTicking = () => {
    clearInterval(tickRef.current);
    tickRef.current = setInterval(() => {
      const t = currentElapsed();
      setElapsed(t);
      if (t >= MAX_SECONDS) stopRecording();
    }, 250);
  };

  /* ----- Recording controls ----- */

  const startRecording = () => {
    const stream = streamRef.current;
    if (!stream) return;
    const mimeType = pickMimeType(kind);
    const options = mimeType ? { mimeType } : {};
    if (kind === "video") options.videoBitsPerSecond = 2_000_000;
    options.audioBitsPerSecond = 128_000;

    let recorder;
    try {
      recorder = new MediaRecorder(stream, options);
    } catch {
      recorder = new MediaRecorder(stream);
    }

    chunksRef.current = [];
    recorder.ondataavailable = (e) => e.data?.size && chunksRef.current.push(e.data);
    recorder.onstop = async () => {
      clearInterval(tickRef.current);
      const duration = currentElapsed();
      startedAtRef.current = 0;
      const type = recorder.mimeType || mimeType || (kind === "video" ? "video/webm" : "audio/webm");
      let blob = new Blob(chunksRef.current, { type });
      // Chrome's WebM files have no duration inside them; add it so players and the backend can read it.
      if (/webm/.test(type)) {
        try {
          blob = await fixWebmDuration(blob, Math.round(duration * 1000), { logger: false });
        } catch {
          /* keep the original blob */
        }
      }
      stopStream();
      setResult({ blob, url: URL.createObjectURL(blob), mimeType: type, kind, duration, name: recordingName(kind, type) });
      setSaveState("");
      setPhase("review");
    };

    recorderRef.current = recorder;
    accumulatedRef.current = 0;
    startedAtRef.current = Date.now();
    setElapsed(0);
    recorder.start(1000);
    startTicking();
    setPhase("recording");
  };

  const pauseRecording = () => {
    const r = recorderRef.current;
    if (r?.state !== "recording") return;
    r.pause();
    accumulatedRef.current = currentElapsed();
    startedAtRef.current = 0;
    clearInterval(tickRef.current);
    setPhase("paused");
  };

  const resumeRecording = () => {
    const r = recorderRef.current;
    if (r?.state !== "paused") return;
    r.resume();
    startedAtRef.current = Date.now();
    startTicking();
    setPhase("recording");
  };

  // Declared as a function so the timer can call it.
  function stopRecording() {
    const r = recorderRef.current;
    if (r && r.state !== "inactive") r.stop();
  }

  const discard = () => {
    if (result?.url) URL.revokeObjectURL(result.url);
    setResult(null);
    setElapsed(0);
    openStream(kind);
  };

  const backToChoice = () => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.onstop = null;
      recorderRef.current.stop();
    }
    clearInterval(tickRef.current);
    stopStream();
    if (result?.url) URL.revokeObjectURL(result.url);
    setResult(null);
    setKind(null);
    setPhase("choose");
    setElapsed(0);
  };

  const switchCamera = () => {
    const next = facing === "user" ? "environment" : "user";
    setFacing(next);
    openStream("video", next);
  };

  const makeFile = () => new File([result.blob], result.name, { type: result.mimeType });

  const handleSave = async () => {
    setSaveState("saving");
    try {
      await onSave({ name: result.name, blob: result.blob, mimeType: result.mimeType, kind: result.kind, duration: result.duration });
      setSaveState("saved");
    } catch (err) {
      console.error(err);
      setSaveState("error");
    }
  };

  // Phones: open the share sheet so the user can "Save video" to the gallery. Computers: download the file.
  const saveCopy = async () => {
    const f = makeFile();
    try {
      if (navigator.canShare?.({ files: [f] })) {
        await navigator.share({ files: [f], title: "Product review recording" });
        return;
      }
    } catch (err) {
      if (err?.name === "AbortError") return;
    }
    const a = document.createElement("a");
    a.href = result.url;
    a.download = result.name;
    a.click();
  };

  /* ----- Safety: warn before leaving with an unsaved recording, clean up on unmount ----- */

  useEffect(() => {
    const unsaved = phase === "recording" || phase === "paused" || (phase === "review" && saveState !== "saved");
    if (!unsaved) return undefined;
    const warn = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [phase, saveState]);

  useEffect(
    () => () => {
      clearInterval(tickRef.current);
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.onstop = null;
        recorderRef.current.stop();
      }
      stopStream();
    },
    [stopStream]
  );

  useEffect(() => () => result?.url && URL.revokeObjectURL(result.url), [result]);

  /* ----- Render ----- */

  if (!supported) {
    return (
      <div className="recorder">
        <div className="recorder-body recorder-center">
          <p className="recorder-title">Recording isn't available here</p>
          <p className="recorder-sub">
            Use a recent version of Chrome, Edge or Safari. The app also has to be opened over https (or on localhost) for the
            browser to allow the camera and microphone.
          </p>
        </div>
      </div>
    );
  }

  if (phase === "choose" || (phase === "starting" && !kind)) {
    return (
      <div className="recorder">
        <div className="recorder-body">
          <p className="recorder-title">What would you like to record?</p>
          <p className="recorder-sub">Talk about the product: taste, packaging, price, and anything you'd change.</p>

          <div className="record-options">
            <button
              type="button"
              className="record-option"
              onClick={() => {
                setKind("audio");
                openStream("audio");
              }}
            >
              <span className="record-option-icon"><MicIcon size={24} /></span>
              <strong>Voice only</strong>
              <span>Uses the microphone. Smaller file, quicker to upload on a weak network.</span>
            </button>
            <button
              type="button"
              className="record-option"
              onClick={() => {
                setKind("video");
                openStream("video");
              }}
            >
              <span className="record-option-icon"><CameraIcon size={24} /></span>
              <strong>Video with voice</strong>
              <span>Uses the camera and microphone, so you can show the product while you talk.</span>
            </button>
          </div>

          {error && <p className="alert alert-error">{error}</p>}
          <p className="recorder-hint">Recordings can be up to 35 minutes. The browser will ask for permission first.</p>
        </div>
      </div>
    );
  }

  const isVideo = kind === "video";
  const live = phase === "recording" || phase === "paused";

  if (phase === "review" && result) {
    const short = result.duration < SUGGESTED_MIN;
    return (
      <div className="recorder">
        <div className="recorder-head">
          <span className="recorder-kind">
            {isVideo ? <CameraIcon size={16} /> : <MicIcon size={16} />}
            {isVideo ? "Video recording" : "Voice recording"}
          </span>
          <span className="recorder-meta">
            <span>{clock(result.duration)}</span>
            <span>{sizeLabel(result.blob.size)}</span>
          </span>
        </div>

        <div className="recorder-body">
          {isVideo ? (
            <video className="recorder-stage" src={result.url} controls playsInline />
          ) : (
            <div className="recorder-audio-review">
              <span className="recorder-audio-icon"><MicIcon size={24} /></span>
              <audio src={result.url} controls />
            </div>
          )}

          {short && (
            <p className="recorder-note">
              This recording is under 5 minutes. It can still be analyzed, but longer reviews give more detailed results.
            </p>
          )}

          {saveState === "saved" ? (
            <div className="recorder-saved">
              <strong>Saved on this device</strong>
              <span>You'll find it under "Saved recordings" below. Analyze it whenever you have a good connection.</span>
              <div className="recorder-actions">
                <button type="button" className="btn btn-primary" onClick={backToChoice}>
                  Record another
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="recorder-actions">
                <button type="button" className="btn btn-primary" onClick={() => onAnalyze(makeFile())} disabled={!online}>
                  {online ? analyzeLabel : "Offline, can't analyze yet"}
                </button>
                {onSave && (
                  <button type="button" className="btn btn-secondary" onClick={handleSave} disabled={saveState === "saving"}>
                    {saveState === "saving" ? "Saving…" : "Save for later"}
                  </button>
                )}
              </div>
              <div className="recorder-links">
                <button type="button" className="link-btn" onClick={saveCopy}>
                  Save a copy to this phone or computer
                </button>
                <button type="button" className="link-btn is-danger" onClick={discard}>
                  Discard and record again
                </button>
              </div>
              {!online && (
                <p className="recorder-note">
                  You're offline. Save the recording now and analyze it from "Saved recordings" once you're back on a good
                  network.
                </p>
              )}
              {saveState === "error" && (
                <p className="alert alert-error">
                  The recording couldn't be saved in the app, possibly because the device is low on storage. Use "Save a
                  copy" so you don't lose it.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  // ready / starting / recording / paused
  return (
    <div className={`recorder${live ? " is-live" : ""}`}>
      <div className="recorder-head">
        <span className="recorder-kind">
          {isVideo ? <CameraIcon size={16} /> : <MicIcon size={16} />}
          {isVideo ? "Video with voice" : "Voice only"}
        </span>
        {live ? (
          <span className={`rec-badge${phase === "paused" ? " is-paused" : ""}`}>
            <i />
            {phase === "paused" ? "Paused" : "Recording"}
          </span>
        ) : (
          <button type="button" className="link-btn" onClick={backToChoice}>
            Change
          </button>
        )}
      </div>

      <div className="recorder-body">
        {isVideo ? (
          <div className="recorder-stage-wrap">
            <video ref={previewRef} className="recorder-stage" autoPlay muted playsInline data-facing={facing} />
            {phase === "starting" && <span className="recorder-overlay">Turning on the camera…</span>}
            <div className="recorder-stage-foot">
              <canvas ref={canvasRef} className="level-meter is-small" aria-hidden="true" />
              {canSwitch && phase === "ready" && (
                <button type="button" className="stage-btn" onClick={switchCamera} aria-label="Switch camera">
                  <SwitchIcon />
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="recorder-audio-stage">
            <canvas ref={canvasRef} className="level-meter" aria-hidden="true" />
            <p className="recorder-audio-status">
              {phase === "starting" ? "Turning on the microphone…" : live ? "Speak normally, the bars show your voice" : "Say something to test the microphone"}
            </p>
          </div>
        )}

        <div className="recorder-timer" aria-live="off">
          <span className="timer-value">{clock(elapsed)}</span>
          <span className="timer-limit">of 35:00 max</span>
        </div>

        <div className="recorder-controls">
          {phase === "ready" && (
            <button type="button" className="rec-btn" onClick={startRecording} aria-label="Start recording">
              <span className="rec-dot" />
            </button>
          )}
          {live && (
            <>
              {phase === "recording" ? (
                <button type="button" className="ctrl-btn" onClick={pauseRecording}>
                  Pause
                </button>
              ) : (
                <button type="button" className="ctrl-btn" onClick={resumeRecording}>
                  Resume
                </button>
              )}
              <button type="button" className="rec-btn is-stop" onClick={stopRecording} aria-label="Stop recording">
                <span className="rec-square" />
              </button>
              <button
                type="button"
                className="ctrl-btn is-ghost"
                onClick={async () => {
                  const ok = await confirmDialog({
                    title: "Discard this recording?",
                    message: "Recording will stop and what you've recorded so far will be deleted.",
                    confirmLabel: "Discard",
                    cancelLabel: "Keep recording",
                    danger: true,
                  });
                  if (ok) backToChoice();
                }}
              >
                Cancel
              </button>
            </>
          )}
        </div>
        {phase === "ready" && <p className="recorder-hint">Press the red button to start. You can pause at any time.</p>}
      </div>
    </div>
  );
}