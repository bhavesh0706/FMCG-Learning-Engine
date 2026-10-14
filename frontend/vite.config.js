import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
//
// Settings come from frontend/.env.local (development) or .env.<mode>.local
// (e.g. `vite build --mode staging`). See .env.example.
//   VITE_PORT          port of the dev server (npm run dev)
//   VITE_API_BASE_URL  address of the backend API used by the browser

function readPort(env) {
  const raw = (env.VITE_PORT || '5173').trim()
  const port = Number(raw)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`VITE_PORT must be a number between 1 and 65535, got "${raw}".`)
  }
  return port
}

function checkApiBaseUrl(env, mode) {
  const value = (env.VITE_API_BASE_URL || '').trim()
  if (!value) {
    throw new Error(
      `VITE_API_BASE_URL is not set for mode "${mode}". ` +
        'Add it to frontend/.env.local (development) or .env.<mode>.local.',
    )
  }
  let url
  try {
    url = new URL(value)
  } catch {
    throw new Error(`VITE_API_BASE_URL must be a full URL like http://127.0.0.1:8000, got "${value}".`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`VITE_API_BASE_URL must start with http:// or https://, got "${value}".`)
  }
}

export default defineConfig(({ mode }) => {
  // '' = read every variable, not only VITE_*, so the check sees the same
  // values Vite will use. Only VITE_* variables are ever sent to the browser.
  const env = loadEnv(mode, process.cwd(), '')
  checkApiBaseUrl(env, mode)

  return {
    plugins: [react()],
    server: {
      port: readPort(env),
      // Stop with an error if the port is taken, instead of silently using the
      // next free one (which the backend's CORS_ORIGINS would not allow).
      strictPort: true,
    },
  }
})
