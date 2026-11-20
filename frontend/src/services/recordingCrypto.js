import axios from "axios";
import { API_BASE_URL } from "../config";

/*
  Encrypted recordings in the browser (the same format and keys as the phone app's
  services/recording_vault.dart and the server's app/services/recording_crypto.py).

  A recording is sealed BEFORE it leaves memory (upload, or "Save for later" in IndexedDB):
    - a fresh AES-256-GCM data key (WebCrypto, from the browser's secure random source);
    - the recording encrypted in 1 MiB chunks (FMCGREC1);
    - the data key wrapped with the server's RSA-OAEP-SHA256 PUBLIC key, then dropped.
  The browser keeps only ciphertext it can't open. Nothing here plays a recording: an
  authorised Admin plays it after the product passkey (components/ProtectedRecording.jsx).

  WebCrypto needs a secure page (https, or localhost). On a plain-http page the recording is
  uploaded as it is (over the connection) and the server encrypts it at rest at once; saving
  it on the device is then not offered.
*/

const api = axios.create({ baseURL: API_BASE_URL });
const KEY_CACHE = "fmcg.recordingKey"; // the PUBLIC key only (not secret), for offline use
const MAGIC = new TextEncoder().encode("FMCGREC1");
export const CHUNK = 1024 * 1024;

export const canSeal = () => typeof window !== "undefined" && Boolean(window.crypto?.subtle) && window.isSecureContext !== false;

function cached() {
  try {
    const k = JSON.parse(localStorage.getItem(KEY_CACHE) || "null");
    return k && typeof k.key_id === "number" && typeof k.public_key_spki_b64 === "string" ? k : null;
  } catch {
    return null;
  }
}

/** The server's public key: fetched (and remembered for offline use), else the remembered one. */
export async function recordingKey() {
  try {
    const { data } = await api.get("/recordings/public-key");
    try {
      localStorage.setItem(KEY_CACHE, JSON.stringify({ key_id: data.key_id, public_key_spki_b64: data.public_key_spki_b64 }));
    } catch {
      /* storage unavailable: use it for this session only */
    }
    return data;
  } catch (err) {
    const k = cached();
    if (k) return k;
    throw Object.assign(new Error("This device has no recording security key yet. Connect to the internet once, then try again."), { cause: err });
  }
}

const b64ToBytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
const bytesToB64 = (bytes) => {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};

function aad(index, last) {
  const out = new Uint8Array(MAGIC.length + 5);
  out.set(MAGIC);
  new DataView(out.buffer).setUint32(MAGIC.length, index);
  out[MAGIC.length + 4] = last ? 1 : 0;
  return out;
}

/**
 * Encrypts a recording (Blob / File). Returns { file, keyId, wrappedKey }: `file` is the
 * ciphertext (same name, so the server knows the format); the browser can't decrypt it.
 */
export async function sealRecording(blob, name) {
  if (!canSeal()) throw new Error("This page isn't secure (https), so recordings can't be encrypted here.");
  const pk = await recordingKey();
  const subtle = window.crypto.subtle;
  const dataKey = await subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt"]);
  const parts = [MAGIC];
  const count = Math.max(1, Math.ceil(blob.size / CHUNK));
  for (let i = 0; i < count; i += 1) {
    const plain = new Uint8Array(await blob.slice(i * CHUNK, (i + 1) * CHUNK).arrayBuffer());
    const nonce = window.crypto.getRandomValues(new Uint8Array(12));
    const sealed = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: aad(i, i === count - 1), tagLength: 128 },
      dataKey, plain));
    const head = new Uint8Array(4);
    new DataView(head.buffer).setUint32(0, 12 + sealed.length);
    parts.push(head, nonce, sealed);
  }
  const raw = new Uint8Array(await subtle.exportKey("raw", dataKey));
  const publicKey = await subtle.importKey("spki", b64ToBytes(pk.public_key_spki_b64), { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]);
  const wrapped = new Uint8Array(await subtle.encrypt({ name: "RSA-OAEP" }, publicKey, raw));
  raw.fill(0); // the data key is forgotten
  const fileName = name || blob.name || "recording.webm";
  return {
    file: new File(parts, fileName, { type: "application/octet-stream" }),
    keyId: pk.key_id,
    wrappedKey: bytesToB64(wrapped),
  };
}

/** Form fields that go with an encrypted upload. */
export function envelopeFields(form, sealed) {
  if (sealed?.wrappedKey) {
    form.append("key_id", String(sealed.keyId));
    form.append("wrapped_key", sealed.wrappedKey);
  }
  return form;
}

/* ---------- Recording passkeys (Super Admin) ---------- */
const PASSKEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no 0/O, 1/I: easy to read out

/** A strong passkey from the browser's secure random source, e.g. K7QM-9XPD-2RTF-H4WB (~80 bits). */
export function generatePasskey() {
  const out = [];
  const bytes = window.crypto.getRandomValues(new Uint8Array(64));
  for (const b of bytes) {
    if (b < 256 - (256 % PASSKEY_ALPHABET.length)) out.push(PASSKEY_ALPHABET[b % PASSKEY_ALPHABET.length]); // no modulo bias
    if (out.length === 16) break;
  }
  return out.join("").replace(/(.{4})(?=.)/g, "$1-");
}

export const passkeyProblem = (value) =>
  (value || "").replace(/[\s-]/g, "").length < 8 ? "The recording passkey needs at least 8 characters." : "";
