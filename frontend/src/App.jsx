// Must stay the first import: it adds the sign-in token to every API client.
import { getSession, onSessionChange, refreshMe, signOut, withToken } from "./services/auth";
import { Component, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import "./App.css";
import {
  uploadVideo,
  getFeedbackHistory,
  getFeedbackById,
  getFeedbackVideoUrl,
  API_BASE_URL,
  getCategories,
  getProducts,
  createRespondent,
  createSurveyResponse,
  saveSurveyAnswers,
  submitSurveyResponse,
} from "./services/feedbackApi";
import { downloadFeedbackPdf } from "./services/feedbackReport";
import {
  saveRecording,
  listRecordings,
  deleteRecording,
  requestPersistentStorage,
  recordingToFile,
} from "./services/recordingStore";
import Recorder from "./components/Recorder";
import SurveyWorkflow from "./components/SurveyWorkflow";
import { ConfirmHost, confirmDialog, TrapFocus } from "./components/ConfirmDialog";
import {
  getProductFeedbackList,
  getProductFeedback,
  generateConsolidatedReport,
  getUnlinkedFeedback,
  getRecentSurveys,
  deleteSurvey,
} from "./services/productFeedbackApi";
import { getTeam, signOutMember, updateTeamMember } from "./services/teamApi";
import ActivityLog from "./components/ActivityLog";
import CatalogPage from "./components/CatalogPage";
import OrgPortal from "./components/OrgPortal";
import LoginPage from "./components/LoginPage";
import NotificationBell from "./components/NotificationBell";
import { buildClientSections, clientHeadline, downloadConsolidatedPdf, normalizeNarrative } from "./services/consolidatedReportPdf";
import axios from "axios";
import { AddMemberDialog, AssignProductsDialog, CredentialsDialog, ResetPasswordDialog } from "./components/TeamDialogs";
import { startAnalysis, waitForAnalysis, announceDataChanged, DATA_CHANGED } from "./services/analysisApi";


/* ================================================================
   Helpers
   ================================================================ */

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${String(m).padStart(2, "0")}:${s}`;
}

// 22 -> "22 sec", 245 -> "4 min 05 sec"
function formatDuration(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s < 60) return `${s} sec`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return h ? `${h} hr ${m} min` : `${m} min ${rest} sec`;
}

// Short form for stats: 3725 -> "1 hr 2 min", 312 -> "5 min"
function formatTotal(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s < 60) return `${s} sec`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return h ? `${h} hr ${m} min` : `${m} min`;
}

const plural = (n, word) => `${n} ${word}${Number(n) === 1 ? "" : "s"}`;

function formatSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
}

// The backend stores UTC without a timezone marker. Treat such values as UTC
// so the local time shown is correct (otherwise IST shows 5.5 hours early).
function parseDate(value) {
  if (!value) return null;
  const s = String(value).trim();
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(s);
  const d = new Date(hasZone ? s : `${s.replace(" ", "T")}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatDateTime(value) {
  const d = parseDate(value);
  if (!d) return "—";
  return d.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timeAgo(value) {
  const d = parseDate(value);
  if (!d) return "";
  const diff = (d.getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const units = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, secs] of units) {
    if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
  }
  return "just now";
}

// Keyword-based tagging of praise and issues. Negative wins when both match.
const NEGATIVE = [
  /melt/i, /\btor[en]\b/i, /\btear/i, /sticky/i, /sticking/i, /\bbroke/i, /leak/i,
  /expensive/i, /costly/i, /(price|cost).{0,20}(high|more)/i,
  /(higher|more).{0,12}(price|cost)/i, /damag/i, /\bbad\b/i, /\bpoor/i,
  /difficult/i, /disappoint/i, /stale/i, /complain/i, /problem/i,
  /\bissues?\b/i, /\bnot (a |an |very |that |so |really |at all )?(good|great|nice|tasty|happy|satisfied|pleased|impressed|worth)/i,
  /\b(dislikes?|disliked|unsatisf|dissatisf|hate[sd]?|worst|terrible|awful)/i,
];
// Polite criticism: customers often say "could be better" instead of "bad".
const IMPROVEMENT = [
  /\b(does ?n[o']t|doesn't|did ?n[o']t|do ?n[o']t|not) (last|stay|work|help|clean|remove|suit|lather|dissolve)/i,
  /\b(could|can|should|needs? to|would|must) be (made )?(a (little|bit) |slightly |much |even |a lot )?(better|improved|stronger|more|less|cheaper|bigger|smaller|easier)/i,
  /\b(needs?|requires?|could use|scope for|room for|rooms for) (some |a little |more |further )?(improvement|work|changes?)/i,
  /\bwith (a )?(little|bit of|some|slight) improvement/i,
  /\b(lacks?|lacking|missing)\b/i,
  /\bnot (enough|sufficient|long[- ]lasting|strong enough|good enough)\b/i,
  /\btoo (oily|greasy|dry|sweet|salty|strong|weak|small|thin|expensive|costly|hard|harsh|watery|runny|bitter)\b/i,
  /\b(wish|would like|would prefer|hope) (it|they|the)\b/i,
  /\b(drawback|downside|shortcoming|weak point)s?\b/i,
];

// Compliments phrased with "not": "does not feel too dry", "no residue".
const POSITIVE_NEGATION = [
  /\b(does ?n[o']t|doesn't|do ?n[o']t|not|never) (feel |feels |make (it |hair |skin |the hair |my hair |the skin |my skin )?(feel )?|leave (any )?|get )?(too |very |so |overly )?(dry|greasy|oily|sticky|harsh|heavy|bitter|itchy|rough|frizzy)/i,
  /\bno (residue|stickiness|irritation|side effects?|smell|aftertaste|mess|leak(age)?)\b/i,
];

const POSITIVE = [
  /\bgood\b/i, /\bgreat\b/i, /smooth/i, /\blov(e|ed)\b/i, /enjoy/i, /tasty/i,
  /delicious/i, /balanced/i, /fresh/i, /recommend/i, /\bprefer/i, /sturdy/i,
  /excellent/i, /\bnice\b/i, /\bhappy\b/i, /\bliked?\b/i, /(?<!dis)satisf/i, /\beasy\b/i,
];

// Filler lines like "No specific issues or suggestions provided" or "None".
const PLACEHOLDER =
  /^\s*(none|n\/?a|nil|-+|nothing( (specific|notable|in particular|significant))?( (mentioned|provided|noted|reported))?|no (specific |major |particular |other |notable |clear |significant )?(issues?|problems?|complaints?|concerns?|suggestions?|feedback|praise|positives?|negatives?|actions?|recommendations?)\b.*)\.?\s*$/i;
const isPlaceholder = (line) => PLACEHOLDER.test(String(line || ""));

// "no issues / no problems" says nothing negative.
const NO_ISSUE = /\b(no|not any|without any|zero) (specific |major |real |particular |other )?(issues?|problems?|complaints?|concerns?)\b/gi;

function classify(text) {
  text = String(text || "").replace(NO_ISSUE, " ");
  // A compliment like "does not feel too dry" must not be read as a complaint.
  const cleaned = POSITIVE_NEGATION.reduce((t, r) => t.replace(r, " "), text);
  const praisedByNegation = cleaned !== text;
  if (NEGATIVE.some((r) => r.test(cleaned)) || IMPROVEMENT.some((r) => r.test(cleaned))) return "negative";
  if (praisedByNegation || POSITIVE.some((r) => r.test(cleaned))) return "positive";
  return "neutral";
}

const SENTIMENTS = {
  positive: { tone: "positive", label: "Positive" },
  concerns: { tone: "mixed", label: "Positive, with concerns" },
  mixed: { tone: "mixed", label: "Mixed" },
  negative: { tone: "negative", label: "Negative" },
  neutral: { tone: "neutral", label: "Neutral" },
};

// Reads an "Overall sentiment" style sentence and turns it into a tone.
function sentimentFromText(text) {
  const t = (text || "").toLowerCase();
  if (!t) return null;
  const pos = /positive|(?<!dis)satisf|happy|pleased|favou?rable|impressed/.test(t);
  const neg = /negative|dissatisf|unhappy|disappoint|frustrat|unsatisf/.test(t);
  if (/mixed/.test(t) || (pos && neg)) return SENTIMENTS.mixed;
  if (neg) return SENTIMENTS.negative;
  if (pos) return /(but|however|concern|issue|some areas|improvement)/.test(t) ? SENTIMENTS.concerns : SENTIMENTS.positive;
  if (/neutral/.test(t)) return SENTIMENTS.neutral;
  return null;
}

// Backend labels such as "Praise", "Issue", "Neutral" -> tone.
function sentimentFromLabel(label) {
  const l = (label || "").toLowerCase();
  if (/positive|praise/.test(l)) return SENTIMENTS.positive;
  if (/negative|issue/.test(l)) return SENTIMENTS.negative;
  if (/mixed|suggestion|concern/.test(l)) return SENTIMENTS.mixed;
  return SENTIMENTS.neutral;
}

// A part that contains both praise and issues is "Mixed", whatever the backend label says.
function partLabel(part) {
  const text = `${part?.transcript || ""} ${part?.summary || ""}`;
  const hasNeg = NEGATIVE.some((r) => r.test(text)) || IMPROVEMENT.some((r) => r.test(text));
  const hasPos = POSITIVE.some((r) => r.test(text)) || POSITIVE_NEGATION.some((r) => r.test(text));
  if (hasPos && hasNeg) return "Mixed";
  return part?.sentiment || (hasNeg ? "Issue" : hasPos ? "Praise" : "Neutral");
}

const partTone = (part) => sentimentFromLabel(partLabel(part)).tone;

// Plain-text summary -> sections (headings end in ":", bullets start with "-").
// The backend may send the summary as plain text, markdown, or a JSON object/array.
// Turn every shape into "Heading:" lines with "- item" bullets.
const titleCase = (key) =>
  String(key)
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

function itemToText(item) {
  if (item == null) return "";
  if (typeof item === "string" || typeof item === "number") return String(item);
  if (typeof item === "object") {
    const main = item.text || item.point || item.description || item.summary || item.action || item.issue || item.aspect_name || item.aspect || "";
    const extra = item.evidence && main !== item.evidence ? ` (${item.evidence})` : "";
    return main ? `${main}${extra}` : Object.values(item).filter((v) => typeof v === "string").join(" - ");
  }
  return "";
}

function summaryToText(summary) {
  if (!summary) return "";
  if (typeof summary === "string") {
    const trimmed = summary.trim();
    // A JSON string saved as text
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return summaryToText(JSON.parse(trimmed));
      } catch {
        /* not JSON, keep as text */
      }
    }
    return summary;
  }
  if (Array.isArray(summary)) return summary.map((i) => `- ${itemToText(i)}`).join("\n");
  if (typeof summary === "object") {
    return Object.entries(summary)
      .map(([key, value]) => {
        if (value == null || value === "") return "";
        if (Array.isArray(value)) {
          if (!value.length) return "";
          return `${titleCase(key)}:\n${value.map((v) => `- ${itemToText(v)}`).join("\n")}`;
        }
        if (typeof value === "object") return `${titleCase(key)}:\n${summaryToText(value)}`;
        return `${titleCase(key)}:\n${value}`;
      })
      .filter(Boolean)
      .join("\n\n");
  }
  return String(summary);
}

function parseSummary(text) {
  const sections = [];
  let current = { heading: null, paragraphs: [], items: [] };
  const push = () => {
    if (current.heading || current.paragraphs.length || current.items.length) {
      sections.push(current);
    }
  };

  summaryToText(text)
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .forEach((raw) => {
      const isBoldLine = /^\*\*[^*]+\*\*:?$/.test(raw);
      const isMdHeading = /^#{1,6}\s+/.test(raw);
      const line = raw.replace(/^#{1,6}\s+/, "").replace(/\*\*/g, "").trim();
      if (!line) return;

      if (/^([-•*]|\d+[.)])\s+/.test(line)) {
        current.items.push(line.replace(/^([-•*]|\d+[.)])\s+/, ""));
      } else if (isMdHeading || isBoldLine || (line.endsWith(":") && line.length < 60)) {
        push();
        current = { heading: line.replace(/:$/, ""), paragraphs: [], items: [] };
      } else {
        // "Overall Sentiment: The customer is..." on one line
        const inline = line.match(/^([A-Za-z][A-Za-z /&-]{2,40}):\s+(.+)$/);
        if (inline && /sentiment|overview|summary|verdict/i.test(inline[1])) {
          push();
          current = { heading: inline[1], paragraphs: [inline[2]], items: [] };
        } else {
          current.paragraphs.push(line);
        }
      }
    });

  push();
  return sections;
}

function buildInsights(summary, counts, feedbackItems = [], tone = null) {
  const out = { overview: [], positives: [], issues: [], actions: [], notes: [], other: [] };

  const sortItem = (line) => {
    const tone = classify(line);
    if (tone === "negative") out.issues.push(line);
    else if (tone === "positive") out.positives.push(line);
    else out.notes.push(line);
  };

  parseSummary(summary).forEach((sec) => {
    const h = (sec.heading || "").toLowerCase();
    const lines = [...sec.paragraphs, ...sec.items];

    if (!sec.heading || /sentiment|overview|verdict|summary|conclusion/.test(h)) {
      out.overview.push(
        ...sec.paragraphs.filter(
          (p) => !/^(praise|positive|negative|neutral|mixed|issue|suggestion|positive, with concerns)\.?$/i.test(p.trim())
        )
      );
      sec.items.forEach(sortItem);
    } else if (/action|recommend|next step|suggest/.test(h)) {
      out.actions.push(...lines);
    } else if (/issue|problem|concern|complaint|negative|pain|improve|weak|drawback|dislike|\bcons\b|challenge/.test(h)) {
      out.issues.push(...lines);
    } else if (/positive|strength|liked|praise|highlight|\bpros\b|what worked|appreciat/.test(h)) {
      out.positives.push(...lines);
    } else if (/feedback|finding|insight|point|theme|observation/.test(h)) {
      lines.forEach(sortItem);
    } else {
      out.other.push(sec);
    }
  });

  // When the summary only has an overview sentence, fill the lists from the
  // per-part customer feedback the backend already returns.
  // Qwen's per-part analysis rates each feedback area. Use it to fill any list
  // the summary left empty (for example, a summary with praise but no issues).
  if (feedbackItems.length) {
    const fillPositives = !out.positives.length;
    const fillIssues = !out.issues.length;
    const fillNotes = fillPositives && fillIssues;
    const seen = new Set();
    feedbackItems.forEach((f) => {
      const name = f.aspect_name || f.aspect || "";
      if (!name) return;
      const text = f.evidence ? `${name}: ${f.evidence}` : name;
      const tone = sentimentFromLabel(f.sentiment).tone;
      const rating = Number(f.rating);
      const kind =
        tone === "negative" || (Number.isFinite(rating) && rating < 3)
          ? "issue"
          : tone === "positive" || (Number.isFinite(rating) && rating >= 4)
          ? "positive"
          : "note";
      const key = `${kind}:${name.toLowerCase()}`;
      if (seen.has(key)) return;
      seen.add(key);
      if (kind === "issue" && fillIssues) out.issues.push(text);
      else if (kind === "positive" && fillPositives) out.positives.push(text);
      else if (kind === "note" && fillNotes) out.notes.push(text);
      else return;
      out.derived = true;
    });
  }

  // Drop filler lines so "No specific issues provided" isn't shown as an issue.
  ["positives", "issues", "actions", "notes"].forEach((k) => {
    out[k] = out[k].filter((line) => !isPlaceholder(line));
  });

  // The shared rule decides (same label as the lists and reports); keyword counting
  // only for old records with no rating, label or aspects.
  let sentiment = tone ? SENTIMENTS[tone] || null : null;
  if (!sentiment) sentiment = sentimentFromText(out.overview.join(" "));
  if (!sentiment) {
    const total = counts.positive + counts.negative;
    if (!total) sentiment = SENTIMENTS.neutral;
    else if (counts.positive >= counts.negative * 2) sentiment = { tone: "positive", label: "Mostly positive" };
    else if (counts.negative >= counts.positive * 2) sentiment = { tone: "negative", label: "Mostly negative" };
    else sentiment = SENTIMENTS.mixed;
  }

  out.sentiment = sentiment;
  return out;
}

/*
  THE sentiment rule for one customer's feedback, used on every screen.
  Exact copy of the backend's response_tone() (app/services/consolidation.py) and
  the mobile toneForFeedback(): change all three together.
    1. The rating decides: 4+ positive, under 2.5 negative, in between mixed;
       clear-cut only if the aspects agree.
    2. No rating: the overall label (Praise / Issue / Suggestion) and the aspects.
*/
function feedbackTone(rating, label, items = []) {
  const s = (x) => String(x || "").trim().toLowerCase();
  const pos = items.filter((i) => s(i.sentiment) === "positive").length;
  const neg = items.reduce((n, i) => n + (s(i.sentiment) === "negative" ? 1 : s(i.sentiment) === "positive with concerns" ? 0.5 : 0), 0);
  const r = Number(rating);
  if (Number.isFinite(r) && r > 0) {
    if (r >= 4) return neg < pos || !neg ? "positive" : "mixed";
    if (r < 2.5) return pos <= neg / 2 ? "negative" : "mixed";
    return "mixed";
  }
  const l = s(label);
  if (l === "praise" || l === "positive") return neg < pos || !neg ? "positive" : "mixed";
  if (l === "issue" || l === "negative") return pos <= neg / 2 ? "negative" : "mixed";
  if (l === "suggestion" || l === "mixed") return "mixed";
  if (pos && neg) return "mixed";
  return pos ? "positive" : neg ? "negative" : "neutral";
}

const sentimentLabelOf = (r) =>
  r?.overall_sentiment || r?.sentiment || (typeof r?.summary === "object" ? r?.summary?.sentiment : "") || "";

// Sentiment for a saved record: the same rule as everywhere else.
function recordSentiment(record) {
  const items = (record?.chapters || []).flatMap((c) => c.feedback || []);
  if (Number(record?.overall_rating) > 0 || sentimentLabelOf(record) || items.length) {
    return SENTIMENTS[feedbackTone(record?.overall_rating, sentimentLabelOf(record), items)] || SENTIMENTS.neutral;
  }
  // Old records with nothing to go on: what the summary says.
  if (record?.summary) {
    const ov = parseSummary(record.summary)
      .filter((s) => !s.heading || /sentiment|overview|verdict/i.test(s.heading))
      .flatMap((s) => s.paragraphs)
      .join(" ");
    const fromText = sentimentFromText(ov);
    if (fromText) return fromText;
  }
  return sentimentFromLabel(record?.sentiment);
}

function countTones(lines) {
  return lines.reduce(
    (acc, line) => {
      acc[classify(line)] += 1;
      return acc;
    },
    { positive: 0, neutral: 0, negative: 0 }
  );
}

// Transcript from the backend may be a list of segments or one long string.
function transcriptLines(transcript) {
  if (Array.isArray(transcript)) return transcript.map((s) => s.text || "").filter(Boolean);
  return splitSentences(transcript);
}

function splitSentences(text) {
  return (text || "")
    .replace(/\s*\n\s*/g, " ")
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// Reflow hard-wrapped transcript text into readable paragraphs of a few sentences.
function toParagraphs(text, size = 4) {
  const sentences = splitSentences(text);
  const out = [];
  for (let i = 0; i < sentences.length; i += size) out.push(sentences.slice(i, i + size).join(" "));
  return out;
}

// Shapes page data into what the PDF report expects.
function buildReport({ filename, date, duration, processingTime, sentiment, insights, parts, partEndFn, transcript, overallRating, aspectRatings }) {
  const rating = toRating(overallRating);
  const partRows = (parts || []).map((p) => ({
    part: p.part,
    range: `${formatTime(p.start)} - ${formatTime(partEndFn(p))}`,
    label: partLabel(p),
    tone: partTone(p),
    summary: p.summary,
    keyPoint: p.key_point,
    feedback: (p.feedback || []).map((f) => ({
      name: f.aspect_name || f.aspect || "Customer feedback",
      category: f.category,
      rating: Number(f.rating),
      sentiment: f.sentiment,
      severity: f.severity,
      evidence: f.evidence,
    })),
  }));

  let t;
  if (Array.isArray(transcript) && transcript.length) {
    t = { type: "timed", lines: transcript.map((s) => ({ time: formatTime(s.start), text: s.text || "" })) };
  } else if ((parts || []).some((p) => p.transcript)) {
    t = {
      type: "blocks",
      blocks: parts.map((p) => ({
        label: `Part ${p.part}  (${formatTime(p.start)} - ${formatTime(partEndFn(p))})`,
        paragraphs: toParagraphs(p.transcript, 5),
      })),
    };
  } else if (typeof transcript === "string" && transcript.trim()) {
    t = { type: "blocks", blocks: [{ label: null, paragraphs: toParagraphs(transcript, 5) }] };
  }

  return {
    filename: filename || "Customer feedback video",
    meta: [
      { label: "Video length", value: duration ? formatDuration(duration) : "" },
      { label: date.label, value: date.value },
      { label: "Parts analyzed", value: parts?.length ? plural(parts.length, "part") : "" },
      { label: "Overall sentiment", value: sentiment.label, tone: sentiment.tone },
      ...(Number.isFinite(rating)
        ? [{ label: "Overall rating", value: `${rating.toFixed(1)} / 5`, tone: ratingTone(rating) }]
        : []),
    ],
    ratings: (aspectRatings || []).map((a) => ({
      name: a.aspect_name || a.aspect_key,
      category: a.category || "General",
      rating: Number(a.rating),
    })),
    insights,
    parts: partRows,
    transcript: t,
    processingTime,
  };
}

// Voice-only files get an audio player instead of a black video frame.
const isAudioFile = (file) => Boolean(file?.type?.startsWith("audio/"));
const isAudioName = (name) => /^audio-recording|\.(m4a|mp3|wav|ogg|oga|opus|aac)$/i.test(name || "");

// Ratings are 1–5. A missing rating (null, "", 0) means "not rated", not 0.0.
const toRating = (v) => {
  const n = v === null || v === undefined || v === "" ? NaN : Number(v);
  return n > 0 ? n : NaN;
};

const baseName = (name) => (name || "video").replace(/\.[^.]+$/, "");

/* ================================================================
   Small components
   ================================================================ */

function Highlight({ text, query }) {
  const q = (query || "").trim();
  if (!q) return text;
  const safe = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.split(new RegExp(`(${safe})`, "gi")).map((part, i) =>
    part.toLowerCase() === q.toLowerCase() ? <mark key={i}>{part}</mark> : part
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <path d="M5 10.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronIcon({ dir = "right" }) {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" style={dir === "left" ? { transform: "rotate(180deg)" } : undefined}>
      <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PlayGlyph({ size = 16 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path d="M8 5.5v13l10-6.5z" fill="currentColor" />
    </svg>
  );
}

function SentimentPill({ sentiment, small = false }) {
  return (
    <span className={`sentiment-pill tone-${sentiment.tone}${small ? " is-small" : ""}`}>{sentiment.label}</span>
  );
}

// Star colour follows the score: 4+ green, 3+ amber, below 3 red.
const ratingTone = (value) => (value >= 4 ? "positive" : value >= 3 ? "mixed" : "negative");

// Fractional stars: 4.3 fills 4 stars and a third of the fifth.
function RatingStars({ rating, showValue = true, compact = false }) {
  const value = Number(rating);
  if (!Number.isFinite(value)) return null;
  const clamped = Math.max(0, Math.min(5, value));

  return (
    <span className={`rating-stars tone-${ratingTone(clamped)}${compact ? " is-compact" : ""}`}>
      <span className="rating-stars-icons" role="img" aria-label={`${clamped.toFixed(1)} out of 5`}>
        <span className="rating-stars-base" aria-hidden="true">★★★★★</span>
        <span className="rating-stars-fill" aria-hidden="true" style={{ width: `${(clamped / 5) * 100}%` }}>
          ★★★★★
        </span>
      </span>
      {showValue && (
        <strong className="rating-value">
          {clamped.toFixed(1)}
          <span> / 5</span>
        </strong>
      )}
    </span>
  );
}

function OverallRatingCard({ rating, areas = 0 }) {
  const value = Number(rating);
  if (!Number.isFinite(value)) return null;
  return (
    <section className={`overall-rating-card tone-${ratingTone(value)}`}>
      <div className="overall-rating-score">
        <span className="overall-rating-number">{Math.max(0, Math.min(5, value)).toFixed(1)}</span>
        <span className="overall-rating-outof">out of 5</span>
      </div>
      <div className="overall-rating-copy">
        <span className="overall-rating-label">Overall product rating</span>
        <span className="overall-rating-description">
          Based on the customer feedback captured from the video
          {areas > 0 ? `, across ${plural(areas, "feedback area")}.` : "."}
        </span>
      </div>
      <RatingStars rating={value} showValue={false} />
    </section>
  );
}

function AspectRatingsPanel({ ratings }) {
  if (!ratings?.length) return null;
  return (
    <article className="panel aspect-ratings-panel">
      <div className="panel-head">
        <h2 className="panel-title">Ratings by feedback area</h2>
        <span className="muted">{plural(ratings.length, "area")}</span>
      </div>
      <ul className="aspect-ratings-list">
        {ratings.map((item, i) => (
          <li key={item.aspect_key || item.aspect_name || i} className="aspect-rating-row">
            <span className="aspect-rating-info">
              <strong>{item.aspect_name || item.aspect_key}</strong>
              <span>{item.category || "General"}</span>
            </span>
            <RatingStars rating={item.rating} compact />
          </li>
        ))}
      </ul>
    </article>
  );
}

function FeedbackRatingList({ feedback = [], title = "Customer feedback" }) {
  if (!feedback.length) return null;
  return (
    <section className="feedback-rating-section">
      <h3 className="feedback-rating-heading">
        {title}
        <span className="count">{feedback.length}</span>
      </h3>
      <div className="feedback-rating-list">
        {feedback.map((item, index) => {
          const tone = sentimentFromLabel(item.sentiment).tone;
          return (
            <article
              key={`${item.aspect_key || item.aspect || "feedback"}-${index}`}
              className={`feedback-rating-item tone-${tone}`}
            >
              <div className="feedback-rating-top">
                <div className="feedback-rating-title">
                  <strong>{item.aspect_name || item.aspect || "Customer feedback"}</strong>
                  {item.category && <span className="feedback-rating-category">{item.category}</span>}
                </div>
                <RatingStars rating={item.rating} compact />
              </div>
              <div className="feedback-rating-meta">
                <span className={`feedback-rating-sentiment tone-${tone}`}>{item.sentiment || "Neutral"}</span>
                {item.severity && <span>{item.severity}</span>}
                {item.mentions > 1 && <span>{item.mentions} mentions</span>}
              </div>
              {item.evidence && <p className="feedback-rating-evidence">“{item.evidence}”</p>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function MomentMap({ segments, duration, currentTime, onSeek }) {
  if (!duration) return null;

  const handleTrackClick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onSeek(((e.clientX - rect.left) / rect.width) * duration);
  };

  return (
    <div className="moment-map">
      <div className="moment-head">
        <span className="moment-title">Feedback moments</span>
        <span className="legend">
          <span><i className="dot dot-positive" />Praise</span>
          <span><i className="dot dot-negative" />Issue</span>
        </span>
      </div>
      <div className="moment-track" onClick={handleTrackClick}>
        {segments
          .filter((s) => s.tone !== "neutral")
          .map((s, i) => (
            <button
              key={i}
              type="button"
              className={`moment moment-${s.tone}`}
              style={{
                left: `${(s.start / duration) * 100}%`,
                width: `max(4px, ${((s.end - s.start) / duration) * 100}%)`,
              }}
              title={`${formatTime(s.start)}  ${s.text}`}
              aria-label={`${s.tone === "positive" ? "Praise" : "Issue"} at ${formatTime(s.start)}`}
              onClick={(e) => {
                e.stopPropagation();
                onSeek(s.start);
              }}
            />
          ))}
        <span className="playhead" style={{ left: `${Math.min(100, (currentTime / duration) * 100)}%` }} />
      </div>
      <div className="moment-scale">
        <span>00:00</span>
        <span>{formatTime(duration)}</span>
      </div>
    </div>
  );
}

// One coloured block per analysed part, sized by its length. Click to jump.
function PartsTimeline({ parts, duration, currentTime, onSeek }) {
  if (!duration || !parts.length) return null;
  return (
    <div className="moment-map">
      <div className="moment-head">
        <span className="moment-title">Parts timeline</span>
        <span className="legend">
          <span><i className="dot dot-positive" />Praise</span>
          <span><i className="dot dot-mixed" />Mixed</span>
          <span><i className="dot dot-negative" />Issue</span>
        </span>
      </div>
      <div className="parts-track">
        {parts.map((p) => {
          const start = Number(p.start) || 0;
          const end = Math.min(Number(p.end) || duration, duration);
          const active = currentTime >= start && currentTime < end;
          return (
            <button
              key={p.part}
              type="button"
              className={`parts-block tone-${partTone(p)}${active ? " is-active" : ""}`}
              style={{ flexGrow: Math.max(end - start, 1) }}
              onClick={() => onSeek(start)}
              title={`Part ${p.part}, ${formatTime(start)}–${formatTime(end)}`}
            >
              Part {p.part}
            </button>
          );
        })}
        <span className="playhead" style={{ left: `${Math.min(100, (currentTime / duration) * 100)}%` }} />
      </div>
      <div className="moment-scale">
        <span>00:00</span>
        <span>{formatTime(duration)}</span>
      </div>
    </div>
  );
}

function SentimentBar({ counts }) {
  const total = counts.positive + counts.neutral + counts.negative;
  if (!total) return null;
  const pct = (n) => `${(n / total) * 100}%`;

  return (
    <div className="sentiment-bar">
      <div
        className="bar"
        role="img"
        aria-label={`${counts.positive} praise, ${counts.neutral} neutral, ${counts.negative} issue lines`}
      >
        <span className="bar-positive" style={{ width: pct(counts.positive) }} />
        <span className="bar-neutral" style={{ width: pct(counts.neutral) }} />
        <span className="bar-negative" style={{ width: pct(counts.negative) }} />
      </div>
      <div className="bar-legend">
        <span><i className="dot dot-positive" />{counts.positive} praise</span>
        <span><i className="dot dot-neutral" />{counts.neutral} neutral</span>
        <span><i className="dot dot-negative" />{counts.negative} issues</span>
      </div>
    </div>
  );
}

// Tick for liked points, "!" for issues, arrow for actions.
function InsightIcon({ tone }) {
  if (tone === "negative") {
    return (
      <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
        <path d="M10 5v6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="10" cy="14.5" r="1.3" fill="currentColor" />
      </svg>
    );
  }
  if (tone === "action") {
    return (
      <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
        <path d="M5 10h9m-3.5-3.5L14 10l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return <CheckIcon />;
}

function InsightList({ title, tone, items }) {
  if (!items.length) return null;
  return (
    <section className={`insight insight-${tone}`}>
      <h3>
        {title}
        <span className="count">{items.length}</span>
      </h3>
      <ul>
        {items.map((item, i) => (
          <li key={i}>
            <span className="insight-icon"><InsightIcon tone={tone} /></span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function InsightsPanel({ insights, counts, hasSummary }) {
  return (
    <article className="panel insights-panel">
      <h2 className="panel-title">AI summary</h2>

      {insights.overview.length > 0 && (
        <div className="overview">
          {insights.overview.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      )}

      <SentimentBar counts={counts} />

      <InsightList title="What customers liked" tone="positive" items={insights.positives} />
      <InsightList title="Issues raised" tone="negative" items={insights.issues} />
      <InsightList title="Recommended actions" tone="action" items={insights.actions} />
      <InsightList title="Other notes" tone="neutral" items={insights.notes} />

      {insights.other.map((sec, i) => (
        <InsightList key={i} title={sec.heading} tone="neutral" items={[...sec.paragraphs, ...sec.items]} />
      ))}

      {!hasSummary && <p className="empty">No summary was returned for this video.</p>}

      <p className="footnote">
        {insights.derived
          ? "Liked and issue points are taken from the customer feedback found in each part."
          : "Praise and issue tags are based on keywords in the transcript."}
      </p>
    </article>
  );
}

/* ================================================================
   Feedback history: stats, list and detail
   ================================================================ */

function HistorySkeleton() {
  return (
    <div className="panel feedback-list">
      {[0, 1, 2].map((i) => (
        <div key={i} className="feedback-row is-skeleton">
          <span className="sk sk-thumb" />
          <span className="sk sk-line" />
          <span className="sk sk-short" />
          <span className="sk sk-short" />
        </div>
      ))}
    </div>
  );
}

function HistoryList({ items, loading, error, onOpen, onRefresh, onAnalyze }) {
  const [q, setQ] = useState("");
  const [tone, setTone] = useState("all");
  const [sort, setSort] = useState("newest");

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    const list = items
      .map((it) => ({ ...it, _sentiment: recordSentiment(it), _date: parseDate(it.uploaded_at) }))
      .filter((it) => (query ? `${it.filename} ${it.id}`.toLowerCase().includes(query) : true))
      .filter((it) => (tone === "all" ? true : it._sentiment.tone === tone));

    list.sort((a, b) => {
      if (sort === "longest") return (Number(b.video_duration_seconds) || 0) - (Number(a.video_duration_seconds) || 0);
      const da = a._date?.getTime() || 0;
      const db = b._date?.getTime() || 0;
      return sort === "oldest" ? da - db : db - da;
    });
    return list;
  }, [items, q, tone, sort]);

  const toneCount = (k) => items.filter((it) => recordSentiment(it).tone === k).length;

  return (
    <section className="feedback-history">
      <div className="history-header">
        <div>
          <p className="eyebrow">Admin</p>
          <h1>Survey history</h1>
          <p className="history-subtitle">Every customer feedback video analyzed so far, with its summary, parts and transcript.</p>
        </div>
        <div className="history-header-actions">
          <button type="button" className="btn btn-secondary" onClick={onRefresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button type="button" className="btn btn-primary" onClick={onAnalyze}>
            Analyze a new video
          </button>
        </div>
      </div>

      {error && <p className="alert alert-error">{error}</p>}

      {loading && items.length === 0 ? (
        <HistorySkeleton />
      ) : items.length === 0 && !error ? (
        <div className="panel history-empty">
          <div className="history-empty-icon"><PlayGlyph size={24} /></div>
          <h2>No feedback videos yet</h2>
          <p>Analyze your first customer feedback video and it will show up here.</p>
          <button type="button" className="btn btn-primary" onClick={onAnalyze}>
            Analyze a video
          </button>
        </div>
      ) : (
        items.length > 0 && (
          <>
            <div className="history-toolbar">
              <input
                type="search"
                className="search"
                placeholder="Search by file name or ID"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                aria-label="Search feedback"
              />
              <div className="segmented">
                {[
                  ["all", "All", items.length],
                  ["positive", "Positive", toneCount("positive")],
                  ["mixed", "Mixed", toneCount("mixed")],
                  ["negative", "Negative", toneCount("negative")],
                ].map(([key, label, n]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={tone === key}
                    className={tone === key ? "is-on" : ""}
                    onClick={() => setTone(key)}
                  >
                    {label} <span className="seg-count">{n}</span>
                  </button>
                ))}
              </div>
              <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="longest">Longest video</option>
              </select>
            </div>

            <div className="panel feedback-list">
              <div className="feedback-list-head">
                <span>Video</span>
                <span>Uploaded</span>
                <span>Length</span>
                <span>Sentiment</span>
                <span aria-hidden="true" />
              </div>

              {rows.length === 0 ? (
                <p className="empty list-empty">No videos match these filters.</p>
              ) : (
                rows.map((item) => (
                  <button key={item.id} type="button" className="feedback-row" onClick={() => onOpen(item.id)}>
                    <span className={`row-thumb tone-${item._sentiment.tone}`}>
                      <PlayGlyph size={20} />
                    </span>
                    <span className="row-name">
                      <strong title={item.filename}>{item.filename}</strong>
                      <span>Feedback #{item.id}</span>
                    </span>
                    <span className="row-date">
                      <span>{timeAgo(item.uploaded_at) || "—"}</span>
                      <small>{formatDateTime(item.uploaded_at)}</small>
                    </span>
                    <span className="row-length">
                      {item.video_duration_seconds != null ? formatDuration(item.video_duration_seconds) : "—"}
                    </span>
                    <span className="row-sentiment">
                      <SentimentPill sentiment={item._sentiment} small />
                      {Number.isFinite(toRating(item.overall_rating)) && (
                        <RatingStars rating={item.overall_rating} compact />
                      )}
                    </span>
                    <span className="row-chevron"><ChevronIcon /></span>
                  </button>
                ))
              )}
            </div>
          </>
        )
      )}
    </section>
  );
}

/* ---------- Re-analysis ----------
   Backend contract (see the handover note):
     POST {API_BASE_URL}/feedback/{id}/reanalyze
       -> 202 { job_id, status, position }   same shape as a new analysis
       -> 409 { detail }  already being analyzed
     The job re-runs the pipeline on the STORED file, overwrites this feedback
     record in place (same id, same survey link) and marks the product's
     consolidated report stale so the worker rebuilds it from the latest results.
     Polling uses the existing /analysis/jobs/{job_id} through waitForAnalysis. */

const reanalyzeUrl = (id) => `${API_BASE_URL}/feedback/${id}/reanalyze`;

async function reanalyzeFeedback(id, onJob) {
  const { data: queued } = await axios.post(reanalyzeUrl(id));
  onJob?.({ status: queued?.status || "queued", position: queued?.position, elapsed_seconds: 0 });
  if (queued?.job_id != null) await waitForAnalysis(queued.job_id, onJob);
  return getFeedbackById(id); // fresh record with the new results
}

function reanalyzeError(err) {
  const detail = err?.response?.data?.detail;
  const status = err?.response?.status;
  if (err?.analysisFailed) return `${err.message} The previous results are unchanged.`;
  if (status === 404 && err?.config?.url?.includes("/reanalyze")) {
    return "The backend doesn't support re-analysis yet. Update the backend, then try again.";
  }
  if (status === 409) return typeof detail === "string" ? detail : "This recording is already being analyzed.";
  if (!navigator.onLine || err?.code === "ERR_NETWORK") {
    return "Lost contact with the server. If the re-analysis had started it keeps running; reopen this page in a few minutes.";
  }
  return typeof detail === "string"
    ? detail
    : "The recording couldn't be re-analyzed. The previous results are unchanged. Check the backend log for details.";
}

function RefreshGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path d="M20 11a8 8 0 00-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0014.3 4.9L20 16M20 20v-4h-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FeedbackDetail({ record, onBack, onRecordUpdated }) {
  const videoRef = useRef(null);
  const [videoError, setVideoError] = useState(false);
  const [videoDuration, setVideoDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);
  // Re-analysis: idle | running | done | error. The previous results stay on screen until it finishes.
  const [redo, setRedo] = useState({ status: "idle", job: null, error: "" });
  const reanalyzing = redo.status === "running";
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const parts = record.chapters || [];
  const duration = videoDuration || Number(record.video_duration_seconds) || 0;
  const lines = useMemo(() => transcriptLines(record.transcript), [record.transcript]);
  const counts = useMemo(() => countTones(lines), [lines]);
  const partFeedback = useMemo(() => (record.chapters || []).flatMap((c) => c.feedback || []), [record.chapters]);
  const insights = useMemo(
    () =>
      buildInsights(
        record.summary || "",
        counts,
        partFeedback,
        feedbackTone(record.overall_rating, sentimentLabelOf(record), partFeedback)
      ),
    [record.summary, record.overall_rating, counts, partFeedback] // eslint-disable-line react-hooks/exhaustive-deps
  );
  const sentiment = insights.sentiment;
  const aspectRatings = record.aspect_ratings || [];
  const overallRating = toRating(record.overall_rating);

  const partEnd = (p) => {
    const end = Number(p?.end) || 0;
    return duration ? Math.min(end, duration) : end;
  };

  const seekTo = (t) => {
    const v = videoRef.current;
    if (v && !videoError) {
      v.currentTime = t;
      v.play().catch(() => {});
      v.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    setCurrentTime(t);
  };

  const copySummary = async () => {
    try {
      await navigator.clipboard.writeText(summaryToText(record.summary));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard not available */
    }
  };

  const reanalyze = async () => {
    if (reanalyzing) return;
    const ok = await confirmDialog({
      title: "Re-analyze this recording?",
      message: `${record.filename} is analyzed again from the stored file. Its rating, summary, parts and transcript are replaced with the new results, and the consolidated report for this product is rebuilt with them. This takes a few minutes.`,
      confirmLabel: "Re-analyze",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    setRedo({ status: "running", job: { status: "queued" }, error: "" });
    try {
      const updated = await reanalyzeFeedback(record.id, (j) => {
        if (mounted.current) setRedo((r) => ({ ...r, job: j }));
      });
      announceDataChanged(); // lists, product page and consolidated report reload
      onRecordUpdated?.(updated);
      if (mounted.current) setRedo({ status: "done", job: null, error: "" });
    } catch (err) {
      console.error(err);
      if (mounted.current) setRedo({ status: "error", job: null, error: reanalyzeError(err) });
    }
  };

  const downloadReport = () => {
    const report = buildReport({
      filename: record.filename,
      date: { label: "Uploaded", value: formatDateTime(record.uploaded_at) },
      duration,
      processingTime: record.processing_time_seconds,
      sentiment,
      insights,
      parts,
      partEndFn: partEnd,
      transcript: record.transcript,
      overallRating: record.overall_rating,
      aspectRatings: record.aspect_ratings,
    });
    downloadFeedbackPdf(report, `${baseName(record.filename)}-feedback-report.pdf`);
  };

  // Transcript blocks: by part when available (each block can seek the video), otherwise reflowed paragraphs.
  const transcriptBlocks = useMemo(() => {
    if (Array.isArray(record.transcript)) {
      return record.transcript.map((s, i) => ({ key: i, start: Number(s.start) || 0, label: formatTime(s.start), text: s.text || "" }));
    }
    if (parts.some((p) => p.transcript)) {
      return parts.map((p) => ({ key: p.part, start: Number(p.start) || 0, label: `Part ${p.part}`, text: toParagraphs(p.transcript, 5).join("\n\n") }));
    }
    return toParagraphs(record.transcript, 5).map((t, i) => ({ key: i, start: null, label: null, text: t }));
  }, [record.transcript, parts]);

  const visibleBlocks = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? transcriptBlocks.filter((b) => b.text.toLowerCase().includes(q)) : transcriptBlocks;
  }, [transcriptBlocks, query]);

  return (
    <section className="feedback-detail">
      <button type="button" className="back-link" onClick={onBack}>
        <ChevronIcon dir="left" /> Survey history
      </button>

      <div className="results-head">
        <div className="results-title">
          <p className="eyebrow">Feedback #{record.id}</p>
          <h1 className="file-name">{record.filename}</h1>
          <div className="file-meta">
            <span>Uploaded {formatDateTime(record.uploaded_at)}</span>
            {duration > 0 && <span>{formatDuration(duration)} long</span>}
            {parts.length > 0 && <span>{plural(parts.length, "part")} analyzed</span>}
            {record.processing_time_seconds != null && (
              <span>Analyzed in {formatDuration(record.processing_time_seconds)}</span>
            )}
            {record.reanalyzed_at && <span>Re-analyzed {timeAgo(record.reanalyzed_at)}</span>}
          </div>
        </div>
        <div className="results-actions">
          <SentimentPill sentiment={sentiment} />
          <button
            type="button"
            className="btn btn-secondary"
            onClick={reanalyze}
            disabled={reanalyzing}
            title="Run the analysis again on this recording and update its results"
          >
            {reanalyzing ? <span className="spinner btn-spinner" aria-hidden="true" /> : <RefreshGlyph />}
            {reanalyzing ? "Re-analyzing…" : "Re-analyze"}
          </button>
          <button type="button" className="btn btn-secondary" onClick={copySummary} disabled={!record.summary}>
            {copied ? "Summary copied" : "Copy summary"}
          </button>
          <button type="button" className="btn btn-secondary" onClick={downloadReport}>
            Download PDF report
          </button>
        </div>
      </div>

      {redo.status === "running" && (
        <div className="reanalyze-bar is-running" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <div>
            <strong>
              {redo.job?.status === "queued"
                ? `Waiting to re-analyze${redo.job?.position ? ` · #${redo.job.position} in queue` : ""}`
                : `Re-analyzing this recording${redo.job?.elapsed_seconds ? ` · ${formatDuration(redo.job.elapsed_seconds)}` : "…"}`}
            </strong>
            <span>
              The results below are from the previous analysis and are replaced when this finishes. You can leave this page; it keeps running on the server.
            </span>
          </div>
        </div>
      )}
      {redo.status === "done" && (
        <div className="reanalyze-bar is-done" role="status">
          <span className="reanalyze-icon" aria-hidden="true"><CheckIcon /></span>
          <div>
            <strong>Re-analyzed. The results below are new.</strong>
            <span>The consolidated report for this product is being rebuilt with them.</span>
          </div>
          <button type="button" className="chapter-close" aria-label="Dismiss" onClick={() => setRedo({ status: "idle", job: null, error: "" })}>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
        </div>
      )}
      {redo.status === "error" && (
        <p className="alert alert-error reanalyze-error" role="alert">{redo.error}</p>
      )}

      <OverallRatingCard rating={overallRating} areas={aspectRatings.length} />

      <div className="results-grid">
        <div className="col-main">
          <article className="panel video-panel">
            {!videoError && isAudioName(record.filename) ? (
              <div className="audio-frame">
                <span className="audio-frame-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="24" height="24"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
                </span>
                <audio
                  ref={videoRef}
                  src={withToken(getFeedbackVideoUrl(record.id))}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => setVideoDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
                  onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                  onError={() => setVideoError(true)}
                />
              </div>
            ) : !videoError ? (
              <div className="video-frame">
                <video
                  ref={videoRef}
                  src={withToken(getFeedbackVideoUrl(record.id))}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => setVideoDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
                  onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                  onError={() => setVideoError(true)}
                />
              </div>
            ) : (
              <div className="video-fallback">
                <p>The original video can't be played in this browser.</p>
                <p className="muted">
                  MOV files recorded on phones often use a format Chrome can't play.{" "}
                  <a href={withToken(getFeedbackVideoUrl(record.id))} download>
                    Download the video
                  </a>{" "}
                  to watch it.
                </p>
              </div>
            )}
            <PartsTimeline parts={parts} duration={duration} currentTime={currentTime} onSeek={seekTo} />
          </article>

          {parts.length > 0 && (
            <article className="panel">
              <div className="panel-head">
                <h2 className="panel-title">Parts analyzed</h2>
                <span className="muted">{plural(parts.length, "part")}</span>
              </div>
              <ol className="part-list">
                {parts.map((p) => {
                  const label = partLabel(p);
                  return (
                    <li key={p.part} className={`part-card tone-${partTone(p)}`}>
                      <div className="part-card-head">
                        <span className="part-index">{p.part}</span>
                        <div className="part-card-title">
                          <strong>Part {p.part}</strong>
                          <span>{formatTime(p.start)}–{formatTime(partEnd(p))}</span>
                        </div>
                        <SentimentPill sentiment={{ tone: partTone(p), label }} small />
                      </div>
                      {p.summary && <p className="part-summary">{p.summary}</p>}
                      {p.key_point && (
                        <p className="part-takeaway">
                          <strong>Key takeaway</strong>
                          {p.key_point}
                        </p>
                      )}
                      <FeedbackRatingList feedback={p.feedback || []} title="Customer feedback" />
                      {!videoError && (
                        <button type="button" className="part-play" onClick={() => seekTo(Number(p.start) || 0)}>
                          <PlayGlyph /> Play from {formatTime(p.start)}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ol>
            </article>
          )}

          <article className="panel transcript-panel">
            <div className="transcript-head">
              <h2 className="panel-title">Complete transcript</h2>
              <div className="transcript-tools">
                <input
                  type="search"
                  className="search"
                  placeholder="Search the transcript"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Search the transcript"
                />
              </div>
            </div>

            {transcriptBlocks.length === 0 ? (
              <p className="empty">No transcript was saved for this video.</p>
            ) : visibleBlocks.length === 0 ? (
              <p className="empty">No lines match your search.</p>
            ) : (
              <div className="history-transcript-body">
                {visibleBlocks.map((b) => (
                  <div key={b.key} className="t-block">
                    {b.label && (
                      <button
                        type="button"
                        className="t-label"
                        onClick={() => b.start != null && seekTo(b.start)}
                        disabled={videoError}
                      >
                        {b.label}
                      </button>
                    )}
                    <p><Highlight text={b.text} query={query} /></p>
                  </div>
                ))}
              </div>
            )}
          </article>
        </div>

        <aside className="col-side">
          <InsightsPanel insights={insights} counts={counts} hasSummary={Boolean(record.summary)} />
          <AspectRatingsPanel ratings={aspectRatings} />
        </aside>
      </div>
    </section>
  );
}

function SavedRecordings({ items, online, busy, onAnalyze, onDelete }) {
  if (!items.length) return null;

  const download = (item) => {
    const url = URL.createObjectURL(item.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = item.name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <section className="saved-recordings">
      <div className="recent-head">
        <div>
          <h2>Saved recordings</h2>
          <p className="saved-sub">
            {online
              ? "You're online. Analyze these one at a time; each takes a few minutes."
              : "Waiting for a connection. They're safe on this device until you analyze them."}
          </p>
        </div>
        <span className={`net-chip${online ? " is-online" : ""}`}>
          <i />
          {online ? "Online" : "Offline"}
        </span>
      </div>

      <ul className="saved-list">
        {items.map((item) => (
          <li key={item.id} className="saved-item">
            <span className={`saved-icon is-${item.kind}`} aria-hidden="true">
              {item.kind === "video" ? (
                <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="6.5" width="12.5" height="11" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M15.5 10.5l5-3v9l-5-3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>
              ) : (
                <svg viewBox="0 0 24 24" width="20" height="20"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
              )}
            </span>
            <span className="saved-body">
              <strong>{item.kind === "video" ? "Video recording" : "Voice recording"}</strong>
              <span className="recent-meta">
                <span>{timeAgo(item.createdAt)}</span>
                <span>{formatDuration(item.duration)}</span>
                <span>{formatSize(item.size)}</span>
              </span>
            </span>
            <span className="saved-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => onAnalyze(item)}
                disabled={!online || busy}
                title={online ? "Upload and analyze" : "Connect to the internet to analyze"}
              >
                Analyze
              </button>
              <button type="button" className="icon-btn" onClick={() => download(item)} aria-label="Download a copy" title="Download a copy">
                <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M10 3v10m0 0l-4-4m4 4l4-4M4 16h12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
              <button
                type="button"
                className="icon-btn is-danger"
                onClick={async () => {
                  const ok = await confirmDialog({
                    title: "Delete this recording?",
                    message: "It's saved only on this device and hasn't been analyzed, so it can't be recovered.",
                    confirmLabel: "Delete",
                    danger: true,
                  });
                  if (ok) onDelete(item);
                }}
                aria-label="Delete recording"
                title="Delete"
              >
                <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecentAnalyses({ items, onOpen, onViewAll }) {
  const recent = useMemo(
    () =>
      [...items]
        .sort((a, b) => (parseDate(b.uploaded_at)?.getTime() || 0) - (parseDate(a.uploaded_at)?.getTime() || 0))
        .slice(0, 3),
    [items]
  );
  if (!recent.length) return null;

  return (
    <section className="recent">
      <div className="recent-head">
        <h2>Recently analyzed</h2>
        <button type="button" className="link-btn" onClick={onViewAll}>
          View all {items.length} <ChevronIcon />
        </button>
      </div>
      <div className="recent-grid">
        {recent.map((it) => {
          const s = recordSentiment(it);
          return (
            <button key={it.id} type="button" className="recent-card" onClick={() => onOpen(it.id)}>
              <span className={`row-thumb tone-${s.tone}`}><PlayGlyph size={20} /></span>
              <span className="recent-body">
                <strong title={it.filename}>{it.filename}</strong>
                <span className="recent-meta">
                  <span>{timeAgo(it.uploaded_at)}</span>
                  {it.video_duration_seconds != null && <span>{formatTotal(it.video_duration_seconds)}</span>}
                </span>
                <SentimentPill sentiment={s} small />
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/* ================================================================
   App
   ================================================================ */

/* ================================================================
   Error boundary: a rendering error shows a message, not a blank page
   ================================================================ */

/* ================================================================
   Page cache: returning to a page shows its last data instantly
   (no zeros or "Loading…"), then refreshes quietly in the background.
   ================================================================ */

const pageCache = {};

/* ================================================================
   Auto-refresh: lists stay current without clicking Refresh
   ================================================================ */

// Re-runs `reload` every `ms`, when the tab becomes visible again, and as soon as
// data changes elsewhere (an analysis finished, a record was saved).
function useAutoRefresh(reload, ms = 30000) {
  const ref = useRef(reload);
  useEffect(() => {
    ref.current = reload;
  });
  useEffect(() => {
    const run = () => {
      if (document.visibilityState === "visible") ref.current({ silent: true });
    };
    const timer = setInterval(run, ms);
    document.addEventListener("visibilitychange", run);
    window.addEventListener("focus", run);
    window.addEventListener(DATA_CHANGED, run);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", run);
      window.removeEventListener("focus", run);
      window.removeEventListener(DATA_CHANGED, run);
    };
  }, [ms]);
}

/* ================================================================
   Home dashboard: the first page
   ================================================================ */

const listOf = (data, key) =>
  Array.isArray(data) ? data : Array.isArray(data?.[key]) ? data[key] : Array.isArray(data?.data?.[key]) ? data.data[key] : [];

// Each product is in one of three states.
// Each product: needs questions, has feedback (its report updates by itself), or none yet.
function productState(p) {
  if (!p.survey_ready) return "setup";
  return (Number(p.responses_collected) || 0) > 0 ? "feedback" : "new";
}

const STATE_LABEL = { feedback: "Has feedback", new: "No feedback yet", setup: "Needs questions" };
const STATE_TONE = { feedback: "ok", new: "neutral", setup: "warn" };

/* ---------- Table paging: "Rows per page" 10 / 20 / 50 / All ----------
   Each table remembers its own choice on this device (localStorage), so a
   person who picks 50 on Survey history keeps 50 there after a refresh. */
const PAGE_SIZES = [10, 20, 50, 0]; // 0 = all rows on one page
const DEFAULT_PAGE_SIZE = 20;

function usePageSize(table) {
  const key = `fmcg.pageSize.${table}`;
  const [size, setSize] = useState(() => {
    try {
      const saved = localStorage.getItem(key);
      return saved !== null && PAGE_SIZES.includes(Number(saved)) ? Number(saved) : DEFAULT_PAGE_SIZE;
    } catch {
      return DEFAULT_PAGE_SIZE;
    }
  });
  const change = useCallback(
    (next) => {
      setSize(next);
      try {
        localStorage.setItem(key, String(next));
      } catch {
        // storage blocked: the choice lasts until the page is reloaded
      }
    },
    [key]
  );
  return [size, change];
}

function paginate(rows, page, size) {
  const per = size > 0 ? size : Math.max(rows.length, 1);
  const pages = Math.max(1, Math.ceil(rows.length / per));
  const current = Math.min(Math.max(page, 1), pages);
  const start = (current - 1) * per;
  return {
    pages,
    current,
    pageRows: rows.slice(start, start + per),
    from: rows.length ? start + 1 : 0,
    to: Math.min(start + per, rows.length),
  };
}

function Pager({ label, total, from, to, current, pages, pageSize, onPageSize, onPage, note = "" }) {
  const sizeId = useId();
  return (
    <nav className="pager" aria-label={label}>
      <span className="muted">
        Showing {formatCount(from)}–{formatCount(to)} of {formatCount(total)}
        {note}
      </span>
      <div className="pager-controls">
        <div className="pager-size">
          <label htmlFor={sizeId}>Rows per page</label>
          <select id={sizeId} className="select select-sm" value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>{n > 0 ? n : "All"}</option>
            ))}
          </select>
        </div>
        <div className="pager-buttons">
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => onPage(current - 1)} disabled={current <= 1}>
            Previous
          </button>
          <span className="pager-page" aria-current="page">
            Page {current} of {pages}
          </span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => onPage(current + 1)} disabled={current >= pages}>
            Next
          </button>
        </div>
      </div>
    </nav>
  );
}

const SORTS = {
  responses: { label: "Most responses", fn: (a, b) => b.responses_collected - a.responses_collected || a.name.localeCompare(b.name) },
  name: { label: "Name (A–Z)", fn: (a, b) => a.name.localeCompare(b.name) },
  newest: {
    label: "Newest first",
    fn: (a, b) => (parseDate(b.created_at)?.getTime() || 0) - (parseDate(a.created_at)?.getTime() || 0),
  },
};

const formatCount = (n) => Number(n || 0).toLocaleString();

// Admin badges: the round collecting surveys now, and a recipe changed by the organization.
const ORG_BADGE_DAYS = 14;
function ProductBadges({ product }) {
  const [now] = useState(() => Date.now());
  const r = product?.live_round;
  const c = product?.recipe_change;
  const orgRecipe = c && c.by_role === "client" && c.at && now - new Date(c.at).getTime() <= ORG_BADGE_DAYS * 86400000;
  if (!r && !orgRecipe) return null;
  return (
    <span className="product-badges">
      {r && <span className="round-chip is-live">Round {r.number} · {r.done} of {r.target}</span>}
      {orgRecipe && <span className="status-chip is-act">Recipe v{c.version} by {c.by_name || "the organization"} · {timeAgo(c.at)}</span>}
    </span>
  );
}

function HomeDashboard({
  surveyDraft,
  surveyInProgress,
  onNewSurvey,
  onContinueSurvey,
  onOpenFeedback,
  onViewHistory,
  canSurvey = true, // admins manage; survey persons take surveys
}) {
  // Numbers come from surveys (analyzed ones), so records deleted from the database never count.
  const [surveyStats, setSurveyStats] = useState(() => pageCache.homeStats || null);

  const [catalog, setCatalog] = useState(() => pageCache.homeCatalog || { loading: true, error: "", categories: [], products: [] });
  const [q, setQ] = useState("");
  const [categoryId, setCategoryId] = useState("all");
  const [state, setState] = useState("all");
  const [sort, setSort] = useState("responses");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePageSize("home");
  const tableRef = useRef(null);

  const [productRatings, setProductRatings] = useState(() => pageCache.homeRatings || {});

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setCatalog((c) => ({ ...c, loading: true, error: "" }));
    try {
      const [c, p, sv, pf] = await Promise.all([
        getCategories(),
        getProducts(),
        getRecentSurveys(30).catch(() => null),
        getProductFeedbackList().catch(() => null), // each product's consolidated-report rating
      ]);
      if (pf) {
        const ratings = {};
        listOf(pf, "products").forEach((x) => {
          const r = x.report?.overall_rating ?? x.avg_rating;
          if (r != null && Number(r) > 0) ratings[x.id] = Number(r);
        });
        pageCache.homeRatings = ratings;
        setProductRatings(ratings);
      }
      const next = { loading: false, error: "", categories: listOf(c, "categories"), products: listOf(p, "products") };
      pageCache.homeCatalog = next;
      setCatalog(next);
      if (sv) {
        pageCache.homeStats = sv;
        setSurveyStats(sv);
      }
    } catch (err) {
      if (silent) return; // background refresh: keep what is on screen
      console.error(err);
      setCatalog((c) => ({ ...c, loading: false, error: "The product list couldn't be loaded. Check that the backend is running." }));
    }
  }, []);

  useEffect(() => {
    load({ silent: Boolean(pageCache.homeCatalog) });
  }, [load]);
  useAutoRefresh(load);

  // Any filter change goes back to page 1.
  useEffect(() => setPage(1), [q, categoryId, state, sort, pageSize]);

  const { categories } = catalog;
  const categoryName = useCallback(
    (id) => categories.find((c) => String(c.id) === String(id))?.name || "",
    [categories]
  );

  const products = useMemo(
    () =>
      catalog.products.map((p) => ({
        ...p,
        _state: productState(p),
        _category: p.category_name || categoryName(p.category_id),
      })),
    [catalog.products, categoryName]
  );

  // Counts per state for the tabs (within the chosen category and search).
  const scoped = useMemo(() => {
    const query = q.trim().toLowerCase();
    return products.filter(
      (p) =>
        (categoryId === "all" || String(p.category_id) === categoryId) &&
        (!query || `${p.name} ${p._category} ${p.sku || ""}`.toLowerCase().includes(query))
    );
  }, [products, q, categoryId]);

  const rows = useMemo(
    () => scoped.filter((p) => state === "all" || p._state === state).sort(SORTS[sort].fn),
    [scoped, state, sort]
  );

  const { pages, current, pageRows, from, to } = paginate(rows, page, pageSize);

  const productCountByCategory = useMemo(() => {
    const m = {};
    products.forEach((p) => (m[p.category_id] = (m[p.category_id] || 0) + 1));
    return m;
  }, [products]);

  const withFeedback = useMemo(
    () => products.filter((p) => p._state === "feedback").sort((x, y) => y.responses_collected - x.responses_collected),
    [products]
  );
  const analyzedCount = surveyStats?.analyzed_total ?? 0;

  // Recent feedback: as many WHOLE entries as fit beside the table (no scrollbar,
  // never half an entry). Recomputed whenever the table or window changes size.
  const recentListRef = useRef(null);
  const [recentFit, setRecentFit] = useState(12);
  useEffect(() => {
    const list = recentListRef.current;
    if (!list || typeof ResizeObserver === "undefined") return undefined;
    const measure = () => {
      const items = [...list.children];
      items.forEach((li) => (li.style.display = "")); // measure every entry
      const bottom = list.getBoundingClientRect().bottom;
      let fit = items.findIndex((li) => li.getBoundingClientRect().bottom > bottom + 1);
      if (fit === -1) fit = items.length;
      items.forEach((li, i) => (li.style.display = i < Math.max(1, fit) ? "" : "none"));
      setRecentFit(Math.max(1, fit));
    };
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    measure();
    return () => ro.disconnect();
  });
  const recent = useMemo(
    () => listOf(surveyStats, "surveys").filter((x) => x.feedback).slice(0, 12),
    [surveyStats]
  );
  const inProgress = canSurvey && (Boolean(surveyDraft && surveyDraft.responseId == null) || surveyInProgress);
  const filtersActive = q || categoryId !== "all" || state !== "all";

  const goToPage = (n) => {
    setPage(n);
    tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <section className="home" aria-labelledby="home-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="home-title">Overview</h1>
          <p className="home-sub">
            {canSurvey ? "Track every product's field feedback and start new surveys." : "Every product's field feedback and its consolidated report."}
          </p>
        </div>
        {canSurvey && (
          <button type="button" className="btn btn-primary" onClick={() => onNewSurvey(null)}>
            Take a survey
          </button>
        )}
      </header>

      {inProgress && (
        <div className="home-resume" role="status">
          <div>
            <strong>Survey in progress</strong>
            <span>
              {surveyDraft
                ? `${surveyDraft.productName} · ${surveyDraft.respondentName}: the recording is still to be done.`
                : "A survey has been started but not finished."}
            </span>
          </div>
          <button type="button" className="btn btn-primary" onClick={onContinueSurvey}>
            Continue survey
          </button>
        </div>
      )}

      <div className="home-stats home-stats-3">
        <div className="h-stat">
          <span className="h-stat-label">Products</span>
          <span className="h-stat-value">{catalog.loading ? "…" : formatCount(products.length)}</span>
          <span className="h-stat-note">
            in {formatCount(categories.length)} {categories.length === 1 ? "category" : "categories"}
          </span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Feedback analyzed</span>
          <span className="h-stat-value">{surveyStats ? formatCount(analyzedCount) : "…"}</span>
          <span className="h-stat-note">surveys</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Products surveyed</span>
          <span className="h-stat-value">
            {catalog.loading ? "…" : (
              <>
                {formatCount(withFeedback.length)}
                <small className="h-stat-of"> of {formatCount(products.length)}</small>
              </>
            )}
          </span>
          <span className={`h-stat-note${products.length - withFeedback.length > 0 ? " is-warn" : ""}`}>
            {catalog.loading
              ? ""
              : products.length - withFeedback.length > 0
              ? `${formatCount(products.length - withFeedback.length)} ${products.length - withFeedback.length === 1 ? "product has" : "products have"} no survey yet`
              : "every product has surveys"}
          </span>
        </div>
      </div>

      <div className="home-grid">
        {/* ---------------- Products table ---------------- */}
        <section className="panel home-products" aria-labelledby="home-products-title" ref={tableRef}>
          <div className="panel-head">
            <h2 className="panel-title" id="home-products-title">Products</h2>
            <button type="button" className="link-btn" onClick={load} disabled={catalog.loading}>
              {catalog.loading ? "Loading…" : "Refresh"}
            </button>
          </div>

          <div className="table-toolbar">
            <input
              type="search"
              className="search"
              placeholder="Search products, categories or SKUs"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search products"
            />
            <select className="input toolbar-select" value={categoryId} onChange={(e) => setCategoryId(e.target.value)} aria-label="Category">
              <option value="all">All categories</option>
              {[...categories]
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.name} ({formatCount(productCountByCategory[c.id] || 0)})
                  </option>
                ))}
            </select>
            <select className="input toolbar-select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort by">
              {Object.entries(SORTS).map(([key, s]) => (
                <option key={key} value={key}>{s.label}</option>
              ))}
            </select>
          </div>

          {catalog.error ? (
            <div className="catalog-empty" role="alert">
              <strong>Couldn't load products</strong>
              <p>{catalog.error}</p>
              <button type="button" className="btn btn-secondary" onClick={load}>Try again</button>
            </div>
          ) : catalog.loading && products.length === 0 ? (
            <p className="muted table-empty">Loading products…</p>
          ) : products.length === 0 ? (
            <div className="catalog-empty">
              <strong>No products yet</strong>
              <p>Add categories and products, or load the sample catalog with: python -m app.database.seed_catalog</p>
            </div>
          ) : rows.length === 0 ? (
            <div className="table-empty">
              <p className="muted">No products match these filters.</p>
              {filtersActive && (
                <button type="button" className="btn btn-secondary" onClick={() => { setQ(""); setCategoryId("all"); setState("all"); }}>
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="table-wrap">
                <table className="data-table">
                  <caption className="visually-hidden">Products and their survey progress</caption>
                  <thead>
                    <tr>
                      <th scope="col">Product</th>
                      <th scope="col" className="col-category">Category</th>
                      <th scope="col">Responses</th>
                      <th scope="col">Rating</th>
                      <th scope="col">Status</th>
                      {canSurvey && <th scope="col"><span className="visually-hidden">Actions</span></th>}
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((p) => (
                      <tr key={p.id}>
                        <td>
                          <div className="cell-product">
                            <strong title={p.name}>{p.name}</strong>
                            <span>{p.sku || `${p.question_count} questions`}</span>
                            <span className="cell-category-inline">{p._category}</span>
                            <ProductBadges product={p} />
                          </div>
                        </td>
                        <td className="col-category">{p._category}</td>
                        <td>
                          <span className="cell-count">
                            {formatCount(p.responses_collected)} {Number(p.responses_collected) === 1 ? "response" : "responses"}
                          </span>
                        </td>
                        <td className="cell-rating">
                          {productRatings[p.id] != null ? (
                            <RatingStars rating={productRatings[p.id]} compact />
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td>
                          <span className={`status-chip is-${STATE_TONE[p._state]}`}>{STATE_LABEL[p._state]}</span>
                        </td>
                        {canSurvey && (
                        <td className="cell-action">
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => onNewSurvey(p)}
                            disabled={!p.survey_ready}
                            aria-label={`Start a survey for ${p.name}`}
                            title={p.survey_ready ? undefined : "Add 5–10 questions to this product first"}
                          >
                            Survey
                          </button>
                        </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <Pager
                label="Product pages"
                total={rows.length}
                from={from}
                to={to}
                current={current}
                pages={pages}
                pageSize={pageSize}
                onPageSize={setPageSize}
                onPage={goToPage}
              />
            </>
          )}
        </section>

        {/* ---------------- Side column ---------------- */}
        <aside className="home-side">
          <section className="panel side-panel" aria-labelledby="recent-title">
            <div className="panel-head">
              <h2 className="panel-title" id="recent-title">Recent feedback</h2>
              {analyzedCount > 0 && (
                <button type="button" className="link-btn" onClick={onViewHistory}>
                  View all {formatCount(analyzedCount)}
                </button>
              )}
            </div>
            {recent.length === 0 ? (
              <p className="muted">No feedback analyzed yet. Completed surveys appear here.</p>
            ) : (
              <ul className="side-list side-list-fit" ref={recentListRef} data-showing={recentFit}>
                {recent.map((it) => {
                  const rating = toRating(it.feedback.overall_rating);
                  return (
                    <li key={it.response_id}>
                      <button type="button" className="side-list-link" onClick={() => onOpenFeedback(it.feedback.id)}>
                        <span className="side-list-main">
                          <strong title={`${it.respondent_label} · ${it.product_name}`}>
                            {it.respondent_label} · {it.product_name}
                          </strong>
                          <span>{timeAgo(it.feedback.uploaded_at || it.created_at)}</span>
                        </span>
                        <span className="side-list-meta">
                          {Number.isFinite(rating) ? (
                            `${rating.toFixed(1)} ★`
                          ) : (
                            <SentimentPill sentiment={TONE_SENTIMENT[it.feedback.tone] || SENTIMENTS.neutral} small />
                          )}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </section>
  );
}

/* ================================================================
   Feedback history, product-wise
   ================================================================ */

const TONE_SENTIMENT = {
  positive: SENTIMENTS.positive,
  mixed: SENTIMENTS.mixed,
  negative: SENTIMENTS.negative,
  neutral: SENTIMENTS.neutral,
};

// Small stacked bar: positive / mixed / negative / neutral customers.
function ToneBar({ counts, label }) {
  const total = ["positive", "mixed", "negative", "neutral"].reduce((n, k) => n + (counts?.[k] || 0), 0);
  if (!total) return <span className="muted">—</span>;
  const pct = (k) => `${((counts[k] || 0) / total) * 100}%`;
  return (
    <span
      className="tone-bar"
      role="img"
      aria-label={label || `${counts.positive || 0} positive, ${counts.mixed || 0} mixed, ${counts.negative || 0} negative`}
      title={`${counts.positive || 0} positive · ${counts.mixed || 0} mixed · ${counts.negative || 0} negative`}
    >
      <span className="mix-positive" style={{ width: pct("positive") }} />
      <span className="mix-mixed" style={{ width: pct("mixed") }} />
      <span className="mix-negative" style={{ width: pct("negative") }} />
      <span className="mix-neutral" style={{ width: pct("neutral") }} />
    </span>
  );
}

// One report per product, kept current by the analysis worker.
function reportState(p) {
  if (p.report && !p.report.outdated) return { key: "ready", tone: "ok", label: "Report up to date" };
  if (p.responses > 0) return { key: "updating", tone: "act", label: p.report ? "Updating report…" : "Creating report…" };
  return { key: "none", tone: "neutral", label: "No feedback yet" };
}

const HISTORY_SORTS = {
  recent: { label: "Latest feedback", fn: (a, b) => (parseDate(b.last_feedback_at)?.getTime() || 0) - (parseDate(a.last_feedback_at)?.getTime() || 0) },
  responses: { label: "Most responses", fn: (a, b) => b.responses - a.responses },
  ratingLow: { label: "Lowest rating", fn: (a, b) => (a.avg_rating ?? 99) - (b.avg_rating ?? 99) },
  ratingHigh: { label: "Highest rating", fn: (a, b) => (b.avg_rating ?? -1) - (a.avg_rating ?? -1) },
  name: { label: "Name (A–Z)", fn: (a, b) => a.name.localeCompare(b.name) },
};

function ProductHistory({ onOpenProduct, onOpenUnlinked, onOpenFeedback, tab = "products", onTabChange }) {
  const [data, setData] = useState(() => pageCache.productHistory || { loading: true, error: "", products: [], unlinked: 0 });
  const [q, setQ] = useState("");
  const [categoryId, setCategoryId] = useState("all");
  const [filter, setFilter] = useState("feedback");
  const [sort, setSort] = useState("recent");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePageSize("history");

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setData((d) => ({ ...d, loading: true, error: "" }));
    try {
      const res = await getProductFeedbackList();
      const next = { loading: false, error: "", products: listOf(res, "products"), unlinked: res?.unlinked_count || 0 };
      pageCache.productHistory = next;
      setData(next);
    } catch (err) {
      if (silent) return; // background refresh: keep what is on screen
      console.error(err);
      setData((d) => ({ ...d, loading: false, error: `Survey history couldn't be loaded. Check that the backend is running at ${API_BASE_URL}.` }));
    }
  }, []);

  useEffect(() => {
    load({ silent: Boolean(pageCache.productHistory) });
  }, [load]);
  useAutoRefresh(load);
  useEffect(() => setPage(1), [q, categoryId, filter, sort, pageSize]);

  const products = useMemo(() => data.products.map((p) => ({ ...p, _report: reportState(p) })), [data.products]);
  const categories = useMemo(() => {
    const m = new Map();
    products.forEach((p) => m.set(String(p.category_id), p.category_name));
    return [...m.entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1])));
  }, [products]);

  const scoped = useMemo(() => {
    const query = q.trim().toLowerCase();
    return products.filter(
      (p) =>
        (categoryId === "all" || String(p.category_id) === categoryId) &&
        (!query || `${p.name} ${p.category_name} ${p.sku || ""}`.toLowerCase().includes(query))
    );
  }, [products, q, categoryId]);

  const FILTERS = {
    feedback: { label: "With feedback", test: (p) => p.responses > 0 },
    ready: { label: "Report up to date", test: (p) => p._report.key === "ready" },
    updating: { label: "Updating", test: (p) => p._report.key === "updating" },
    all: { label: "All products", test: () => true },
  };

  const rows = useMemo(() => scoped.filter(FILTERS[filter].test).sort(HISTORY_SORTS[sort].fn), [scoped, filter, sort]); // eslint-disable-line react-hooks/exhaustive-deps
  const { pages, current, pageRows, from, to } = paginate(rows, page, pageSize);

  return (
    <section className="feedback-history" aria-labelledby="history-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="history-title">Survey history</h1>
          <p className="home-sub">
            {tab === "products"
              ? "Grouped by product. Open one to see each customer's feedback and the consolidated report."
              : "Every analyzed survey from every product, newest first."}
          </p>
        </div>
        {tab === "products" && (
          <button type="button" className="btn btn-secondary" onClick={load} disabled={data.loading}>
            {data.loading ? "Refreshing…" : "Refresh"}
          </button>
        )}
      </header>

      <div className="segmented page-tabs history-tabs" role="tablist" aria-label="Survey history view">
        <button type="button" role="tab" aria-selected={tab === "products"} className={tab === "products" ? "is-on" : ""} onClick={() => onTabChange?.("products")}>
          By product
        </button>
        <button type="button" role="tab" aria-selected={tab === "surveys"} className={tab === "surveys" ? "is-on" : ""} onClick={() => onTabChange?.("surveys")}>
          All surveys
        </button>
      </div>

      {tab === "surveys" ? (
        <AllSurveysList onOpenFeedback={onOpenFeedback} />
      ) : (
      <>
      <section className="panel home-products">
        <div className="table-toolbar">
          <input type="search" className="search" placeholder="Search products, categories or SKUs" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search products" />
          <select className="input toolbar-select" value={categoryId} onChange={(e) => setCategoryId(e.target.value)} aria-label="Filter by category">
            <option value="all">All categories</option>
            {categories.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          <select className="input toolbar-select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort products">
            {Object.entries(HISTORY_SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>

        <div className="segmented state-tabs" role="tablist" aria-label="Show">
          {Object.entries(FILTERS).map(([key, f]) => (
            <button key={key} type="button" role="tab" aria-selected={filter === key} className={filter === key ? "is-on" : ""} onClick={() => setFilter(key)}>
              {f.label} <span className="seg-count">{formatCount(scoped.filter(f.test).length)}</span>
            </button>
          ))}
        </div>

        {data.error ? (
          <div className="catalog-empty" role="alert">
            <strong>Couldn't load survey history</strong>
            <p>{data.error}</p>
            <button type="button" className="btn btn-secondary" onClick={load}>Try again</button>
          </div>
        ) : data.loading && !products.length ? (
          <p className="muted table-empty">Loading…</p>
        ) : rows.length === 0 ? (
          <div className="table-empty">
            <p className="muted">
              {filter === "feedback" && !q && categoryId === "all"
                ? "No product has feedback yet. Completed surveys appear here, grouped by product."
                : "No products match these filters."}
            </p>
          </div>
        ) : (
          <>
            <div className="table-wrap">
              <table className="data-table history-table">
                <caption className="visually-hidden">Products and their customer feedback</caption>
                <thead>
                  <tr>
                    <th scope="col">Product</th>
                    <th scope="col">Responses</th>
                    <th scope="col">Avg rating</th>
                    <th scope="col">Sentiment</th>
                    <th scope="col" className="col-category">Latest feedback</th>
                    <th scope="col">Report</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((p) => (
                    <tr key={p.id} className="is-clickable" onClick={() => onOpenProduct(p.id)}>
                      <td>
                        <div className="cell-product">
                          <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); onOpenProduct(p.id); }}>
                            {p.name}
                          </button>
                          <span>{p.category_name}{p.rounds > 1 ? ` · ${p.rounds} rounds` : ""}</span>
                          <ProductBadges product={p} />
                        </div>
                      </td>
                      <td>
                        <span className="cell-count">
                          {formatCount(p.responses)} {p.responses === 1 ? "response" : "responses"}
                        </span>
                        {p.analyzing > 0 && <span className="analyzing-note">+{p.analyzing} analyzing</span>}
                      </td>
                      <td>{p.avg_rating != null ? <RatingStars rating={p.avg_rating} compact /> : <span className="muted">—</span>}</td>
                      <td><ToneBar counts={p.sentiment} /></td>
                      <td className="col-category">{p.last_feedback_at ? timeAgo(p.last_feedback_at) : "—"}</td>
                      <td><span className={`status-chip is-${p._report.tone}`}>{p._report.label}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager
              label="Product pages"
              total={rows.length}
              from={from}
              to={to}
              current={current}
              pages={pages}
              pageSize={pageSize}
              onPageSize={setPageSize}
              onPage={setPage}
            />
          </>
        )}
      </section>

      {data.unlinked > 0 && (
        <div className="unlinked-note">
          <span>
            <strong>{formatCount(data.unlinked)} other {data.unlinked === 1 ? "recording" : "recordings"}</strong> analyzed without a
            survey, so not linked to any product.
          </span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onOpenUnlinked}>View</button>
        </div>
      )}
      </>
      )}
    </section>
  );
}

/* ================================================================
   Team: executives and what they may see in the app
   ================================================================ */

function Switch({ checked, onChange, label, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`switch${checked ? " is-on" : ""}`}
      onClick={() => onChange(!checked)}
      disabled={disabled}
    >
      <span className="switch-knob" />
    </button>
  );
}

const TEAM_FILTERS = {
  all: { label: "All", test: () => true },
  history: { label: "History on", test: (u) => u.can_view_history },
  today: { label: "Active today", test: (u) => (u.stats?.today || 0) > 0 },
  disabled: { label: "Disabled", test: (u) => !u.is_active },
};

function TeamPage() {
  const [tab, setTab] = useState("members"); // "members" | "activity"
  const [data, setData] = useState({ loading: true, error: "", users: [] });
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [saving, setSaving] = useState({});
  const [notice, setNotice] = useState("");
  const [dialog, setDialog] = useState(null); // { type: "add" | "credentials" | "reset" | "products", member?, password?, linked? }
  const [productNames, setProductNames] = useState({}); // id -> name, for the Products column
  useEffect(() => {
    getProducts()
      .then((res) => setProductNames(Object.fromEntries(listOf(res, "products").map((p) => [p.id, p.name]))))
      .catch(() => {});
  }, []);

  // What each executive can actually survey: a product given to nobody is open to all;
  // a product given to someone is shown only to the people it's given to.
  const allProductIds = Object.keys(productNames).map(Number);
  const owners = {};
  data.users.forEach((u) => (u.role === "executive" ? u.assigned_products || [] : []).forEach((pid) => (owners[pid] ||= new Set()).add(u.id)));
  const visibleTo = (u) => allProductIds.filter((pid) => !owners[pid] || owners[pid].has(u.id));

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setData((d) => ({ ...d, loading: true, error: "" }));
    try {
      const res = await getTeam();
      setData({ loading: false, error: "", users: listOf(res, "users") });
    } catch (err) {
      if (silent) return; // background refresh: keep what is on screen
      console.error(err);
      setData((d) => ({
        ...d,
        loading: false,
        error:
          err?.response?.status === 404
            ? "The backend doesn't have team management yet. Add the admin router to app/main.py."
            : `The team couldn't be loaded. Check that the backend is running at ${API_BASE_URL}.`,
      }));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useAutoRefresh(load);

  const change = async (user, changes, message) => {
    setSaving((x) => ({ ...x, [user.id]: true }));
    // Show the change straight away; undo it if the server refuses.
    setData((d) => ({ ...d, users: d.users.map((u) => (u.id === user.id ? { ...u, ...changes } : u)) }));
    try {
      const res = await updateTeamMember(user.id, changes);
      setData((d) => ({ ...d, users: d.users.map((u) => (u.id === user.id ? { ...u, ...res.user } : u)) }));
      setNotice(message);
    } catch {
      setData((d) => ({ ...d, users: d.users.map((u) => (u.id === user.id ? user : u)) }));
      setNotice(`Couldn't update ${user.name}. Try again.`);
    } finally {
      setSaving((x) => ({ ...x, [user.id]: false }));
    }
  };

  // Lost or shared phone: end every sign-in of this person (their password stays the same).
  const signOutEverywhere = async (user) => {
    const ok = await confirmDialog({
      title: `Sign ${user.name} out of every device?`,
      message: `${user.name} is signed out of the mobile app and the web straight away and must sign in again with their password. Use this when a phone is lost or was shared. Surveys saved on the phone are not deleted.`,
      confirmLabel: "Sign out everywhere",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    setSaving((x) => ({ ...x, [user.id]: true }));
    try {
      await signOutMember(user.id);
      setNotice(`${user.name} was signed out of every device.`);
    } catch {
      setNotice(`Couldn't sign ${user.name} out. Try again.`);
    } finally {
      setSaving((x) => ({ ...x, [user.id]: false }));
    }
  };

  const query = q.trim().toLowerCase();
  // Team members only: admins manage the team and the organization has its own portal.
  const scoped = data.users.filter(
    (u) => u.role === "executive" && (!query || `${u.name} ${u.mobile || ""} ${u.email || ""}`.toLowerCase().includes(query))
  );
  const rows = scoped.filter(TEAM_FILTERS[filter].test);

  return (
    <section className="team" aria-labelledby="team-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="team-title">Team</h1>
          <p className="home-sub">Survey executives using the mobile app. Add executives and choose who may see their survey history.</p>
        </div>
        {tab === "members" && (
          <div className="catalog-head-actions">
            <button type="button" className="btn btn-secondary" onClick={() => load()} disabled={data.loading}>
              {data.loading ? "Refreshing…" : "Refresh"}
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setDialog({ type: "add" })}>
              Add team member
            </button>
          </div>
        )}
      </header>

      <div className="segmented team-tabs" role="tablist" aria-label="Team page">
        <button type="button" role="tab" aria-selected={tab === "members"} className={tab === "members" ? "is-on" : ""}
          onClick={() => setTab("members")}>
          Members
        </button>
        <button type="button" role="tab" aria-selected={tab === "activity"} className={tab === "activity" ? "is-on" : ""}
          onClick={() => setTab("activity")}>
          Activity log
        </button>
      </div>

      {tab === "activity" && <ActivityLog />}

      {notice && (
        <p className="team-notice" role="status" onAnimationEnd={() => setNotice("")} key={notice}>
          {notice}
        </p>
      )}

      <section className="panel home-products" hidden={tab !== "members"}>
        <div className="table-toolbar">
          <input type="search" className="search" placeholder="Search by name, mobile or email" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search executives" />
        </div>
        <div className="segmented state-tabs" role="tablist" aria-label="Show">
          {Object.entries(TEAM_FILTERS).map(([key, f]) => (
            <button key={key} type="button" role="tab" aria-selected={filter === key} className={filter === key ? "is-on" : ""} onClick={() => setFilter(key)}>
              {f.label} <span className="seg-count">{formatCount(scoped.filter(f.test).length)}</span>
            </button>
          ))}
        </div>

        {data.error ? (
          <div className="catalog-empty" role="alert">
            <strong>Couldn't load the team</strong>
            <p>{data.error}</p>
            <button type="button" className="btn btn-secondary" onClick={load}>Try again</button>
          </div>
        ) : data.loading && !data.users.length ? (
          <p className="muted table-empty">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted table-empty">
            {data.users.length ? "No executives match." : "No accounts yet. Executives appear here after they create an account in the mobile app."}
          </p>
        ) : (
          <div className="table-wrap">
            <table className="data-table team-table">
              <caption className="visually-hidden">Survey executives</caption>
              <thead>
                <tr>
                  <th scope="col">Executive</th>
                  <th scope="col">Today</th>
                  <th scope="col">This week</th>
                  <th scope="col">Total</th>
                  <th scope="col" className="col-category">Last survey</th>
                  <th scope="col" className="col-category">Last login</th>
                  <th scope="col">Can survey</th>
                  <th scope="col">Survey history</th>
                  <th scope="col">Active</th>
                  <th scope="col"><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => {
                  const isAdmin = u.role === "admin";
                  return (
                    <tr key={u.id} className={`${u.is_active ? "" : "is-pending"}${isAdmin ? " is-admin-row" : ""}`}>
                      <td>
                        <div className="cell-person">
                          <span className="avatar" aria-hidden="true">{(u.name || "?").trim()[0]?.toUpperCase()}</span>
                          <div className="cell-product">
                            <strong>
                              {u.name}
                              {isAdmin && <span className="status-chip is-act team-role">Admin</span>}
                            </strong>
                            <span>{u.mobile || u.email || "—"}</span>
                          </div>
                        </div>
                      </td>

                      {isAdmin ? (
                        // Admins don't take surveys, so the survey columns don't apply to them.
                        <td colSpan={7} className="team-admin-note">
                          <span className="muted">Administrator account. Manages the team and has full access; no surveys are taken from it.</span>
                        </td>
                      ) : (
                        <>
                          <td className="num-cell"><strong>{formatCount(u.stats?.today || 0)}</strong></td>
                          <td className="num-cell">{formatCount(u.stats?.this_week || 0)}</td>
                          <td className="num-cell">
                            {formatCount(u.stats?.total || 0)}
                            <span className="muted team-analyzed">{formatCount(u.stats?.analyzed || 0)} analyzed</span>
                          </td>
                          <td className="col-category">{u.stats?.last_survey_at ? timeAgo(u.stats.last_survey_at) : "Never"}</td>
                          <td className="col-category">{u.last_login_at ? timeAgo(u.last_login_at) : "—"}</td>
                          <td className="cell-products">
                            {(() => {
                              const mine = visibleTo(u);
                              const total = allProductIds.length;
                              const names = mine.map((pid) => productNames[pid]).filter(Boolean);
                              return (
                                <button type="button" className="assign-btn" onClick={() => setDialog({ type: "products", member: u })}
                                  title={names.length ? `Can survey: ${names.join(", ")}` : "Can't survey any product yet"}>
                                  <strong className={mine.length ? "" : "assign-none"}>
                                    {total && mine.length === total ? `All ${total} products` : `${mine.length} of ${total} products`}
                                  </strong>
                                  <span className="assign-edit">Manage</span>
                                </button>
                              );
                            })()}
                          </td>
                          <td>
                            <Switch
                              checked={!!u.can_view_history}
                              disabled={saving[u.id]}
                              label={`Survey history for ${u.name}`}
                              onChange={(v) =>
                                change(u, { can_view_history: v }, v ? `${u.name} can now see their survey history in the app.` : `${u.name} now sees only their own numbers.`)
                              }
                            />
                          </td>
                        </>
                      )}

                      <td>
                        <Switch
                          checked={!!u.is_active}
                          disabled={saving[u.id]}
                          label={`${u.name} active`}
                          onChange={(v) =>
                            change(u, { is_active: v }, v ? `${u.name} can log in again.` : `${u.name} is disabled and signed out of the app.`)
                          }
                        />
                      </td>
                      <td className="cell-action">
                        <div className="team-actions">
                          <button type="button" className="link-btn" onClick={() => setDialog({ type: "reset", member: u })}>
                            Reset password
                          </button>
                          <button type="button" className="link-btn" disabled={saving[u.id]} onClick={() => signOutEverywhere(u)}>
                            Sign out everywhere
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="team-help">
          <p>
            <strong>Survey history</strong> on: the executive's mobile app shows the History tab, with the ratings and
            summaries of their own surveys. Off: the app shows only their numbers and whether each survey was analyzed.
            On the web, executives always see the full analysis of the surveys they took (My surveys).
          </p>
          <p>
            <strong>Active</strong> on: they can log in and take surveys. Off: their login is refused and they're signed out
            of the app; their account and surveys are kept, and switching it back on restores access. Use it instead of
            deleting someone who leaves or should be paused.
          </p>
          <p>
            <strong>Sign out everywhere</strong> ends every sign-in of that person at once (a lost or shared phone) without
            changing their password. Resetting a password, switching Active off or changing the role does this too.
            Survey persons stay signed in for 7 days and admins for 24 hours while the app isn't used; using the app
            keeps the sign-in fresh.
          </p>
          <p className="muted">Changes reach the app the next time it's opened or refreshed. Admins always have access.</p>
        </div>
      </section>

      {dialog?.type === "add" && (
        <AddMemberDialog
          onClose={() => setDialog(null)}
          onAdded={(member, password, linked) => {
            setDialog({ type: "credentials", member, password, linked });
            setNotice(`${member.name} was added to the team.`);
            load();
          }}
        />
      )}
      {dialog?.type === "credentials" && (
        <CredentialsDialog member={dialog.member} password={dialog.password} linked={dialog.linked}
          reset={dialog.reset} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "products" && (
        <AssignProductsDialog
          member={dialog.member}
          team={data.users}
          onClose={() => setDialog(null)}
          onSaved={(ids) => {
            setDialog(null);
            setData((d) => ({ ...d, users: d.users.map((x) => (x.id === dialog.member.id ? { ...x, assigned_products: ids } : x)) }));
            setNotice(`Saved. ${dialog.member.name}'s products are updated in the app and on the web.`);
          }}
        />
      )}
      {dialog?.type === "reset" && (
        <ResetPasswordDialog
          member={dialog.member}
          onClose={() => setDialog(null)}
          onDone={(password) => setDialog({ type: "credentials", member: dialog.member, password, linked: 0, reset: true })}
        />
      )}
    </section>
  );
}

/* ================================================================
   Survey person's Analyze page: their own surveys
   ================================================================ */

function surveyStatus(sv) {
  if (sv.feedback) return { tone: "ok", label: "Analyzed" };
  const st = sv.analysis?.status;
  if (st === "processing") return { tone: "act", label: "Analyzing…", spin: true };
  if (st === "queued") return { tone: "neutral", label: `In queue${sv.analysis.position ? ` · #${sv.analysis.position}` : ""}` };
  if (st === "failed") return { tone: "warn", label: "Analysis failed" };
  return { tone: "neutral", label: "No recording yet" };
}

function MySurveys({ user, onTakeSurvey }) {
  const [data, setData] = useState(() => pageCache.mySurveys || { loading: true, error: "", items: [], canView: false });
  const [openId, setOpenId] = useState(null); // the survey being viewed

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setData((d) => ({ ...d, loading: true, error: "" }));
    try {
      const res = await axios.get(`${API_BASE_URL}/auth/me/surveys`, { params: { limit: 100 } });
      const next = { loading: false, error: "", items: listOf(res.data, "surveys"), canView: Boolean(res.data?.can_view_history) };
      pageCache.mySurveys = next;
      setData(next);
    } catch {
      if (silent) return;
      setData((d) => ({ ...d, loading: false, error: "Your surveys couldn't be loaded. Check your connection and try again." }));
    }
  }, []);

  useEffect(() => {
    load({ silent: Boolean(pageCache.mySurveys) });
  }, [load]);
  useAutoRefresh(load);
  const running = data.items.some((x) => !x.feedback && ["queued", "processing"].includes(x.analysis?.status));
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => document.visibilityState === "visible" && load({ silent: true }), 4000);
    return () => clearInterval(t);
  }, [running, load]);

  const today = new Date().toDateString();
  const todayCount = data.items.filter((x) => parseDate(x.created_at)?.toDateString() === today).length;
  const analyzed = data.items.filter((x) => x.feedback).length;
  const inProgress = data.items.filter((x) => !x.feedback && ["queued", "processing"].includes(x.analysis?.status)).length;

  if (openId) {
    return (
      <MySurveyDetail
        responseId={openId}
        onBack={() => {
          setOpenId(null);
          window.scrollTo({ top: 0 });
        }}
      />
    );
  }

  const open = (sv) => {
    setOpenId(sv.response_id);
    window.scrollTo({ top: 0 });
  };

  return (
    <section className="my-surveys" aria-labelledby="my-title">
      <header className="home-head">
        <div className="page-heading">
          <h1 id="my-title">My surveys</h1>
          <p className="home-sub">
            {data.canView
              ? "Every survey you've taken, with its analysis."
              : "Every survey you've taken and whether it has been analyzed."}
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={onTakeSurvey}>Take a survey</button>
      </header>

      <div className="home-stats">
        <div className="h-stat">
          <span className="h-stat-label">Total</span>
          <span className="h-stat-value">{data.loading && !data.items.length ? "…" : formatCount(data.items.length)}</span>
          <span className="h-stat-note">surveys by {user?.name?.split(" ")[0] || "you"}</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Today</span>
          <span className="h-stat-value">{formatCount(todayCount)}</span>
          <span className="h-stat-note">taken today</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Analyzed</span>
          <span className="h-stat-value">{formatCount(analyzed)}</span>
          <span className="h-stat-note">{inProgress ? `${formatCount(inProgress)} in progress` : "all done"}</span>
        </div>
      </div>

      <section className="panel home-products">
        {data.error ? (
          <div className="catalog-empty" role="alert">
            <strong>Couldn't load your surveys</strong>
            <p>{data.error}</p>
            <button type="button" className="btn btn-secondary" onClick={() => load()}>Try again</button>
          </div>
        ) : data.loading && !data.items.length ? (
          <p className="muted table-empty">Loading…</p>
        ) : !data.items.length ? (
          <div className="catalog-empty">
            <strong>No surveys yet</strong>
            <p>Take your first survey: choose the product, fill in the customer's details and answers, then record their feedback.</p>
            <button type="button" className="btn btn-primary" onClick={onTakeSurvey}>Take a survey</button>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <caption className="visually-hidden">Your surveys</caption>
              <thead>
                <tr>
                  <th scope="col">Customer</th>
                  <th scope="col">Product</th>
                  <th scope="col">Version</th>
                  <th scope="col">Date</th>
                  <th scope="col">Status</th>
                  {data.canView && <th scope="col">Rating</th>}
                  {data.canView && <th scope="col" className="col-summary">Summary</th>}
                  <th scope="col"><span className="visually-hidden">View</span></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((sv) => {
                  const st = surveyStatus(sv);
                  const fb = sv.feedback;
                  return (
                    <tr
                      key={sv.response_id}
                      className="is-clickable"
                      onClick={() => open(sv)}
                      title="View this survey"
                    >
                      <td>
                        <div className="cell-product">
                          <strong>{sv.respondent_label}</strong>
                          <span>{sv.area || "—"}</span>
                        </div>
                      </td>
                      <td>
                        <div className="cell-product">
                          <strong>{sv.product_name}</strong>
                          <span>{sv.category_name}</span>
                        </div>
                      </td>
                      <td>
                        {sv.round ? (
                          <span className="version-stack">
                            {sv.recipe_version && <span className="version-chip">v{sv.recipe_version}</span>}
                            <span className="round-chip">Round {sv.round}</span>
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        <div className="cell-product">
                          <strong>{timeAgo(sv.created_at)}</strong>
                          <span>{formatDateTime(sv.created_at)}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`status-chip is-${st.tone}${st.spin ? " analyzing-chip" : ""}`}>
                          {st.spin && <span className="spinner chip-spinner" aria-hidden="true" />}
                          {st.label}
                        </span>
                      </td>
                      {data.canView && (
                        <td>{fb?.overall_rating ? <RatingStars rating={fb.overall_rating} compact /> : <span className="muted">—</span>}</td>
                      )}
                      {data.canView && (
                        <td className="col-summary"><span className="summary-clip">{fb?.summary || "—"}</span></td>
                      )}
                      <td className="cell-action">
                        <button
                          type="button"
                          className="icon-btn view-btn"
                          aria-label={`View the survey of ${sv.respondent_label}`}
                          title="View survey"
                          onClick={(e) => {
                            e.stopPropagation();
                            open(sv);
                          }}
                        >
                          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                            <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                            <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}


/* ================================================================
   One of my surveys: recording + transcript, result, survey answers
   ================================================================ */

const USAGE_LABEL = { last_week: "Used in the last week", last_month: "Used in the last month", last_3_months: "Used in the last 3 months", more_than_3_months: "Used more than 3 months ago" };

function transcriptRows(t) {
  if (!t) return [];
  if (Array.isArray(t)) return t.map((x, i) => ({ key: i, time: x.start != null ? formatTime(x.start) : null, text: x.text || "" })).filter((x) => x.text);
  // "[00:00 - 00:05] text" lines keep their time; plain text becomes short paragraphs.
  const timed = String(t).split(/\n+/).map((line) => line.match(/^\s*\[([\d:.]+)\s*-\s*[\d:.]+\]\s*(.+)$/)).filter(Boolean);
  if (timed.length) return timed.map((m, i) => ({ key: i, time: m[1].replace(/^0(\d:)/, "$1").split(".")[0], text: m[2] }));
  return toParagraphs(t, 3).map((p, i) => ({ key: i, time: null, text: p }));
}

// Aspect sentiment -> one of four groups.
const aspectTone = (s) => {
  const v = String(s || "").toLowerCase();
  if (v === "positive") return "positive";
  if (v.includes("concern") || v === "mixed") return "concern";
  if (v === "negative") return "negative";
  return "neutral";
};
const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };

/* Everything the page shows, derived from the recording's own analysis (nothing fixed). */
function analyseFeedback(fb) {
  const parts = fb?.parts || [];
  const items = parts.flatMap((p) => p.feedback || []);
  const groups = { positive: [], concern: [], negative: [], neutral: [] };
  items.forEach((i) => groups[aspectTone(i.sentiment)].push(i));

  // Rating per aspect (several mentions of one aspect are averaged).
  const byAspect = {};
  items.forEach((i) => {
    const k = i.aspect || "General";
    const a = (byAspect[k] ||= { aspect: k, ratings: [], tones: [] });
    if (typeof i.rating === "number" && i.rating > 0) a.ratings.push(i.rating);
    a.tones.push(aspectTone(i.sentiment));
  });
  const aspects = Object.values(byAspect)
    .map((a) => ({
      aspect: a.aspect,
      rating: a.ratings.length ? a.ratings.reduce((x, y) => x + y, 0) / a.ratings.length : null,
      tone: a.tones.includes("negative") ? "negative" : a.tones.includes("concern") ? "concern" : a.tones.includes("positive") ? "positive" : "neutral",
    }))
    .sort((x, y) => (y.rating ?? -1) - (x.rating ?? -1));

  const praises = groups.positive;
  const issues = [...groups.negative, ...groups.concern].sort(
    (x, y) => (SEVERITY_ORDER[String(x.severity).toLowerCase()] ?? 3) - (SEVERITY_ORDER[String(y.severity).toLowerCase()] ?? 3)
  );

  // Key insights: each part's main point (or its summary).
  const insights = parts.map((p) => p.key_point || p.summary).filter(Boolean);

  // Recommendations: one per distinct issue, plus suggestions the customer stated.
  const seen = new Set();
  const recs = [];
  issues.forEach((i) => {
    const k = (i.aspect || "").toLowerCase();
    if (seen.has(k)) return;
    seen.add(k);
    recs.push({ title: `Improve ${i.aspect}`, detail: i.evidence ? `Customer: "${i.evidence}"` : null, severity: i.severity });
  });
  const suggestionWords = /\b(should|suggest|would like|wants?|prefer|reduce|increase|add|improve|make it|less|more)\b/i;
  splitSentences(fb?.summary || "")
    .filter((sentence) => suggestionWords.test(sentence))
    .slice(0, 3)
    .forEach((sentence) => recs.push({ title: sentence, detail: null }));

  return {
    praises,
    issues,
    aspects,
    insights,
    recs: recs.slice(0, 6),
    mix: {
      positive: groups.positive.length,
      concern: groups.concern.length,
      negative: groups.negative.length,
      neutral: groups.neutral.length,
    },
  };
}

function SentimentDonut({ mix }) {
  const parts = [
    ["positive", "Positive", "var(--pos, #1e8a5a)"],
    ["concern", "With concerns", "var(--act, #a86a12)"],
    ["negative", "Negative", "var(--neg, #c8414f)"],
    ["neutral", "Neutral", "var(--muted, #8a97a6)"],
  ].filter(([k]) => mix[k] > 0);
  const total = parts.reduce((n, [k]) => n + mix[k], 0);
  if (!total) return <p className="muted">No aspect-level sentiment was found in this recording.</p>;
  const R = 42;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 120 120" className="donut" role="img" aria-label={parts.map(([k, l]) => `${l} ${mix[k]}`).join(", ")}>
        <circle cx="60" cy="60" r={R} fill="none" stroke="var(--line)" strokeWidth="16" />
        {parts.map(([k, , color]) => {
          const len = (mix[k] / total) * C;
          const el = (
            <circle key={k} cx="60" cy="60" r={R} fill="none" stroke={color} strokeWidth="16"
              strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset} transform="rotate(-90 60 60)" />
          );
          offset += len;
          return el;
        })}
        <text x="60" y="57" textAnchor="middle" className="donut-num">{total}</text>
        <text x="60" y="73" textAnchor="middle" className="donut-lbl">mentions</text>
      </svg>
      <ul className="donut-legend">
        {parts.map(([k, l, color]) => (
          <li key={k}>
            <span className="dot" style={{ background: color }} aria-hidden="true" />
            {l}
            <strong>{mix[k]}</strong>
            <span className="muted">{Math.round((100 * mix[k]) / total)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AspectBars({ aspects }) {
  const rated = aspects.filter((a) => a.rating != null);
  if (!rated.length) return <p className="muted">The customer didn't rate specific aspects.</p>;
  return (
    <ul className="aspect-bars">
      {rated.map((a) => (
        <li key={a.aspect}>
          <span className="aspect-name">{a.aspect}</span>
          <span className="aspect-track" aria-hidden="true">
            <span className={`aspect-fill tone-${a.tone}`} style={{ width: `${Math.max(4, (a.rating / 5) * 100)}%` }} />
          </span>
          <span className="aspect-val">{a.rating.toFixed(1)}</span>
        </li>
      ))}
    </ul>
  );
}

function MySurveyDetail({ responseId, onBack }) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const [lang, setLang] = useState("en");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: "" }));
    axios
      .get(`${API_BASE_URL}/auth/me/surveys/${responseId}`)
      .then((res) => alive && setState({ loading: false, error: "", data: res.data }))
      .catch((err) => {
        if (!alive) return;
        const status = err?.response?.status;
        setState({
          loading: false,
          data: null,
          error:
            status === 404
              ? "This survey isn't available. It may have been deleted, or it belongs to someone else."
              : err?.code === "ERR_NETWORK"
              ? "Can't reach the server. Check your connection and try again."
              : "The survey couldn't be loaded. Try again.",
          retry: status !== 404,
        });
      });
    return () => {
      alive = false;
    };
  }, [responseId, attempt]);

  const back = (
    <button type="button" className="back-link" onClick={onBack}>
      <ChevronIcon dir="left" /> My surveys
    </button>
  );

  if (state.loading) {
    return (
      <section className="my-survey" aria-busy="true" aria-label="Loading survey">
        {back}
        <div className="sk sk-title" />
        <div className="kpi-grid">{[0, 1, 2, 3].map((i) => <div key={i} className="sk sk-kpi" />)}</div>
        <div className="my-survey-grid"><div className="sk sk-card" /><div className="sk sk-card" /></div>
      </section>
    );
  }
  if (state.error) {
    return (
      <section className="my-survey">
        {back}
        <div className="panel state-card" role="alert">
          <strong>{state.error}</strong>
          {state.retry && <button type="button" className="btn btn-primary" onClick={() => setAttempt((n) => n + 1)}>Try again</button>}
        </div>
      </section>
    );
  }

  const sv = state.data;
  const fb = sv.feedback;
  const st = surveyStatus({ feedback: fb, analysis: null });
  const full = Boolean(fb && sv.can_view_history && fb.parts !== undefined);
  const A = full ? analyseFeedback(fb) : null;
  const english = transcriptRows(fb?.transcript);
  const original = transcriptRows(fb?.original_transcript);
  const rows = lang === "original" && original.length ? original : english;
  const tone = fb?.tone ? TONE_SENTIMENT[fb.tone] || SENTIMENTS.neutral : null;
  const noSpeech = full && !english.length && !A.praises.length && !A.issues.length && !fb.summary;

  const recording = (
    <section className="panel my-card" aria-labelledby="rec-title">
      <h2 className="my-card-title" id="rec-title">Recording</h2>
      {fb ? (
        <>
          {fb.is_video ? (
            <video className="my-media" controls preload="metadata" src={withToken(getFeedbackVideoUrl(fb.id))} />
          ) : (
            <audio className="my-media my-audio" controls preload="metadata" src={withToken(getFeedbackVideoUrl(fb.id))} />
          )}
          <p className="muted my-media-meta">
            {fb.duration_seconds ? formatDuration(fb.duration_seconds) : ""}
            {fb.language ? ` · spoken in ${fb.language}` : ""}
          </p>
        </>
      ) : (
        <p className="muted">No recording was added to this survey.</p>
      )}
    </section>
  );

  const answersCard = (
    <section className="panel my-card" aria-labelledby="ans-title">
      <h2 className="my-card-title" id="ans-title">Survey answers</h2>
      {sv.answers.length ? (
        <ol className="my-answers">
          {sv.answers.map((a, i) => (
            <li key={i}>
              <span className="my-q">{a.question}</span>
              <span className="my-a">{a.type === "rating" && /^\d$/.test(String(a.value)) ? `${a.value} out of 5` : a.value || "—"}</span>
              {a.value_en && <span className="my-a-en">In English: {a.value_en}</span>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No answers were saved with this survey. If you remember filling them in, ask your admin to run the survey check.</p>
      )}
    </section>
  );

  return (
    <section className="my-survey" aria-labelledby="my-survey-title">
      {back}
      <header className="my-survey-head">
        <p className="eyebrow">{sv.category_name}</p>
        <h1 id="my-survey-title">{sv.respondent_label} · {sv.product_name}</h1>
        <div className="my-survey-meta">
          <span>{formatDateTime(sv.created_at)}</span>
          {sv.area && <span>{sv.area}</span>}
          {sv.survey_location && <span>Surveyed at {sv.survey_location}</span>}
          {sv.usage_period && <span>{USAGE_LABEL[sv.usage_period] || sv.usage_period}</span>}
          {sv.recipe_version && <span className="version-chip">Recipe v{sv.recipe_version}</span>}
          {sv.round && <span className="round-chip">Round {sv.round}</span>}
          <span className={`status-chip is-${st.tone}`}>{st.label}</span>
        </div>
      </header>

      {!fb && (
        <div className="panel state-card">
          <strong>No analysis for this survey</strong>
          <span className="muted">No recording was added, so there was nothing to analyze. The customer's answers are below.</span>
        </div>
      )}
      {fb && !sv.can_view_history && (
        <div className="panel state-card is-locked">
          <strong>This survey was analyzed</strong>
          <span className="muted">
            The rating, summary, praises and issues become visible when your admin turns on <b>Survey history</b> for
            you on the Team page.
          </span>
        </div>
      )}

      {full && (
        <>
          {/* ---- KPI cards ---- */}
          <div className="kpi-grid">
            <div className="kpi">
              <span className="kpi-label">Overall rating</span>
              <span className="kpi-value">{fb.overall_rating != null ? Number(fb.overall_rating).toFixed(1) : "—"}<small>/5</small></span>
              {fb.overall_rating != null && <RatingStars rating={fb.overall_rating} showValue={false} />}
            </div>
            <div className="kpi">
              <span className="kpi-label">Sentiment</span>
              <span className={`kpi-value kpi-text tone-text-${fb.tone || "neutral"}`}>{tone ? tone.label : "—"}</span>
              <span className="kpi-note">overall feeling about the product</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Praises</span>
              <span className="kpi-value is-pos">{A.praises.length}</span>
              <span className="kpi-note">things the customer liked</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Issues</span>
              <span className="kpi-value is-neg">{A.issues.length}</span>
              <span className="kpi-note">complaints or concerns</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Recording</span>
              <span className="kpi-value kpi-text">{fb.duration_seconds ? formatDuration(fb.duration_seconds) : "—"}</span>
              <span className="kpi-note">{fb.language ? `in ${fb.language}` : `${(fb.parts || []).length || 1} part${(fb.parts || []).length > 1 ? "s" : ""}`}</span>
            </div>
          </div>

          {noSpeech && (
            <div className="panel state-card">
              <strong>No customer feedback was found in this recording</strong>
              <span className="muted">The audio may be silent, very noisy, or in a language that couldn't be recognised. The answers below are still saved.</span>
            </div>
          )}

          {/* ---- summary + insights ---- */}
          {(fb.summary || A.insights.length > 0) && (
            <section className="panel my-card summary-card" aria-labelledby="sum-title">
              <h2 className="my-card-title" id="sum-title">AI summary</h2>
              {fb.summary && <p className="my-summary-lead">{fb.summary}</p>}
              {A.insights.length > 0 && (
                <>
                  <h3 className="my-subtitle">Key insights</h3>
                  <ul className="insight-list">{A.insights.map((t, i) => <li key={i}>{t}</li>)}</ul>
                </>
              )}
            </section>
          )}

          {/* ---- praises / issues ---- */}
          {(A.praises.length > 0 || A.issues.length > 0) && (
            <div className="my-survey-grid even">
              <section className="panel my-card" aria-labelledby="pr-title">
                <h2 className="my-card-title" id="pr-title">What the customer praised <span className="count-badge">{A.praises.length}</span></h2>
                {A.praises.length ? (
                  <ul className="fb-list is-pos">
                    {A.praises.map((p, i) => (
                      <li key={i}>
                        <strong>{p.aspect}</strong>
                        {p.evidence && <q>{p.evidence}</q>}
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted">Nothing specific was praised.</p>}
              </section>
              <section className="panel my-card" aria-labelledby="is-title">
                <h2 className="my-card-title" id="is-title">Issues and complaints <span className="count-badge">{A.issues.length}</span></h2>
                {A.issues.length ? (
                  <ul className="fb-list is-neg">
                    {A.issues.map((p, i) => (
                      <li key={i}>
                        <strong>
                          {p.aspect}
                          {p.severity && <span className={`sev sev-${String(p.severity).toLowerCase()}`}>{p.severity}</span>}
                          {aspectTone(p.sentiment) === "concern" && <span className="sev sev-concern">concern</span>}
                        </strong>
                        {p.evidence && <q>{p.evidence}</q>}
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted">No complaints were raised.</p>}
              </section>
            </div>
          )}

          {/* ---- charts ---- */}
          {(A.aspects.length > 0) && (
            <div className="my-survey-grid even">
              <section className="panel my-card" aria-labelledby="mix-title">
                <h2 className="my-card-title" id="mix-title">Sentiment mix</h2>
                <SentimentDonut mix={A.mix} />
              </section>
              <section className="panel my-card" aria-labelledby="asp-title">
                <h2 className="my-card-title" id="asp-title">Rating by aspect</h2>
                <AspectBars aspects={A.aspects} />
              </section>
            </div>
          )}

          {/* ---- recommendations ---- */}
          {A.recs.length > 0 && (
            <section className="panel my-card" aria-labelledby="rec2-title">
              <h2 className="my-card-title" id="rec2-title">Recommendations</h2>
              <ol className="rec-list">
                {A.recs.map((r, i) => (
                  <li key={i}>
                    <strong>{r.title}</strong>
                    {r.detail && <span>{r.detail}</span>}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}

      {/* ---- recording + transcript | answers ---- */}
      <div className="my-survey-grid">
        <div className="my-survey-col">
          {recording}
          {full && (
            <section className="panel my-card" aria-labelledby="tr-title">
              <div className="my-card-head">
                <h2 className="my-card-title" id="tr-title">Transcript</h2>
                {original.length > 0 && (
                  <div className="segmented my-lang" role="tablist" aria-label="Transcript language">
                    <button type="button" role="tab" aria-selected={lang === "en"} className={lang === "en" ? "is-on" : ""} onClick={() => setLang("en")}>English</button>
                    <button type="button" role="tab" aria-selected={lang === "original"} className={lang === "original" ? "is-on" : ""} onClick={() => setLang("original")}>{fb.language || "Original"}</button>
                  </div>
                )}
              </div>
              {rows.length ? (
                <ol className="my-transcript">
                  {rows.map((r) => (
                    <li key={r.key}>
                      {r.time && <span className="my-time">{r.time}</span>}
                      <span>{r.text}</span>
                    </li>
                  ))}
                </ol>
              ) : <p className="muted">No speech was transcribed for this recording.</p>}
            </section>
          )}
        </div>
        <div className="my-survey-col">{answersCard}</div>
      </div>
    </section>
  );
}

/* ---------- Every analyzed survey, across products ---------- */

const SURVEY_TONES = {
  all: { label: "All", test: () => true },
  positive: { label: "Positive", test: (s) => s.feedback?.tone === "positive" },
  mixed: { label: "Mixed", test: (s) => s.feedback?.tone === "mixed" },
  negative: { label: "Negative", test: (s) => s.feedback?.tone === "negative" },
  analyzing: { label: "Analyzing", test: (s) => !s.feedback && ["queued", "processing"].includes(s.analysis?.status) },
  none: { label: "No recording", test: (s) => !s.feedback && !["queued", "processing"].includes(s.analysis?.status) },
};

const SURVEY_SORTS = {
  newest: { label: "Newest first", fn: (a, b) => (parseDate(b.created_at)?.getTime() || 0) - (parseDate(a.created_at)?.getTime() || 0) },
  oldest: { label: "Oldest first", fn: (a, b) => (parseDate(a.created_at)?.getTime() || 0) - (parseDate(b.created_at)?.getTime() || 0) },
  ratingHigh: { label: "Highest rating", fn: (a, b) => (b.feedback?.overall_rating ?? -1) - (a.feedback?.overall_rating ?? -1) },
  ratingLow: { label: "Lowest rating", fn: (a, b) => (a.feedback?.overall_rating ?? 99) - (b.feedback?.overall_rating ?? 99) },
};

function AllSurveysList({ onOpenFeedback }) {
  const [data, setData] = useState(() => pageCache.allSurveys || { loading: true, error: "", items: [], total: 0 });
  const [q, setQ] = useState("");
  const [product, setProduct] = useState("all");
  const [tone, setTone] = useState("all");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePageSize("surveys");

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setData((d) => ({ ...d, loading: true, error: "" }));
    try {
      const res = await getRecentSurveys(500);
      const next = { loading: false, error: "", items: listOf(res, "surveys"), total: res?.total || 0 };
      pageCache.allSurveys = next;
      setData(next);
    } catch (err) {
      if (silent) return; // background refresh: keep what is on screen
      console.error(err);
      setData((d) => ({ ...d, loading: false, error: `The surveys couldn't be loaded. Check that the backend is running at ${API_BASE_URL}.` }));
    }
  }, []);

  useEffect(() => {
    load({ silent: Boolean(pageCache.allSurveys) });
  }, [load]);

  // While analyses are running, check every few seconds so finished ones update in place.
  const running = data.items.some((x) => !x.feedback && ["queued", "processing"].includes(x.analysis?.status));
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => document.visibilityState === "visible" && load({ silent: true }), 4000);
    return () => clearInterval(t);
  }, [running, load]);
  useAutoRefresh(load);
  useEffect(() => setPage(1), [q, product, tone, sort, pageSize]);

  const products = useMemo(
    () => [...new Set(data.items.map((s) => s.product_name).filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [data.items]
  );
  const scoped = useMemo(() => {
    const query = q.trim().toLowerCase();
    return data.items.filter(
      (s) =>
        (product === "all" || s.product_name === product) &&
        (!query ||
          `${s.respondent_label} ${s.area || ""} ${s.product_name} ${s.executive_name || ""} ${s.category_name || ""}`
            .toLowerCase()
            .includes(query))
    );
  }, [data.items, q, product]);
  const rows = useMemo(() => scoped.filter(SURVEY_TONES[tone].test).sort(SURVEY_SORTS[sort].fn), [scoped, tone, sort]);
  const { pages, current, pageRows, from, to } = paginate(rows, page, pageSize);

  const removeSurvey = async (sv) => {
    const ok = await confirmDialog({
      title: `Delete ${sv.respondent_label}'s survey?`,
      message: `${sv.product_name} · ${formatDateTime(sv.created_at)}. Its answers, analysis and recording are deleted permanently and every count updates. This can't be undone.`,
      confirmLabel: "Delete survey",
      cancelLabel: "Keep",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteSurvey(sv.response_id);
      setData((d) => ({ ...d, items: d.items.filter((x) => x.response_id !== sv.response_id) }));
      announceDataChanged();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setData((d) => ({ ...d, error: typeof detail === "string" ? detail : "The survey couldn't be deleted. Try again." }));
    }
  };

  return (
    <section className="panel home-products">
      <div className="table-toolbar">
        <input type="search" className="search" placeholder="Search customer, product, area or executive" value={q}
          onChange={(e) => setQ(e.target.value)} aria-label="Search surveys" />
        <select className="input toolbar-select" value={product} onChange={(e) => setProduct(e.target.value)} aria-label="Filter by product">
          <option value="all">All products</option>
          {products.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select className="input toolbar-select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort surveys">
          {Object.entries(SURVEY_SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <button type="button" className="btn btn-secondary" onClick={load} disabled={data.loading}>
          {data.loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <div className="segmented state-tabs" role="tablist" aria-label="Sentiment">
        {Object.entries(SURVEY_TONES).map(([key, t]) => (
          <button key={key} type="button" role="tab" aria-selected={tone === key} className={tone === key ? "is-on" : ""} onClick={() => setTone(key)}>
            {t.label} <span className="seg-count">{formatCount(scoped.filter(t.test).length)}</span>
          </button>
        ))}
      </div>

      {data.error ? (
        <div className="catalog-empty" role="alert">
          <strong>Couldn't load surveys</strong>
          <p>{data.error}</p>
          <button type="button" className="btn btn-secondary" onClick={load}>Try again</button>
        </div>
      ) : data.loading && !data.items.length ? (
        <p className="muted table-empty">Loading surveys…</p>
      ) : rows.length === 0 ? (
        <p className="muted table-empty">{data.items.length ? "No surveys match these filters." : "No surveys yet."}</p>
      ) : (
        <>
          <div className="table-wrap">
            <table className="data-table">
              <caption className="visually-hidden">Every survey, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Customer</th>
                  <th scope="col">Product</th>
                  <th scope="col" className="col-category">Executive</th>
                  <th scope="col" className="col-category">Date</th>
                  <th scope="col">Rating</th>
                  <th scope="col">Sentiment</th>
                  <th scope="col" className="col-summary">Summary</th>
                  <th scope="col"><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((s) => {
                  const fb = s.feedback;
                  const open = fb ? () => onOpenFeedback(fb.id) : null;
                  return (
                    <tr key={s.response_id} className={open ? "is-clickable" : "is-pending"} onClick={open || undefined}>
                      <td>
                        <div className="cell-product">
                          {open ? (
                            <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); open(); }}>
                              {s.respondent_label}
                            </button>
                          ) : (
                            <strong>{s.respondent_label}</strong>
                          )}
                          <span>{s.area || "—"}</span>
                          <span className="cell-category-inline">{formatDateTime(s.created_at)}</span>
                        </div>
                      </td>
                      <td>
                        <div className="cell-product">
                          <strong title={s.product_name}>{s.product_name}</strong>
                          <span>{s.category_name}</span>
                        </div>
                      </td>
                      <td className="col-category">{s.executive_name || "—"}</td>
                      <td className="col-category">
                        <div className="cell-product">
                          <strong className="cell-date">{timeAgo(s.created_at)}</strong>
                          <span>{formatDateTime(s.created_at)}</span>
                        </div>
                      </td>
                      <td>{fb && toRating(fb.overall_rating) ? <RatingStars rating={fb.overall_rating} compact /> : <span className="muted">—</span>}</td>
                      <td>
                        {fb ? (
                          <SentimentPill sentiment={TONE_SENTIMENT[fb.tone] || SENTIMENTS.neutral} small />
                        ) : s.analysis?.status === "processing" ? (
                          <span className="status-chip is-act analyzing-chip">
                            <span className="spinner chip-spinner" aria-hidden="true" />
                            Analyzing…
                          </span>
                        ) : s.analysis?.status === "queued" ? (
                          <span className="status-chip is-neutral">In queue{s.analysis.position ? ` · #${s.analysis.position}` : ""}</span>
                        ) : s.analysis?.status === "failed" ? (
                          <span className="status-chip is-warn" title={s.analysis.error || ""}>Analysis failed</span>
                        ) : (
                          <span className="status-chip is-warn">No recording</span>
                        )}
                      </td>
                      <td className="col-summary"><span className="summary-clip">{fb?.summary || "—"}</span></td>
                      <td className="cell-action">
                        <button
                          type="button"
                          className="icon-btn danger"
                          aria-label={`Delete the survey of ${s.respondent_label}`}
                          title="Delete survey"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeSurvey(s);
                          }}
                        >
                          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                            <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12M9 7V4h6v3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pager
            label="Survey pages"
            total={rows.length}
            from={from}
            to={to}
            current={current}
            pages={pages}
            pageSize={pageSize}
            onPageSize={setPageSize}
            onPage={setPage}
            note={data.total > data.items.length ? ` (latest ${formatCount(data.items.length)} of ${formatCount(data.total)})` : ""}
          />
        </>
      )}
    </section>
  );
}

/* ---------- Consolidated report ---------- */

function DistributionBars({ distribution, total }) {
  const entries = Object.entries(distribution || {});
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <ul className="dist-bars">
      {entries.map(([label, value]) => (
        <li key={label}>
          <span className="dist-label">{label}</span>
          <span className="dist-track"><span style={{ width: `${(value / max) * 100}%` }} /></span>
          <span className="dist-value">{value}{total ? <small> · {Math.round((value / total) * 100)}%</small> : null}</span>
        </li>
      ))}
    </ul>
  );
}

function BreakdownTable({ rows }) {
  if (!rows?.length) return <p className="muted">No data.</p>;
  return (
    <div className="table-wrap">
      <table className="data-table compact-table">
        <thead>
          <tr><th scope="col">Group</th><th scope="col">Responses</th><th scope="col">Avg rating</th><th scope="col">Sentiment</th></tr>
        </thead>
        <tbody>
          {rows.map((g) => (
            <tr key={g.group}>
              <td>{g.group}</td>
              <td>{g.responses}</td>
              <td>{g.avg_rating != null ? g.avg_rating.toFixed(1) : "—"}</td>
              <td><ToneBar counts={g} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const BREAKDOWNS = { gender: "Gender", age_group: "Age group", city: "City", usage_period: "When used" };

/* ================================================================
   Client summary: the classic product-test report (A, B, C …),
   built from questions tagged with a report role in the Catalog,
   followed by everything else the app finds ("Detailed insights").
   ================================================================ */

function SummaryTable({ head, rows }) {
  return (
    <div className="table-wrap">
      <table className="data-table client-table">
        <thead>
          <tr>{head.map((h, i) => <th key={i} scope="col" className={i > 0 ? "num-cell" : ""}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={r.highlight ? "is-highlight" : r.total ? "is-total" : ""}>
              {r.cells.map((c, j) => <td key={j} className={j > 0 ? "num-cell" : ""}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ClientSummary({ stats, narrative, n, report, product, forClient = false }) {
  const cs = stats.client_summary || {};
  // Same sections as the downloaded PDF (services/consolidatedReportPdf.js).
  const sections = buildClientSections(stats, narrative, n);
  const tagged = cs.preference || cs.price || cs.score || cs.improvement || cs.purchase_intent || cs.ease;
  const [busy, setBusy] = useState(false);

  const download = () => {
    setBusy(true);
    // Let the button show "Preparing…" before the (synchronous) PDF build.
    setTimeout(() => {
      try {
        downloadConsolidatedPdf({ report, product });
      } finally {
        setBusy(false);
      }
    }, 30);
  };

  return (
    <section className="client-summary" aria-labelledby="client-summary-title">
      <div className="client-summary-head">
        <div>
          <p className="eyebrow">Client summary</p>
          <h3 id="client-summary-title" className="client-headline">{clientHeadline(stats, n, narrative)}</h3>
        </div>
        <button type="button" className="btn btn-secondary no-print client-download" onClick={download} disabled={busy}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M12 4v11m0 0l-4-4m4 4l4-4M5 19h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {busy ? "Preparing…" : "Download PDF"}
        </button>
      </div>
      {!tagged && !forClient && (
        <p className="client-tip no-print">
          Tip: in <strong>Catalog → product → Survey questions</strong>, set "Use in the client report as" (preference, price,
          score, improvement…) to get preference by location, the ideal price and the other client tables here.
        </p>
      )}
      <div className="client-sections">
        {sections.map((sec, i) => (
          <section key={sec.title} className="panel client-section">
            <h4 className="client-section-title">
              <span className="client-letter">{String.fromCharCode(65 + i)}</span>
              {sec.title}
            </h4>
            {sec.sub && <p className="muted client-sub">{sec.sub}</p>}
            {sec.table && <SummaryTable head={sec.table.head} rows={sec.table.rows} />}
            {sec.points && (
              <ul className={`client-points${sec.points[0]?.tone === "concern" ? " is-concern" : ""}`}>
                {sec.points.map((p, k) => (
                  <li key={k}><strong>{p.text}</strong><span>{p.meta}</span></li>
                ))}
              </ul>
            )}
            {sec.text && <p className={sec.table ? "muted" : "client-text"}>{sec.text}</p>}
            {(sec.list || []).length > 0 && (
              <>
                <h5 className="client-subhead">Recommended actions</h5>
                <ul className="client-actions">{sec.list.map((a, k) => <li key={k}>{a}</li>)}</ul>
              </>
            )}
            {(sec.suggestions || []).length > 0 && (
              <>
                <h5 className="client-subhead">Customers suggested</h5>
                <ul className="client-actions is-quote">{sec.suggestions.map((a, k) => <li key={k}>{a}</li>)}</ul>
              </>
            )}
            {(sec.recipe || []).length > 0 && (
              <ol className="recipe-rec-list">
                {sec.recipe.map((r, k) => (
                  <li key={k}>
                    <div className="recipe-rec-head">
                      {r.where && <span className="recipe-rec-ingredient">{r.where}</span>}
                      <span className="count-badge">{r.meta}</span>
                    </div>
                    <p className="recipe-rec-change">{r.change}</p>
                    {r.reason && <p className="muted recipe-rec-reason">Because: {r.reason}</p>}
                  </li>
                ))}
              </ol>
            )}
            {sec.note && <p className="client-note">{sec.note}</p>}
          </section>
        ))}
      </div>
      <div className="client-divider"><span>Detailed insights</span></div>
    </section>
  );
}

function ConsolidatedReportView({ report, product, forClient = false }) {
  const [breakdown, setBreakdown] = useState("gender");
  const st = report.stats || {};
  // Saved narratives come in a few shapes (see normalizeNarrative); display them all safely.
  const nv = normalizeNarrative(report.narrative);
  const n = st.response_count || report.response_count || 0;
  const ratingDist = st.rating_distribution || {};

  return (
    <div className="report">
      <ClientSummary stats={st} narrative={nv} n={n} report={report} product={product} forClient={forClient} />

      {/* Rating and sentiment are in the page header; positives, issues, conclusion,
          actions, suggestions and recipe changes are in the client summary above. */}
      <div className="report-grid">
        <section className="panel report-card">
          <h3 className="report-h">Rating distribution</h3>
          <DistributionBars distribution={Object.fromEntries(["5", "4", "3", "2", "1"].map((k) => [`${k} ★`, ratingDist[k] || 0]))} total={n} />
          {st.median_rating != null && <p className="muted report-note">Median rating {Number(st.median_rating).toFixed(1)} out of 5.</p>}
        </section>
        <section className="panel report-card">
          <h3 className="report-h">Neutral feedback</h3>
          {(nv.neutral_feedback || []).length ? (
            <ul className="neutral-list">{nv.neutral_feedback.map((t, k) => <li key={k}>{t}</li>)}</ul>
          ) : (
            <p className="muted">No neutral comments. Every remark was clearly positive or negative.</p>
          )}
        </section>
      </div>

      {(st.aspects || []).length > 0 && (
        <section className="panel report-card">
          <h3 className="report-h">Feedback themes</h3>
          <div className="table-wrap">
            <table className="data-table compact-table">
              <thead>
                <tr>
                  <th scope="col">Theme</th><th scope="col">Mentioned by</th><th scope="col">Praised</th>
                  <th scope="col">Concerns</th><th scope="col">Avg rating</th>
                </tr>
              </thead>
              <tbody>
                {st.aspects.slice(0, 12).map((a) => (
                  <tr key={a.key}>
                    <td>
                      <strong>{a.name}</strong>
                      {a.evidence?.[0] && <span className="theme-quote">“{a.evidence[0]}”</span>}
                    </td>
                    <td>
                      <div className="cell-progress">
                        <div className="mini-progress" aria-hidden="true"><span style={{ width: `${(a.customers / Math.max(1, n)) * 100}%` }} /></div>
                        <span className="cell-progress-text">{a.customers} of {n}</span>
                      </div>
                    </td>
                    <td className="num-positive">{a.positive || "—"}</td>
                    <td className="num-negative">{a.negative || "—"}</td>
                    <td>{a.avg_rating != null ? a.avg_rating.toFixed(1) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {(st.questions || []).length > 0 && (
        <section className="panel report-card">
          <h3 className="report-h">Survey answers</h3>
          <div className="question-results">
            {st.questions.map((qr, i) => (
              <div key={qr.question_id ?? i} className="question-result">
                <p className="question-result-title">
                  <span className="question-num">{i + 1}</span>
                  <span>{qr.question}</span>
                </p>
                {qr.type === "rating" && (
                  <>
                    <p className="question-result-avg">
                      Average <strong>{qr.average != null ? qr.average.toFixed(1) : "—"}</strong> / 5
                    </p>
                    <DistributionBars distribution={Object.fromEntries(["5", "4", "3", "2", "1"].map((k) => [`${k} ★`, qr.distribution?.[k] || 0]))} total={qr.answered} />
                  </>
                )}
                {(qr.type === "yes_no" || qr.type === "single_choice") && (
                  <DistributionBars distribution={qr.distribution} total={qr.answered} />
                )}
                {qr.type === "text" && (() => {
                  // Answers come in many languages: everything shown here is English.
                  const oa = nv.open_answers?.[String(qr.question_id)];
                  const originals = qr.text_answers || [];
                  const translations = oa?.translations || [];
                  const english = translations.filter(Boolean);
                  return (
                    <>
                      {oa?.summary && <p className="open-summary">{oa.summary}</p>}
                      {oa?.points?.length > 0 ? (
                        <ul className="open-points">
                          {oa.points.map((pt, k) => (
                            <li key={k}>
                              <span>{pt.point}</span>
                              {pt.customers != null && <span className="count-badge">{pt.customers} of {oa.answered || qr.answered}</span>}
                            </li>
                          ))}
                        </ul>
                      ) : english.length > 0 ? (
                        <>
                          <p className="field-hint">What customers said, translated to English:</p>
                          <ul className="text-answers translated-answers">
                            {english.map((t, k) => <li key={k}>“{t}”</li>)}
                          </ul>
                        </>
                      ) : !oa ? (
                        <p className="muted open-summary">Regenerate the report to get these answers in English.</p>
                      ) : null}
                      {oa?.error && (
                        <p className="open-error" role="note">
                          {oa.error} Check that Ollama is running, then regenerate the report.
                        </p>
                      )}
                      {originals.length > 0 && (
                        <details className="original-answers">
                          <summary>Original answers ({originals.length})</summary>
                          <ul className="text-answers original-list">
                            {originals.map((t, k) => (
                              <li key={k}>
                                <span>“{t}”</span>
                                {translations[k] && translations[k] !== t && (
                                  <span className="original-translation">{translations[k]}</span>
                                )}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </>
                  );
                })()}
              </div>
            ))}
          </div>
        </section>
      )}

      {st.breakdowns && (
        <section className="panel report-card">
          <div className="report-head-row">
            <h3 className="report-h">Breakdown</h3>
            <div className="segmented" role="tablist" aria-label="Breakdown by">
              {Object.entries(BREAKDOWNS).map(([k, label]) => (
                <button key={k} type="button" role="tab" aria-selected={breakdown === k} className={breakdown === k ? "is-on" : ""} onClick={() => setBreakdown(k)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <BreakdownTable rows={st.breakdowns[breakdown]} />
        </section>
      )}

      <p className="footnote report-footnote">
        All numbers are counted directly from the {n} stored analyses.{" "}
        {nv.generated_by === "ai"
          ? "The wording was written by AI from those numbers; counts it gave were checked against them."
          : "The AI writer was unavailable, so this wording was generated from the numbers alone."}{" "}
        Respondents' names and contact details are not used in this report.
      </p>
    </div>
  );
}

/* ---------- One product: report + every customer's feedback ---------- */

function ProductFeedbackPage({ productId, initialRound = null, onBack, onOpenFeedback }) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [tab, setTab] = useState("report");
  const [round, setRound] = useState(initialRound); // null = every survey of the product

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      setState({ loading: false, error: "", data: await getProductFeedback(productId, round) });
    } catch (err) {
      if (silent) return; // background refresh: keep what is on screen
      console.error(err);
      setState({ loading: false, error: "This product's feedback couldn't be loaded.", data: null });
    }
  }, [productId, round]);

  useEffect(() => {
    load();
  }, [load]);
  // Not while a report is being generated (it updates the page itself).
  useAutoRefresh((opts) => (generating ? null : load(opts)));
  const waitingForReport = Boolean(
    state.data && state.data.summary?.can_consolidate && (!state.data.report || state.data.summary?.report_updating)
  );
  useEffect(() => {
    if (!waitingForReport || generating) return undefined;
    const t = setInterval(() => document.visibilityState === "visible" && load({ silent: true }), 6000);
    return () => clearInterval(t);
  }, [waitingForReport, generating, load]);

  useEffect(() => {
    if (!generating) return undefined;
    setElapsed(0);
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [generating]);

  const generate = async () => {
    setGenerating(true);
    setGenError("");
    try {
      const report = await generateConsolidatedReport(productId, round);
      setState((s) => ({ ...s, data: { ...s.data, report } }));
      setTab("report");
    } catch (err) {
      const d = err?.response?.data?.detail;
      setGenError(typeof d === "string" ? d : "The report couldn't be generated. Check that the backend and Ollama are running.");
    } finally {
      setGenerating(false);
    }
  };

  if (state.loading && !state.data) {
    return (
      <div className="panel history-loading">
        <span className="spinner" aria-hidden="true" />
        <p>Loading product feedback…</p>
      </div>
    );
  }
  if (state.error) {
    return (
      <section className="feedback-detail">
        <button type="button" className="back-link" onClick={onBack}><ChevronIcon dir="left" /> Survey history</button>
        <div className="catalog-empty" role="alert">
          <strong>{state.error}</strong>
          <button type="button" className="btn btn-secondary" onClick={load}>Try again</button>
        </div>
      </section>
    );
  }

  const { product, summary, responses, report } = state.data;
  const rounds = state.data.rounds || [];
  const shown = round != null ? rounds.find((r) => r.number === round) : null;
  const live = rounds.find((r) => r.status === "live");
  const analyzed = responses.filter((r) => r.status === "analyzed" && r.feedback);
  const pending = responses.filter((r) => !(r.status === "analyzed" && r.feedback));
  const updating = Boolean(summary.report_updating); // the worker is refreshing it

  return (
    <section className="feedback-detail product-feedback" aria-labelledby="pf-title">
      <button type="button" className="back-link" onClick={onBack}><ChevronIcon dir="left" /> Survey history</button>

      <header className="home-head">
        <div>
          <p className="eyebrow">{product.category_name}</p>
          <h1 id="pf-title">{product.name}</h1>
          <p className="home-sub">
            {formatCount(summary.responses)} customer {summary.responses === 1 ? "response" : "responses"} analyzed
            {shown ? ` in Round ${shown.number}` : rounds.length > 1 ? ` in ${rounds.length} rounds` : ""}
            {product.sku ? ` · ${product.sku}` : ""}
          </p>
          {live && <span className="product-badges"><span className="round-chip is-live">Round {live.number} · {live.done} of {live.target}</span></span>}
        </div>
        {rounds.length > 0 && (
          <div className="segmented round-picker" role="group" aria-label="Survey round">
            <button type="button" aria-pressed={round == null} className={round == null ? "is-on" : ""} onClick={() => setRound(null)}>All rounds</button>
            {rounds.map((r) => (
              <button key={r.number} type="button" aria-pressed={round === r.number} className={round === r.number ? "is-on" : ""} onClick={() => setRound(r.number)}>
                Round {r.number}{r.status === "live" ? " · live" : ""}
              </button>
            ))}
          </div>
        )}
      </header>

      <div className="home-stats">
        <div className="h-stat">
          <span className="h-stat-label">Responses</span>
          <span className="h-stat-value">{formatCount(summary.responses)}</span>
          <span className="h-stat-note">{summary.pending ? `${summary.pending} awaiting a recording` : "analyzed surveys"}</span>
        </div>
        <div className="h-stat">
          <span className="h-stat-label">Average rating</span>
          <span className="h-stat-value h-stat-rating">
            {summary.avg_rating != null ? summary.avg_rating.toFixed(1) : "—"}
            {summary.avg_rating != null && <RatingStars rating={summary.avg_rating} showValue={false} compact />}
          </span>
          <span className="h-stat-note">from individual analyses</span>
        </div>
        <div className="h-stat h-stat-wide">
          <span className="h-stat-label">Customer sentiment</span>
          <ToneBar counts={summary.sentiment} />
          <div className="bar-legend">
            <span><i className="dot dot-positive" />{summary.sentiment.positive} positive</span>
            <span><i className="dot dot-mixed" />{summary.sentiment.mixed} mixed</span>
            <span><i className="dot dot-negative" />{summary.sentiment.negative} negative</span>
          </div>
        </div>
      </div>

      <div className="segmented page-tabs" role="tablist" aria-label="Product feedback">
        <button type="button" role="tab" aria-selected={tab === "report"} className={tab === "report" ? "is-on" : ""} onClick={() => setTab("report")}>
          Consolidated feedback
        </button>
        <button type="button" role="tab" aria-selected={tab === "responses"} className={tab === "responses" ? "is-on" : ""} onClick={() => setTab("responses")}>
          Customer feedback <span className="seg-count">{responses.length}</span>
        </button>
      </div>

      {tab === "report" && (
        <>
          <div className={`report-bar${report ? "" : " is-empty"}`}>
            <div>
              {generating ? (
                <>
                  <strong>Updating the consolidated report… {elapsed}s</strong>
                  <span>Combining {summary.responses} stored analyses and writing the summary. This usually takes under a minute.</span>
                </>
              ) : report ? (
                <>
                  <strong>
                    {shown ? `Round ${shown.number} report` : "Consolidated report"}
                    {updating && <span className="status-chip is-act report-updating-chip">Updating with the latest analyses…</span>}
                  </strong>
                  <span>
                    {shown
                      ? `Recipe v${shown.recipe_version ?? "—"} · ${shown.done} of ${shown.target} consumers · started by ${shown.started_by_name}${shown.note ? ` · “${shown.note}”` : ""} · `
                      : ""}
                    Based on {formatCount(report.response_count)} {report.response_count === 1 ? "survey" : "surveys"} · updated{" "}
                    {timeAgo(report.generated_at)}. It updates by itself after every new analysis.
                  </span>
                </>
              ) : summary.can_consolidate ? (
                <>
                  <strong>Creating the consolidated report…</strong>
                  <span>It's made automatically from the analyzed surveys, usually within a minute. You can also create it now.</span>
                </>
              ) : (
                <>
                  <strong>No analyzed surveys yet</strong>
                  <span>The consolidated report appears automatically after the first survey of {product.name} is analyzed.</span>
                </>
              )}
            </div>
            {generating ? (
              <span className="spinner" aria-hidden="true" />
            ) : summary.can_consolidate ? (
              <button type="button" className={`btn ${report ? "btn-secondary" : "btn-primary"}`} onClick={generate}>
                {report ? "Refresh now" : "Create now"}
              </button>
            ) : null}
          </div>
          {genError && <p className="alert alert-error report-error" role="alert">{genError}</p>}
          {report && !generating && (
            <ConsolidatedReportView report={report} product={shown ? { ...product, name: `${product.name} · Round ${shown.number}` } : product} />
          )}
        </>
      )}

      {tab === "responses" && (
        <section className="panel home-products">
          {responses.length === 0 ? (
            <p className="muted table-empty">No surveys for {product.name}{shown ? ` in Round ${shown.number}` : ""} yet.</p>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <caption className="visually-hidden">Customer feedback for {product.name}</caption>
                <thead>
                  <tr>
                    <th scope="col">Customer</th>
                    <th scope="col" className="col-category">City</th>
                    {rounds.length > 0 && <th scope="col">Round</th>}
                    <th scope="col">Rating</th>
                    <th scope="col">Sentiment</th>
                    <th scope="col" className="col-summary">Summary</th>
                    <th scope="col" className="col-category">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {[...analyzed, ...pending].map((r) => {
                    const fb = r.feedback;
                    const who = r.respondent;
                    const open = fb && r.status === "analyzed" ? () => onOpenFeedback(fb.id) : null;
                    return (
                      <tr key={r.response_id} className={open ? "is-clickable" : "is-pending"} onClick={open || undefined}>
                        <td>
                          <div className="cell-product">
                            {open ? (
                              <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); open(); }}>
                                {who?.name || `Respondent #${r.response_id}`}
                              </button>
                            ) : (
                              <strong>{who?.name || `Respondent #${r.response_id}`}</strong>
                            )}
                            <span>{[who?.gender, who?.age ? `${who.age} yrs` : null].filter(Boolean).join(" · ") || `Survey #${r.response_id}`}</span>
                            <span className="cell-category-inline">{who?.location}</span>
                          </div>
                        </td>
                        <td className="col-category">{who?.location || "—"}</td>
                        {rounds.length > 0 && <td>{r.round ? <span className="round-chip">{r.round}</span> : "—"}</td>}
                        <td>{fb && toRating(fb.overall_rating) ? <RatingStars rating={fb.overall_rating} compact /> : <span className="muted">—</span>}</td>
                        <td>
                          {fb ? (
                            <SentimentPill sentiment={TONE_SENTIMENT[fb.tone] || SENTIMENTS.neutral} small />
                          ) : (
                            <span className="status-chip is-warn">Awaiting recording</span>
                          )}
                        </td>
                        <td className="col-summary">
                          <span className="summary-clip">{fb?.summary || "—"}</span>
                        </td>
                        <td className="col-category">{formatDateTime(fb?.uploaded_at || r.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </section>
  );
}

class ResultsBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Results failed to render:", error, info?.componentStack);
  }

  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="panel render-error">
        <h2>This analysis couldn't be displayed</h2>
        <p>
          The video was processed, but part of the result has a format this page doesn't expect. The analysis is
          still saved.
        </p>
        <pre>{String(this.state.error?.message || this.state.error)}</pre>
        <div className="render-error-actions">
          {this.props.onOpenHistory && (
            <button type="button" className="btn btn-primary" onClick={this.props.onOpenHistory}>
              Open it in survey history
            </button>
          )}
          {this.props.onRetry && (
            <button type="button" className="btn btn-secondary" onClick={this.props.onRetry}>
              Back to upload
            </button>
          )}
        </div>
      </div>
    );
  }
}

/* ================================================================
   Roles: which pages each person may open
   ================================================================ */

const ROLE_PAGES = {
  admin: ["home", "history", "catalog", "team"],
  executive: ["survey", "analyze", "mine"], // "analyze" = step 4 of a survey in progress
};
const ROLE_START = { admin: "home", executive: "survey" };
const PAGE_SLUG = { home: "home", survey: "take-survey", analyze: "record", mine: "my-surveys", history: "survey-history", catalog: "catalog", team: "team" };
const SLUG_PAGE = Object.fromEntries(Object.entries(PAGE_SLUG).map(([k, v]) => [v, k]));
const PAGE_NAME = { home: "Home", survey: "Take survey", analyze: "Record feedback", mine: "My surveys", history: "Survey history", catalog: "Catalog", team: "Team" };

const roleOf = (user) => (user?.role === "admin" ? "admin" : user?.role === "client" ? "client" : "executive");
const ROLE_LABEL = { admin: "Admin", executive: "Survey person", client: "Organization" };
const pageFromHash = () => SLUG_PAGE[(window.location.hash || "").replace(/^#\/?/, "").split(/[/?]/)[0]] || null;

function UserMenu({ user }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const initial = (user?.name || "?").trim()[0]?.toUpperCase();
  const role = ROLE_LABEL[roleOf(user)];
  return (
    <div className="user-menu" ref={ref}>
      <button type="button" className="user-chip" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        aria-label={`${user?.name}, ${role}. Account menu`}>
        <span className="user-avatar" aria-hidden="true">{initial}</span>
        <span className="user-chip-text">
          <strong>{user?.name}</strong>
          <span>{role}</span>
        </span>
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <span className="user-avatar user-avatar-lg" aria-hidden="true">{initial}</span>
            <span>
              <strong>{user?.name}</strong>
              <span>{user?.mobile || user?.email}</span>
              <span className="status-chip is-act user-role">{role}</span>
            </span>
          </div>
          <button type="button" role="menuitem" className="user-pop-item" onClick={() => signOut("")}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function AppContent({ user }) {
  const role = roleOf(user);
  const isAdminUser = role === "admin";
  const allowedPages = ROLE_PAGES[role];
  const startPage = ROLE_START[role];
  // home | survey | analyze | history | catalog | team, limited to this person's role
  const [view, setView] = useState(() => {
    const fromAddress = pageFromHash();
    return fromAddress && ROLE_PAGES[roleOf(user)].includes(fromAddress) ? fromAddress : ROLE_START[roleOf(user)];
  });
  const [accessNotice, setAccessNotice] = useState("");
  const [homeKey, setHomeKey] = useState(0); // new key = refreshed dashboard
  const [surveyInProgress, setSurveyInProgress] = useState(false); // survey form has entries

  // Field survey in progress. Steps 1-3 happen in SurveyWorkflow; step 4 (the
  // customer's recording) on the Analyze page. The survey is saved only when the
  // analysis starts, so it stays editable until then.
  const [surveyDraft, setSurveyDraft] = useState(null);
  const surveyRef = useRef(null);
  const [surveyKey, setSurveyKey] = useState(0); // new key = fresh survey form
  const [surveyContext, setSurveyContext] = useState(null); // carried to "next respondent"
  const surveySaved = surveyDraft?.respondentId != null;

  const [stage, setStage] = useState("idle"); // idle | uploading | processing | done | error
  const [progress, setProgress] = useState(0);
  const [data, setData] = useState(null);
  const [selectedPart, setSelectedPart] = useState(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);

  // Feedback history
  const [feedbackHistory, setFeedbackHistory] = useState([]);
  const [, setHistoryLoading] = useState(false);
  const [, setHistoryError] = useState("");
  const [selectedFeedback, setSelectedFeedback] = useState(null);
  const [historyProductId, setHistoryProductId] = useState(null); // product page in Feedback history
  const [historyMode, setHistoryMode] = useState("products"); // products | unlinked
  const [historyTab, setHistoryTab] = useState("products"); // products | surveys
  // The analysis running in the background: { filename, status, position, elapsed }
  const [job, setJob] = useState(null);
  // Analyzed surveys (the same number History shows), for the tab badge.
  const [analyzedCount, setAnalyzedCount] = useState(null);
  const [unlinked, setUnlinked] = useState({ loading: false, error: "", items: [] });
  const [selectedFeedbackLoading, setSelectedFeedbackLoading] = useState(false);

  const [videoUrl, setVideoUrl] = useState("");
  const [videoError, setVideoError] = useState(false);
  const [videoDuration, setVideoDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [copied, setCopied] = useState(false);

  const videoRef = useRef(null);
  const listRef = useRef(null);
  const segmentRefs = useRef([]);

  const busy = stage === "uploading" || stage === "processing";

  // Upload a file or record on the spot
  const [inputMode, setInputMode] = useState("upload"); // upload | record
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  const [savedRecordings, setSavedRecordings] = useState([]);
  const [failedFile, setFailedFile] = useState(null); // a fresh recording whose upload failed

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  const refreshSaved = useCallback(async () => {
    try {
      setSavedRecordings(await listRecordings());
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    refreshSaved();
  }, [refreshSaved]);

  const handleSaveRecording = async (recording) => {
    await requestPersistentStorage();
    await saveRecording(recording);
    await refreshSaved();
  };

  const handleDeleteSaved = async (item) => {
    await deleteRecording(item.id);
    await refreshSaved();
  };

  // Free the previous preview URL whenever a new video is chosen.
  useEffect(() => {
    return () => {
      if (videoUrl) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  // Popup: close on Escape and stop the page behind it from scrolling.
  useEffect(() => {
    if (!selectedPart) return;
    const onKey = (e) => e.key === "Escape" && setSelectedPart(null);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [selectedPart]);

  // silent: used on page load and after an upload, so errors don't show on the upload page.
  // Analyzed surveys: the same number History shows (not every row in feedback_records).
  const loadAnalyzedCount = useCallback(async () => {
    if (!isAdminUser) return; // survey history is admin-only
    try {
      const res = await getRecentSurveys(1);
      setAnalyzedCount(res?.analyzed_total ?? null);
    } catch {
      // Older backend or offline: keep the last number.
    }
  }, [isAdminUser]);

  useEffect(() => {
    loadAnalyzedCount();
  }, [loadAnalyzedCount]);

  const loadFeedbackHistory = useCallback(async ({ silent = false } = {}) => {
    if (!isAdminUser) return; // the full list of recordings is admin-only
    setHistoryLoading(true);
    if (!silent) setHistoryError("");
    try {
      setFeedbackHistory(await getFeedbackHistory());
      setHistoryError("");
    } catch (err) {
      console.error(err);
      if (!silent) setHistoryError(`Unable to load survey history. Make sure the backend is running at ${API_BASE_URL}.`);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useAutoRefresh(
    useCallback(
      (opts) => {
        loadAnalyzedCount();
        loadFeedbackHistory(opts);
      },
      [loadAnalyzedCount, loadFeedbackHistory]
    )
  );

  useEffect(() => {
    loadFeedbackHistory({ silent: true });
  }, [loadFeedbackHistory]);

  // Never show a page outside this person's role, however it was reached.
  useEffect(() => {
    if (!allowedPages.includes(view)) setView(startPage);
  }, [view, allowedPages, startPage]);

  // Page -> address bar (#/team), so pages can be bookmarked and Back works.
  useEffect(() => {
    const want = `#/${PAGE_SLUG[view]}`;
    if (allowedPages.includes(view) && window.location.hash !== want) window.location.hash = want;
  }, [view, allowedPages]);

  // Address bar -> page, refusing pages this role may not open.
  useEffect(() => {
    const onHash = () => {
      const page = pageFromHash();
      if (!page) {
        window.location.hash = `#/${PAGE_SLUG[startPage]}`;
        return;
      }
      if (!allowedPages.includes(page)) {
        setAccessNotice(`${PAGE_NAME[page]} isn't available to your account.`);
        window.location.hash = `#/${PAGE_SLUG[allowedPages.includes(view) ? view : startPage]}`;
        return;
      }
      setSelectedFeedback(null);
      setView(page);
    };
    window.addEventListener("hashchange", onHash);
    if (pageFromHash() && !allowedPages.includes(pageFromHash())) onHash(); // opened with a forbidden address
    return () => window.removeEventListener("hashchange", onHash);
  }, [allowedPages, startPage, view]);

  useEffect(() => {
    if (!accessNotice) return undefined;
    const t = setTimeout(() => setAccessNotice(""), 5000);
    return () => clearTimeout(t);
  }, [accessNotice]);

  const goStart = () => {
    setView(startPage);
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  const goHome = () => {
    setView("home");
    setSelectedFeedback(null);
    setHomeKey((k) => k + 1);
    loadFeedbackHistory({ silent: true });
    window.scrollTo({ top: 0 });
  };

  // Start a fresh survey, optionally with the product already chosen.
  // Asks first if a survey is half-way through, so nothing is lost by accident.
  const startNewSurvey = async (product) => {
    const draft = surveyRef.current;
    const unfinished = (draft && draft.responseId == null) || (!draft && surveyInProgress);
    if (unfinished) {
      const ok = await confirmDialog({
        title: "Discard the survey in progress?",
        message: "It hasn't been analyzed yet, so the details and answers entered so far will be lost.",
        confirmLabel: "Discard and start new",
        cancelLabel: "Keep it",
        danger: true,
      });
      if (!ok) return;
    }
    setSurveyContext(product ? { categoryId: String(product.category_id), productId: String(product.id) } : null);
    surveyRef.current = null;
    setSurveyDraft(null);
    setSurveyInProgress(false);
    setSurveyKey((k) => k + 1);
    setStage("idle");
    setData(null);
    setFile(null);
    setError("");
    setView("survey");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  // Back to wherever the current survey is: step 4 on Analyze, or the form.
  const continueSurvey = () => {
    if (surveyRef.current && surveyRef.current.responseId == null) {
      setView("analyze");
      window.scrollTo({ top: 0 });
    } else {
      goSurvey();
    }
  };

  const [catalogRequest, setCatalogRequest] = useState(null); // open a product in Catalog (from the bell)
  const goCatalog = () => {
    setCatalogRequest(null);
    setView("catalog");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  // Bell: "Recipe updated by the organization" -> Catalog, that product, Recipe tab
  // (with its history); "Round started" -> the Rounds tab.
  const openCatalogProduct = (productId, tab = "details") => {
    setCatalogRequest({ productId, tab, at: Date.now() });
    setView("catalog");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  const goTeam = () => {
    setView("team");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  const goHistory = () => {
    setView("history");
    setSelectedFeedback(null);
    setHistoryProductId(null);
    setHistoryMode("products");
    loadFeedbackHistory();
    window.scrollTo({ top: 0 });
  };

  const [historyRound, setHistoryRound] = useState(null); // round shown on the product page (null = all)
  const openHistoryProduct = (productId, round = null) => {
    setHistoryRound(round);
    setHistoryProductId(productId);
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  // Catalog -> Rounds -> "Open report": Survey history, that product, that round.
  const openProductRound = (productId, round) => {
    setView("history");
    setHistoryMode("products");
    openHistoryProduct(productId, round);
  };

  const openUnlinked = async () => {
    setHistoryMode("unlinked");
    setUnlinked((u) => ({ ...u, loading: true, error: "" }));
    window.scrollTo({ top: 0 });
    try {
      const res = await getUnlinkedFeedback();
      setUnlinked({ loading: false, error: "", items: listOf(res, "feedback") });
    } catch (err) {
      console.error(err);
      setUnlinked({ loading: false, error: "These recordings couldn't be loaded.", items: [] });
    }
  };

  const goSurvey = () => {
    setView("survey");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  const goMine = () => {
    setView("mine");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  // The recording step without a survey in progress has nothing to show: go to My surveys.
  useEffect(() => {
    if (view === "analyze" && !surveyDraft && stage === "idle" && allowedPages.includes("mine")) setView("mine");
  }, [view, surveyDraft, stage, allowedPages]);

  // Survey steps 1-3 done (or re-confirmed after editing): continue on the Analyze page.
  const handleSurveyReady = (draft) => {
    if (draft) {
      const prev = surveyRef.current;
      const next = {
        ...draft,
        respondentId: prev?.respondentId ?? null,
        responseId: prev?.responseId ?? null,
        submitted: prev?.submitted ?? false,
      };
      surveyRef.current = next;
      setSurveyDraft(next);
    }
    if (stage !== "done") {
      setStage("idle");
      setError("");
    }
    setView("analyze");
    setSelectedFeedback(null);
    window.scrollTo({ top: 0 });
  };

  // Saves the survey once: respondent, survey response, answers, submit. Each part
  // is remembered as it succeeds, so a retry continues where it stopped.
  const saveSurveyIfNeeded = async () => {
    let d = surveyRef.current;
    if (!d) return null;
    const commit = (patch) => {
      d = { ...d, ...patch };
      surveyRef.current = d;
      setSurveyDraft(d);
    };
    if (d.respondentId == null) {
      const r = await createRespondent(d.respondent);
      const id = r?.id ?? r?.respondent_id;
      if (id == null) throw new Error("The respondent was saved but the server returned no ID.");
      commit({ respondentId: id });
    }
    if (d.responseId == null) {
      const r = await createSurveyResponse({ ...d.response, respondent_id: d.respondentId });
      const id = r?.id ?? r?.response_id;
      if (id == null) throw new Error("The survey was saved but the server returned no ID.");
      commit({ responseId: id });
    }
    if (!d.submitted) {
      await saveSurveyAnswers(d.responseId, d.answers);
      // Make sure the answers really arrived before the recording is uploaded:
      // otherwise the analysis would end up without them (they can't be added later).
      const sent = (d.answers || []).filter((a) => String(a.value ?? "").trim()).length;
      if (sent) {
        try {
          const check = await axios.get(`${API_BASE_URL}/auth/me/surveys/${d.responseId}`);
          if (!(check.data?.answers || []).length) {
            throw Object.assign(
              new Error("The survey answers weren't saved. The questions may have changed meanwhile. Go back to the questions, check them and try again."),
              { answersLost: true }
            );
          }
        } catch (err) {
          if (err.answersLost) throw err; // other problems (e.g. an older backend): carry on as before
        }
      }
      await submitSurveyResponse(d.responseId);
      commit({ submitted: true });
    }
    return d.responseId;
  };

  // After a survey's analysis: start the next one.
  const startNextSurvey = (keepProduct) => {
    setSurveyContext(keepProduct ? surveyRef.current?.context ?? null : null);
    surveyRef.current = null;
    setSurveyDraft(null);
    setSurveyKey((k) => k + 1);
    setStage("idle");
    setData(null);
    setFile(null);
    setError("");
    setView("survey");
    loadFeedbackHistory({ silent: true });
    window.scrollTo({ top: 0 });
  };

  const openFeedback = async (feedbackId) => {
    setView("history");
    setSelectedFeedback(null);
    setSelectedFeedbackLoading(true);
    setHistoryError("");
    window.scrollTo({ top: 0 });

    try {
      setSelectedFeedback(await getFeedbackById(feedbackId));
    } catch (err) {
      console.error(err);
      setHistoryError("Unable to load this feedback record.");
    } finally {
      setSelectedFeedbackLoading(false);
    }
  };

  // savedId: the saved recording this upload came from (deleted from the device once analysis succeeds).
  // fromRecorder: a fresh recording that isn't saved yet, so a failed upload can offer to save it.
  const analyzeFile = async (f, { savedId = null, fromRecorder = false } = {}) => {
    if (!f) return;
    if (!navigator.onLine) {
      setError("You're offline. Connect to the internet to analyze, or save the recording for later.");
      return;
    }
    setFailedFile(null);

    setView("analyze");
    setSelectedFeedback(null);
    setFile(f);
    setVideoUrl(URL.createObjectURL(f));
    setVideoError(false);
    setVideoDuration(0);
    setCurrentTime(0);
    setIsPlaying(false);
    setData(null);
    setSelectedPart(null);
    setError("");
    setQuery("");
    setFilter("all");
    setProgress(0);
    setStage("uploading");

    try {
      // Field survey: save it first, then link the recording to it.
      let responseId = null;
      try {
        responseId = await saveSurveyIfNeeded();
      } catch (err) {
        if (err?.answersLost) throw err; // its own message explains what to do
        const reason = typeof err?.response?.data?.detail === "string" ? err.response.data.detail : err?.message;
        throw Object.assign(new Error(`The survey couldn't be saved${reason ? `: ${reason}` : "."}`), { surveySaveFailed: true });
      }
      const onUpload = (pct) => {
        setProgress(pct);
        if (pct >= 100) setStage("processing");
      };
      let result;
      try {
        // Queue it: the upload returns at once and the analysis runs in the background,
        // so every other page keeps working meanwhile.
        const queued = await startAnalysis(f, responseId, onUpload);
        setStage("processing");
        setJob({ filename: f.name, status: queued.status, position: queued.position, elapsed: 0 });
        const finished = await waitForAnalysis(queued.job_id, (j) =>
          setJob({ filename: f.name, status: j.status, position: j.position, elapsed: j.elapsed_seconds })
        );
        result = finished.result;
      } catch (err) {
        // Older backend without the queue: analyze directly, as before.
        if (err?.response?.status !== 404 || err?.config?.url?.includes("/analysis/jobs/")) throw err;
        result = await uploadVideo(f, onUpload, responseId);
      } finally {
        setJob(null);
      }
      setData(result);
      setStage("done");
      loadFeedbackHistory({ silent: true });
      announceDataChanged(); // every list on screen refreshes
      if (savedId) {
        await deleteRecording(savedId).catch(() => {});
        refreshSaved();
      }
    } catch (err) {
      console.error(err);
      const noNetwork = !navigator.onLine || err?.code === "ERR_NETWORK";
      // The backend explains rejected files (e.g. "This file is incomplete…").
      const serverReason = err?.analysisFailed || err?.answersLost
        ? err.message
        : typeof err?.response?.data?.detail === "string"
        ? err.response.data.detail
        : "";
      setError(
        err?.surveySaveFailed
          ? `${err.message} Check the connection and try again; nothing was lost.`
          : noNetwork
          ? `The connection dropped before the upload finished, or the backend isn't running at ${API_BASE_URL}.`
          : serverReason ||
              "The video couldn't be processed on the server. Try again, or check the backend log for details."
      );
      if (fromRecorder) setFailedFile(f);
      setStage("error");
    }
  };

  const onDrop = (accepted) => analyzeFile(accepted[0]);

  const analyzeSaved = (item) => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    analyzeFile(recordingToFile(item), { savedId: item.id });
  };

  const saveFailedFile = async () => {
    if (!failedFile) return;
    await handleSaveRecording({
      name: failedFile.name,
      blob: failedFile,
      mimeType: failedFile.type,
      kind: isAudioFile(failedFile) ? "audio" : "video",
      duration: 0,
    });
    setFailedFile(null);
    setError("Saved on this device. Analyze it from \"Saved recordings\" when the connection is better.");
  };

  const { getRootProps, getInputProps, isDragActive, fileRejections, open } = useDropzone({
    onDrop,
    multiple: false,
    disabled: busy,
    accept: {
      "video/mp4": [".mp4"],
      "video/quicktime": [".mov"],
      "video/x-msvideo": [".avi"],
      "video/x-matroska": [".mkv"],
      "video/webm": [".webm"],
      "audio/mpeg": [".mp3"],
      "audio/wav": [".wav"],
      "audio/mp4": [".m4a"],
      "audio/ogg": [".ogg"],
      "audio/webm": [".weba"],
    },
  });

  const segments = useMemo(() => {
    const raw = Array.isArray(data?.transcript) ? data.transcript : [];
    return raw.map((s, i) => {
      const start = Number(s.start) || 0;
      const next = raw[i + 1] ? Number(raw[i + 1].start) : undefined;
      const end = Number(s.end) || next || start + 5;
      return { text: s.text || "", start, end, tone: classify(s.text || "") };
    });
  }, [data]);

  const counts = useMemo(() => countTones(segments.map((s) => s.text)), [segments]);
  const insights = useMemo(
    () => {
      const items = (data?.chapters || []).flatMap((c) => c.feedback || []);
      return buildInsights(data?.summary || "", counts, items, feedbackTone(data?.overall_rating, sentimentLabelOf(data), items));
    },
    [data, counts]
  );

  // Real length, read from the video file itself. The transcript is only a fallback.
  const duration = videoDuration || (segments.length ? segments[segments.length - 1].end : 0);

  const chapters = data?.chapters || [];
  const partsCount = Number(data?.chunks_processed) || chapters.length;
  const aspectRatings = data?.aspect_ratings || [];
  const overallRating = toRating(data?.overall_rating);
  const savedId = data?.feedback_id ?? data?.id;

  // A part can't end after the video does (the backend uses fixed-length parts).
  const partEnd = (part) => {
    const end = Number(part?.end) || 0;
    return duration ? Math.min(end, duration) : end;
  };

  const activeIndex = useMemo(
    () => segments.findIndex((s) => currentTime >= s.start && currentTime < s.end),
    [segments, currentTime]
  );

  const visibleSegments = useMemo(() => {
    const q = query.trim().toLowerCase();
    return segments
      .map((s, index) => ({ ...s, index }))
      .filter((s) => (filter === "all" ? true : s.tone === filter))
      .filter((s) => (q ? s.text.toLowerCase().includes(q) : true));
  }, [segments, filter, query]);

  // Keep the line being spoken in view while the video plays.
  useEffect(() => {
    if (!isPlaying || activeIndex < 0) return;
    const list = listRef.current;
    const el = segmentRefs.current[activeIndex];
    if (!list || !el) return;
    list.scrollTo({ top: el.offsetTop - list.clientHeight / 3, behavior: "smooth" });
  }, [activeIndex, isPlaying]);

  const seekTo = (t) => {
    const v = videoRef.current;
    if (v && !videoError) {
      v.currentTime = t;
      v.play().catch(() => {});
    }
    setCurrentTime(t);
  };

  const copySummary = async () => {
    try {
      await navigator.clipboard.writeText(summaryToText(data?.summary));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard not available */
    }
  };

  const downloadReport = () => {
    const name = file?.name || data?.filename;
    const report = buildReport({
      filename: name,
      date: { label: "Analyzed on", value: formatDateTime(new Date().toISOString()) },
      duration,
      processingTime: data?.processing_time_seconds,
      sentiment: insights.sentiment,
      insights,
      parts: chapters,
      partEndFn: partEnd,
      transcript: segments.length ? segments : data?.transcript,
      overallRating,
      aspectRatings,
    });
    downloadFeedbackPdf(report, `${baseName(name)}-feedback-report.pdf`);
  };

  useEffect(() => {
    const base = "FMCG Video Learning Engine";
    let page = "Record feedback";
    if (view === "history") page = selectedFeedback ? selectedFeedback.filename || "Feedback" : "Survey history";
    else if (view === "survey") page = "Take survey";
    else if (view === "home") page = "Home";
    else if (view === "team") page = "Team";
    else if (view === "catalog") page = "Catalog";
    else if (view === "mine") page = "My surveys";
    else if (busy) page = "Analyzing…";
    else if (stage === "done" && data) page = file?.name || data.filename || "Results";
    document.title = `${page} · ${base}`;
  }, [view, selectedFeedback, busy, stage, data, file]);

  const uploadStep = stage === "uploading" ? "active" : "done";
  const analyzeStep = stage === "processing" ? "active" : "pending";
  const selectedLabel = selectedPart ? partLabel(selectedPart) : "";

  return (
    <div className="app">
      <input {...getInputProps()} />
      <ConfirmHost />
      {job && view !== "analyze" && (
        <div className="bg-job" role="status" aria-live="polite">
          <span className="spinner bg-job-spinner" aria-hidden="true" />
          <div className="bg-job-text">
            <strong>{job.status === "queued" ? "Waiting to analyze" : "Analyzing in the background"}</strong>
            <span>
              {job.filename}
              {job.status === "queued" ? ` · position ${job.position}` : ` · ${formatDuration(job.elapsed || 0)}`}
            </span>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setView("analyze")}>
            View
          </button>
        </div>
      )}

      <a className="skip-link" href="#main">Skip to content</a>

      <header className="topbar">
        <div className="topbar-inner">
          <button type="button" className="brand" onClick={goStart} aria-label="FMCG Video Learning Engine, go to the start page">
            <span className="brand-mark" aria-hidden="true"><PlayGlyph size={16} /></span>
            <span className="brand-name">FMCG Video Learning Engine</span>
          </button>

          <div className="topbar-right">
            {view === "analyze" && stage === "done" && !surveyDraft && (
              <button type="button" className="btn btn-on-dark topbar-cta" onClick={open}>
                Analyze another video
              </button>
            )}
            <nav className="topnav" aria-label="Main">
              {allowedPages.includes("home") && (
                <button
                  type="button"
                  className={view === "home" ? "is-active" : ""}
                  aria-current={view === "home" ? "page" : undefined}
                  onClick={goHome}
                >
                  Home
                </button>
              )}
              {allowedPages.includes("survey") && (
                <button
                  type="button"
                  className={view === "survey" || view === "analyze" ? "is-active" : ""}
                  aria-current={view === "survey" || view === "analyze" ? "page" : undefined}
                  onClick={goSurvey}
                >
                  <span className="nav-long">Take survey</span>
                  <span className="nav-short" aria-hidden="true">Survey</span>
                </button>
              )}
              {allowedPages.includes("mine") && (
                <button
                  type="button"
                  className={view === "mine" ? "is-active" : ""}
                  aria-current={view === "mine" ? "page" : undefined}
                  onClick={goMine}
                >
                  My surveys
                </button>
              )}
              {allowedPages.includes("history") && (
                <button
                  type="button"
                  className={view === "history" ? "is-active" : ""}
                  aria-current={view === "history" ? "page" : undefined}
                  onClick={goHistory}
                >
                  <span className="nav-long">Survey history</span>
                  <span className="nav-short" aria-hidden="true">History</span>
                  {(analyzedCount ?? 0) > 0 && (
                    <span className="nav-count" aria-label={`${analyzedCount} analyzed surveys`}>
                      {analyzedCount}
                    </span>
                  )}
                </button>
              )}
              {allowedPages.includes("catalog") && (
                <button
                  type="button"
                  className={view === "catalog" ? "is-active" : ""}
                  aria-current={view === "catalog" ? "page" : undefined}
                  onClick={goCatalog}
                >
                  Catalog
                </button>
              )}
              {allowedPages.includes("team") && (
                <button
                  type="button"
                  className={view === "team" ? "is-active" : ""}
                  aria-current={view === "team" ? "page" : undefined}
                  onClick={goTeam}
                >
                  Team
                </button>
              )}
            </nav>
            <NotificationBell
              onOpenHistory={isAdminUser ? goHistory : undefined}
              onOpenSurvey={allowedPages.includes("survey") ? goSurvey : undefined}
              onOpenRecipe={isAdminUser ? (pid) => openCatalogProduct(pid, "recipe") : undefined}
              onOpenRounds={isAdminUser ? (pid) => openCatalogProduct(pid, "rounds") : undefined}
            />
            <UserMenu user={user} />
          </div>
        </div>
      </header>

      {accessNotice && (
        <div className="access-banner" role="alert">
          {accessNotice}
        </div>
      )}

      {!online && (
        <div className="offline-banner" role="status">
          You're offline. You can still record and save; analyzing needs a connection.
        </div>
      )}

      <main className="container" id="main" tabIndex={-1}>
        {/* The survey stays mounted while other tabs are open, so a half-finished
            survey isn't lost when the executive checks history. */}
        <div hidden={view !== "survey"}>
          <ResultsBoundary resetKey="survey" onRetry={goSurvey}>
            <SurveyWorkflow
              key={surveyKey}
              onReady={handleSurveyReady}
              locked={surveySaved}
              initialContext={surveyContext}
              onActivity={setSurveyInProgress}
              active={view === "survey"}
            />
          </ResultsBoundary>
        </div>

        {view === "home" ? (
          <ResultsBoundary resetKey={`home-${homeKey}`} onRetry={goHome}>
            <HomeDashboard
              key={homeKey}
              history={feedbackHistory}
              surveyDraft={surveyDraft}
              surveyInProgress={surveyInProgress}
              onNewSurvey={startNewSurvey}
              onContinueSurvey={continueSurvey}
              onOpenFeedback={openFeedback}
              onViewHistory={() => {
                goHistory();
                setHistoryTab("surveys"); // "View all" means every survey, not grouped by product
              }}
              canSurvey={!isAdminUser}
            />
          </ResultsBoundary>
        ) : view === "catalog" ? (
          <ResultsBoundary resetKey="catalog" onRetry={goCatalog}>
            <CatalogPage openRequest={catalogRequest} onOpenRound={openProductRound} />
          </ResultsBoundary>
        ) : view === "team" ? (
          <ResultsBoundary resetKey="team" onRetry={goTeam}>
            <TeamPage />
          </ResultsBoundary>
        ) : view === "survey" ? null : view === "mine" ? (
          <ResultsBoundary resetKey="my-surveys" onRetry={goMine}>
            <MySurveys user={user} onTakeSurvey={goSurvey} />
          </ResultsBoundary>
        ) : view === "history" ? (
          selectedFeedbackLoading ? (
            <div className="panel history-loading">
              <span className="spinner" aria-hidden="true" />
              <p>Loading feedback details…</p>
            </div>
          ) : selectedFeedback ? (
            <ResultsBoundary resetKey={selectedFeedback.id} onRetry={() => setSelectedFeedback(null)}>
            <FeedbackDetail
              key={selectedFeedback.id}
              record={selectedFeedback}
              onRecordUpdated={(updated) => {
                if (!updated) return;
                setSelectedFeedback((cur) => (cur && cur.id === updated.id ? updated : cur));
                loadFeedbackHistory({ silent: true });
              }}
              onBack={() => {
                setSelectedFeedback(null);
                window.scrollTo({ top: 0 });
              }}
            />
            </ResultsBoundary>
          ) : historyProductId != null ? (
            <ResultsBoundary resetKey={`product-${historyProductId}`} onRetry={goHistory}>
              <ProductFeedbackPage
                key={`${historyProductId}-${historyRound ?? "all"}`}
                productId={historyProductId}
                initialRound={historyRound}
                onBack={() => {
                  setHistoryProductId(null);
                  window.scrollTo({ top: 0 });
                }}
                onOpenFeedback={openFeedback}
              />
            </ResultsBoundary>
          ) : historyMode === "unlinked" ? (
            <section>
              <button type="button" className="back-link" onClick={() => setHistoryMode("products")}>
                <ChevronIcon dir="left" /> Survey history
              </button>
              <HistoryList
                items={unlinked.items}
                loading={unlinked.loading}
                error={unlinked.error}
                onOpen={openFeedback}
                onRefresh={openUnlinked}
                onAnalyze={() => startNewSurvey(null)}
              />
            </section>
          ) : (
            <ResultsBoundary resetKey="product-history" onRetry={goHistory}>
              <ProductHistory
                onOpenProduct={openHistoryProduct}
                onOpenUnlinked={openUnlinked}
                onOpenFeedback={openFeedback}
                tab={historyTab}
                onTabChange={setHistoryTab}
              />
            </ResultsBoundary>
          )
        ) : (
          <>
            {stage !== "done" && (
              <>
                <section className="hero">
                  {surveyDraft ? (
                    <div className="hero-copy survey-context">
                      <p className="eyebrow">Take survey · Step 4 of 4</p>
                      <h1 className="survey-context-title">Record {surveyDraft.respondentName}'s feedback</h1>
                      <p>
                        Ask the customer about their overall experience with <strong>{surveyDraft.productName}</strong>:
                        what they liked, what they didn't, and what they'd change. Keep it between 30 seconds and 35
                        minutes.
                      </p>
                      <dl className="survey-context-facts">
                        <div>
                          <dt>Product</dt>
                          <dd>{surveyDraft.productName}</dd>
                        </div>
                        {surveyDraft.round && (
                          <div>
                            <dt>Version</dt>
                            <dd>{surveyDraft.recipeVersion ? `Recipe v${surveyDraft.recipeVersion} · ` : ""}Round {surveyDraft.round}</dd>
                          </div>
                        )}
                        <div>
                          <dt>Respondent</dt>
                          <dd>{surveyDraft.respondentName}</dd>
                        </div>
                        <div>
                          <dt>Questions</dt>
                          <dd>{surveyDraft.questionCount} answered</dd>
                        </div>
                      </dl>
                      {!busy && (
                        <button type="button" className="btn btn-secondary" onClick={goSurvey}>
                          {surveySaved ? "View survey details" : "Back: edit survey details"}
                        </button>
                      )}
                      {surveySaved && !busy && (
                        <p className="field-hint">The survey is saved (#{surveyDraft.responseId}). Only the recording is left.</p>
                      )}
                    </div>
                  ) : (
                  <div className="hero-copy">
                    <h1>Hear what customers really think about your product.</h1>
                    <p>
                      Upload a customer feedback video or audio file, or record one right here. You'll get a
                      timestamped transcript, what customers praised or criticized, and recommended next steps.
                    </p>
                    <ul className="hero-points">
                      <li>
                        <span className="point-icon"><CheckIcon /></span>
                        Praise and issues flagged across the whole video
                      </li>
                      <li>
                        <span className="point-icon"><CheckIcon /></span>
                        Record voice or video on the spot, even without a connection
                      </li>
                      <li>
                        <span className="point-icon"><CheckIcon /></span>
                        Every analysis is saved to survey history
                      </li>
                    </ul>
                  </div>
                  )}

                  <div className="hero-panel">
                    {busy ? (
                      <div className="progress-card" aria-live="polite">
                        <div className="progress-file">
                          <span className="file-icon" aria-hidden="true">▶</span>
                          <div>
                            <p className="progress-name">{file?.name}</p>
                            <p className="progress-size">{formatSize(file?.size)}</p>
                          </div>
                        </div>

                        <ol className="steps">
                          <li className={`step step-${uploadStep}`}>
                            <span className="step-marker" />
                            <div className="step-body">
                              <p className="step-label">{isAudioFile(file) ? "Uploading recording" : "Uploading video"}</p>
                              {stage === "uploading" && (
                                <div className="progress-bar">
                                  <span style={{ width: `${progress}%` }} />
                                </div>
                              )}
                              <p className="step-detail">{stage === "uploading" ? `${progress}% uploaded` : "Uploaded"}</p>
                            </div>
                          </li>
                          <li className={`step step-${analyzeStep}`}>
                            <span className="step-marker" />
                            <div className="step-body">
                              <p className="step-label">
                                {job?.status === "queued" ? "Waiting in line" : "Transcribing and summarizing"}
                              </p>
                              <p className="step-detail">
                                {job?.status === "queued"
                                  ? `Position ${job.position} in the queue. It starts automatically.`
                                  : job?.status === "processing"
                                  ? `${formatDuration(job.elapsed || 0)} so far. Longer recordings take a few minutes.`
                                  : "Longer videos can take a few minutes."}
                              </p>
                              {stage === "processing" && (
                                <p className="step-detail step-hint">
                                  You can use other pages meanwhile. This page shows the results when they're ready.
                                </p>
                              )}
                            </div>
                          </li>
                          <li className="step step-pending">
                            <span className="step-marker" />
                            <div className="step-body">
                              <p className="step-label">Results ready</p>
                            </div>
                          </li>
                        </ol>
                      </div>
                    ) : (
                      <>
                      <div className="input-tabs" role="tablist" aria-label="How to add feedback">
                        <button
                          type="button"
                          role="tab"
                          aria-selected={inputMode === "upload"}
                          className={inputMode === "upload" ? "is-on" : ""}
                          onClick={() => setInputMode("upload")}
                        >
                          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 16V4m0 0l-4.5 4.5M12 4l4.5 4.5M4 16v2.5A1.5 1.5 0 005.5 20h13a1.5 1.5 0 001.5-1.5V16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
                          Upload a file
                        </button>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={inputMode === "record"}
                          className={inputMode === "record" ? "is-on" : ""}
                          onClick={() => setInputMode("record")}
                        >
                          <span className="tab-rec-dot" aria-hidden="true" />
                          Record now
                        </button>
                      </div>

                      {inputMode === "record" ? (
                        <Recorder
                          online={online}
                          onAnalyze={(f) => analyzeFile(f, { fromRecorder: true })}
                          onSave={handleSaveRecording}
                        />
                      ) : (
                      <div {...getRootProps({ className: `dropzone${isDragActive ? " is-dragging" : ""}` })}>
                        <div className="dropzone-icon" aria-hidden="true">
                          <svg viewBox="0 0 24 24" width="24" height="24">
                            <path
                              d="M12 16V4m0 0l-4.5 4.5M12 4l4.5 4.5M4 16v2.5A1.5 1.5 0 005.5 20h13a1.5 1.5 0 001.5-1.5V16"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.8"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </div>
                        <p className="dropzone-title">{isDragActive ? "Drop the video to start" : "Drag a video here"}</p>
                        <p className="dropzone-sub">MP4, MOV, AVI, MKV, WebM, MP3, WAV or M4A, from 30 seconds to 35 minutes</p>
                        <span className="btn btn-primary">Choose a file</span>
                      </div>
                      )}
                      </>
                    )}

                    {fileRejections.length > 0 && !busy && (
                      <p className="alert alert-error">That file type isn't supported. Upload a video or audio file.</p>
                    )}
                    {error && (
                      <div className="alert alert-error alert-row">
                        <span>{error}</span>
                        {failedFile && (
                          <button type="button" className="btn btn-secondary" onClick={saveFailedFile}>
                            Save recording for later
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </section>

                {!busy && (
                  <SavedRecordings
                    items={savedRecordings}
                    online={online}
                    busy={busy}
                    onAnalyze={analyzeSaved}
                    onDelete={handleDeleteSaved}
                  />
                )}

                {!busy && !surveyDraft && <RecentAnalyses items={feedbackHistory} onOpen={openFeedback} onViewAll={goHistory} />}
              </>
            )}

            {stage === "done" && data && (
              <ResultsBoundary
                resetKey={data}
                onOpenHistory={() => (savedId != null ? openFeedback(savedId) : goHistory())}
                onRetry={() => {
                  setStage("idle");
                  setData(null);
                }}
              >
              <section className="results">
                {surveyDraft && (
                  <div className="survey-done" role="status">
                    <div className="survey-done-text">
                      <strong>Survey #{surveyDraft.responseId} complete</strong>
                      <span>
                        {surveyDraft.productName} · {surveyDraft.respondentName} · details, answers and this analysis are
                        saved together
                      </span>
                    </div>
                    <div className="survey-done-actions">
                      <button type="button" className="btn btn-secondary" onClick={goHome}>
                        Back to home
                      </button>
                      <button type="button" className="btn btn-secondary" onClick={() => startNextSurvey(false)}>
                        New survey
                      </button>
                      <button type="button" className="btn btn-primary" onClick={() => startNextSurvey(true)}>
                        Next respondent, same product
                      </button>
                    </div>
                  </div>
                )}
                <div className="results-head">
                  <div className="results-title">
                    <h1 className="file-name">{file?.name || data.filename}</h1>
                    <div className="file-meta">
                      {duration > 0 && <span>{formatDuration(duration)} long</span>}
                      <span>{plural(segments.length, "transcript segment")}</span>
                      {file && <span>{formatSize(file.size)}</span>}
                    </div>
                  </div>
                  <div className="results-actions">
                    <SentimentPill sentiment={insights.sentiment} />
                    <button type="button" className="btn btn-secondary" onClick={copySummary}>
                      {copied ? "Summary copied" : "Copy summary"}
                    </button>
                    <button type="button" className="btn btn-secondary" onClick={downloadReport}>
                      Download PDF report
                    </button>
                    {savedId != null && (
                      <button type="button" className="btn btn-secondary" onClick={() => openFeedback(savedId)}>
                        View in history
                      </button>
                    )}
                  </div>
                </div>

                <OverallRatingCard rating={overallRating} areas={aspectRatings.length} />

                {/* ===== How the video was analyzed ===== */}
                <div className="processing-panel panel">
                  <div className="processing-header">
                    <h2 className="panel-title">How the video was analyzed</h2>
                    <span className="processing-badge">Saved to survey history</span>
                  </div>

                  <div className="processing-grid">
                    <div className="processing-item">
                      <span>Video length</span>
                      <strong>{duration > 0 ? formatDuration(duration) : data.video_duration || "—"}</strong>
                    </div>
                    <div className="processing-item">
                      <span>Time to analyze</span>
                      <strong>{data.processing_time_seconds != null ? formatDuration(data.processing_time_seconds) : "—"}</strong>
                    </div>
                    <div className="processing-item">
                      <span>Parts analyzed</span>
                      <strong>{plural(partsCount, "part")}</strong>
                    </div>
                  </div>

                  <p className="processing-note">
                    The video was split into {plural(partsCount, "part")} and each part was analyzed on its own. Select a
                    part to see what was said in it.
                  </p>

                  {chapters.length > 0 && (
                    <div className="chunk-visual">
                      {chapters.map((part) => (
                        <button key={part.part} type="button" className="chunk-chip" onClick={() => setSelectedPart(part)}>
                          <i className={`dot dot-${partTone(part)}`} />
                          Part {part.part}
                          <span className="chunk-time">
                            {formatTime(part.start)}–{formatTime(partEnd(part))}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="results-grid">
                  <div className="col-main">
                    <article className="panel video-panel">
                      {videoUrl && !videoError && isAudioFile(file) ? (
                        <div className="audio-frame">
                          <span className="audio-frame-icon" aria-hidden="true">
                            <svg viewBox="0 0 24 24" width="24" height="24"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
                          </span>
                          <audio
                            ref={videoRef}
                            src={videoUrl}
                            controls
                            preload="metadata"
                            onLoadedMetadata={(e) => setVideoDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
                            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                            onPlay={() => setIsPlaying(true)}
                            onPause={() => setIsPlaying(false)}
                            onError={() => setVideoError(true)}
                          />
                        </div>
                      ) : videoUrl && !videoError ? (
                        <div className="video-frame">
                          <video
                            ref={videoRef}
                            src={videoUrl}
                            controls
                            preload="metadata"
                            onLoadedMetadata={(e) => setVideoDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
                            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                            onPlay={() => setIsPlaying(true)}
                            onPause={() => setIsPlaying(false)}
                            onError={() => setVideoError(true)}
                          />
                        </div>
                      ) : (
                        <div className="video-fallback">
                          <p>This browser can't preview this video format.</p>
                          <p className="muted">The transcript and summary are still complete. MP4 files play here.</p>
                        </div>
                      )}
                      <MomentMap segments={segments} duration={duration} currentTime={currentTime} onSeek={seekTo} />
                    </article>

                    <article className="panel transcript-panel">
                      <div className="transcript-head">
                        <h2 className="panel-title">Transcript</h2>
                        <div className="transcript-tools">
                          <input
                            type="search"
                            className="search"
                            placeholder="Search the transcript"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            aria-label="Search the transcript"
                          />
                          <div className="segmented">
                            {[
                              ["all", "All", segments.length],
                              ["positive", "Praise", counts.positive],
                              ["negative", "Issues", counts.negative],
                            ].map(([key, label, n]) => (
                              <button
                                key={key}
                                type="button"
                                aria-pressed={filter === key}
                                className={filter === key ? "is-on" : ""}
                                onClick={() => setFilter(key)}
                              >
                                {label} <span className="seg-count">{n}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>

                      {visibleSegments.length === 0 ? (
                        <p className="empty">
                          {segments.length === 0
                            ? "No speech was detected in this video."
                            : "No lines match your search. Try a different word or switch to All."}
                        </p>
                      ) : (
                        <ol ref={listRef} className="transcript-list">
                          {visibleSegments.map((s) => (
                            <li
                              key={s.index}
                              ref={(el) => (segmentRefs.current[s.index] = el)}
                              className={`segment tone-${s.tone}${s.index === activeIndex ? " is-active" : ""}`}
                            >
                              <button type="button" className="segment-btn" onClick={() => seekTo(s.start)}>
                                <time className="time">{formatTime(s.start)}</time>
                                <span className="segment-text">
                                  <Highlight text={s.text} query={query} />
                                </span>
                                {s.tone !== "neutral" && (
                                  <span className={`tag tag-${s.tone}`}>{s.tone === "positive" ? "Praise" : "Issue"}</span>
                                )}
                              </button>
                            </li>
                          ))}
                        </ol>
                      )}
                    </article>
                  </div>

                  <aside className="col-side">
                    <InsightsPanel insights={insights} counts={counts} hasSummary={Boolean(data.summary)} />
                    <AspectRatingsPanel ratings={aspectRatings} />
                  </aside>
                </div>
              </section>
              </ResultsBoundary>
            )}
          </>
        )}

        {selectedPart && (
          <div className="chapter-modal-overlay" onClick={() => setSelectedPart(null)}>
            <TrapFocus
              className="chapter-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="chapter-title"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="chapter-top">
                <div className="chapter-heading">
                  <h2 id="chapter-title">Part {selectedPart.part}</h2>
                  <p>
                    {formatTime(selectedPart.start)} – {formatTime(partEnd(selectedPart))}
                  </p>
                </div>

                <span className={`sentiment-badge ${selectedLabel.toLowerCase()}`}>{selectedLabel}</span>

                <button type="button" className="chapter-close" onClick={() => setSelectedPart(null)} aria-label="Close">
                  <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
                    <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </button>
              </div>

              <div className="chapter-body">
                {selectedPart.summary && (
                  <div className="chapter-section">
                    <h3>What happened</h3>
                    <p>{selectedPart.summary}</p>
                  </div>
                )}

                {selectedPart.key_point && (
                  <div className="chapter-section chapter-takeaway">
                    <h3>Key takeaway</h3>
                    <p>{selectedPart.key_point}</p>
                  </div>
                )}

                <FeedbackRatingList feedback={selectedPart.feedback || []} title="Customer feedback" />

                {selectedPart.transcript && (
                  <div className="chapter-section">
                    <h3>Transcript</h3>
                    <p className="chapter-transcript">{selectedPart.transcript}</p>
                  </div>
                )}
              </div>

              <div className="chapter-foot">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    seekTo(Number(selectedPart.start) || 0);
                    setSelectedPart(null);
                  }}
                >
                  Play this part
                </button>
              </div>
            </TrapFocus>
          </div>
        )}
      </main>
    </div>
  );
}

// Last line of defence: an error anywhere shows a message instead of an empty page.
export default function App() {
  const [session, setSession] = useState(getSession);
  useEffect(() => onSessionChange(setSession), []);
  // Latest role and permissions from the server whenever the app opens, and every
  // 30 minutes while it stays open (this also renews the sign-in before it expires).
  const token = session?.token;
  useEffect(() => {
    if (!token) return undefined;
    refreshMe(API_BASE_URL);
    const timer = setInterval(() => refreshMe(API_BASE_URL), 30 * 60 * 1000);
    return () => clearInterval(timer);
  }, [token]);

  if (!session) return <LoginPage apiBase={API_BASE_URL} onSignedIn={() => {}} />;
  // The organization (users.role = "client") has its own portal: components/OrgPortal.jsx.
  if (roleOf(session.user) === "client") {
    return (
      <ResultsBoundary resetKey="org-root" onRetry={() => window.location.reload()}>
        <OrgPortal key={session.user.id} user={session.user} ReportView={ConsolidatedReportView} />
      </ResultsBoundary>
    );
  }
  return (
    <ResultsBoundary resetKey="root" onRetry={() => window.location.reload()}>
      {/* A different user gets a fresh app (no leftover pages or drafts). */}
      <AppContent key={session.user.id} user={session.user} />
    </ResultsBoundary>
  );
}