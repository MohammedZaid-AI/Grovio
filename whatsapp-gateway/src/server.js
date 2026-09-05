/**
 * The gateway's HTTP face: POST /send and GET /health.
 *
 * Node's own http module. A framework for two routes would be a dependency for
 * nothing.
 */
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';

// A synthesized voice note is base64, so the ceiling is generous. Still
// bounded: an unbounded body is a way to exhaust this process's memory.
const MAX_BODY_BYTES = 12 * 1024 * 1024;
// WhatsApp's own per-message ceiling.
const MAX_TEXT_LENGTH = 4096;

/** Constant-time compare. A fast reject leaks the secret byte by byte. */
export function secretMatches(provided, expected) {
  if (!provided || !expected) return false;
  const a = Buffer.from(String(provided));
  const b = Buffer.from(String(expected));
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/**
 * Validate a /send body. Returns {ok, error} — never throws, and never trusts
 * what arrived.
 */
export function validateSend(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'body must be a JSON object' };
  }
  const phone = String(body.phone ?? '').replace(/\D/g, '');
  if (!phone) return { ok: false, error: 'phone is required and must contain digits' };

  // A voice note. Same route, same auth, same recipient rules — only the
  // payload differs, so the backend needs no second endpoint to learn about.
  if (body.audio !== undefined) {
    if (typeof body.audio !== 'string' || !body.audio) {
      return { ok: false, error: 'audio must be a non-empty base64 string' };
    }
    return {
      ok: true, phone, audio: body.audio,
      mimetype: typeof body.mimetype === 'string' ? body.mimetype : null,
    };
  }

  const text = body.text;
  if (typeof text !== 'string' || !text.trim()) {
    return { ok: false, error: 'text is required and must be a non-empty string' };
  }
  if (text.length > MAX_TEXT_LENGTH) {
    return { ok: false, error: `text exceeds the WhatsApp limit of ${MAX_TEXT_LENGTH} characters` };
  }
  return { ok: true, phone, text };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

export function createServer(gateway, cfg, { log = console.log } = {}) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');

    // Deliberately unauthenticated, and deliberately says nothing but the
    // connection state — no session detail, no phone number, no secret.
    if (req.method === 'GET' && url.pathname === '/health') {
      return json(res, 200, {
        status: gateway.connected ? 'connected' : 'disconnected',
        stopped: gateway.stopped,
      });
    }

    if (url.pathname !== '/send') return json(res, 404, { error: 'not found' });
    if (req.method !== 'POST') return json(res, 405, { error: 'method not allowed' });

    // AUTH FIRST, before the body is even parsed. /send puts words in someone's
    // mouth and can be told to message anyone.
    if (!secretMatches(req.headers['x-gateway-secret'], cfg.secret)) {
      log('[gateway] /send rejected: bad or missing X-Gateway-Secret');
      return json(res, 401, { error: 'unauthorized' });
    }

    let parsed;
    try {
      parsed = JSON.parse(await readBody(req));
    } catch {
      return json(res, 400, { error: 'malformed JSON' });
    }

    const valid = validateSend(parsed);
    if (!valid.ok) return json(res, 400, { error: valid.error });

    try {
      const messageId = valid.audio
        ? await gateway.sendAudio(valid.phone, valid.audio, valid.mimetype)
        : await gateway.sendText(valid.phone, valid.text);
      return json(res, 200, {
        status: 'sent', message_id: messageId, phone: valid.phone,
      });
    } catch (error) {
      if (error.code === 'NOT_CONNECTED') {
        // 503 so the backend's own retry treats it as transient, which it is:
        // the session comes back and the queued message goes out.
        return json(res, 503, {
          status: 'failed', error: 'whatsapp_not_connected', phone: valid.phone,
        });
      }
      log(`[gateway] send failed: ${error.message}`);
      return json(res, 502, {
        status: 'failed', error: 'send_failed', phone: valid.phone,
      });
    }
  });
}
