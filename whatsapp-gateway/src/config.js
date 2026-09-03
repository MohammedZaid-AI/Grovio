/**
 * Configuration. Environment only — nothing secret has a working default that
 * could ship by accident.
 */
import fs from 'node:fs';
import path from 'node:path';

/** Minimal .env reader. A dependency for `KEY=value` would be silly. */
export function loadEnv(file = '.env') {
  const full = path.resolve(file);
  if (!fs.existsSync(full)) return;
  for (const line of fs.readFileSync(full, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq < 1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

export function config() {
  return {
    port: Number(process.env.GATEWAY_PORT || 8100),
    // The shared secret, both directions. FAILS CLOSED: with no secret the
    // gateway refuses to start rather than expose /send to whoever can reach
    // the port.
    secret: process.env.WHATSAPP_GATEWAY_SECRET || '',
    backendUrl: (process.env.BACKEND_URL || 'http://localhost:8000').replace(/\/+$/, ''),
    inboundPath: process.env.BACKEND_INBOUND_PATH || '/webhook/inbound',
    authDir: process.env.AUTH_DIR || 'auth',
    // How long to wait on the backend, and how many extra attempts to make.
    backendTimeoutMs: Number(process.env.BACKEND_TIMEOUT_MS || 30000),
    backendRetries: Number(process.env.BACKEND_RETRIES || 4),
    // Remembered WhatsApp message ids, for deduplication.
    dedupeSize: Number(process.env.DEDUPE_SIZE || 2000),
  };
}

export function assertConfigured(cfg) {
  if (!cfg.secret) {
    throw new Error(
      'WHATSAPP_GATEWAY_SECRET is not set. It is the only thing standing '
      + 'between /send and anyone who can reach this port, so the gateway will '
      + 'not start without it. See .env.example.',
    );
  }
}
