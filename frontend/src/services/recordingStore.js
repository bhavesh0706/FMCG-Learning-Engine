// Recordings saved on this device, waiting to be analyzed.
// Stored in IndexedDB, so they survive a page reload or closing the browser,
// and need no network connection.

const DB_NAME = "fmcg-video-learning";
const STORE = "recordings";
const VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    if (!("indexedDB" in window)) {
      reject(new Error("This browser can't save recordings on the device."));
      return;
    }
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function run(mode, work) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const request = work(tx.objectStore(STORE));
    tx.oncomplete = () => {
      db.close();
      resolve(request?.result);
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error);
    };
    tx.onabort = () => {
      db.close();
      reject(tx.error || new Error("Saving was cancelled. The device may be out of storage."));
    };
  });
}

// recording = { name, blob, mimeType, kind: "audio" | "video", duration (seconds) }
export async function saveRecording(recording) {
  const item = {
    id: `rec-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    createdAt: new Date().toISOString(),
    size: recording.blob.size,
    ...recording,
  };
  await run("readwrite", (store) => store.put(item));
  return item;
}

export async function listRecordings() {
  const items = (await run("readonly", (store) => store.getAll())) || [];
  return items.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
}

export async function deleteRecording(id) {
  await run("readwrite", (store) => store.delete(id));
}

// Asks the browser not to clear saved recordings when the device is low on space.
export async function requestPersistentStorage() {
  try {
    if (navigator.storage?.persisted && (await navigator.storage.persisted())) return true;
    return (await navigator.storage?.persist?.()) || false;
  } catch {
    return false;
  }
}

export function recordingToFile(item) {
  return new File([item.blob], item.name, { type: item.mimeType || item.blob.type });
}