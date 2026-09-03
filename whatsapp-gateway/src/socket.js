/**
 * The Baileys connection: pairing, reconnect, inbound fan-out, outbound send.
 *
 * This is the ONLY file that imports Baileys. Everything else — normalization,
 * deduplication, delivery, the HTTP server — works on plain objects and is
 * tested without opening a socket.
 */
import path from 'node:path';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';

import { createDedupe, deliverInbound } from './backend.js';
import { normalizeMessage } from './normalize.js';

const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 60000;

/**
 * Should we dial again after a close, and why not if not?
 *
 * A logout is the ONE case where reconnecting is pointless: the credentials in
 * the auth directory are dead and only a fresh QR scan revives them. Retrying
 * forever would spin a loop that can never succeed. Everything else — a dropped
 * socket, restart-required, a stream error, a flaky network — is worth another
 * attempt.
 */
export function shouldReconnect(statusCode) {
  if (statusCode === DisconnectReason.loggedOut) {
    return { reconnect: false, reason: 'logged out from the phone' };
  }
  return { reconnect: true, reason: `close code ${statusCode ?? 'unknown'}` };
}

export function backoffMs(attempt) {
  return Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(attempt, 5));
}

export function createGateway(cfg, { log = console.log } = {}) {
  const dedupe = createDedupe(cfg.dedupeSize);
  // Where each person's conversation actually lives, so a reply goes back to
  // the same chat rather than a JID rebuilt from a phone number.
  const chats = new Map();

  let sock = null;
  let connecting = false;      // one dial at a time, never a second socket
  let stopped = false;
  let attempts = 0;
  let ready = false;

  async function connect() {
    if (connecting || stopped) return;
    connecting = true;

    try {
      const authDir = path.resolve(cfg.authDir);
      // Creates the directory if it does not exist, so a first run needs no
      // setup beyond `npm start`.
      const { state, saveCreds } = await useMultiFileAuthState(authDir);
      const { version } = await fetchLatestBaileysVersion();
      log(`[gateway] starting Baileys ${version.join('.')} (auth: ${authDir})`);

      sock = makeWASocket({
        version,
        auth: state,
        // Baileys can print the QR itself; we render it ourselves so the QR
        // string is never written through the logger to a file.
        printQRInTerminal: false,
        markOnlineOnConnect: false,
        syncFullHistory: false,
        // Baileys wants a logger, and its default prints message contents and
        // key material at debug. Silence it.
        logger: quietLogger(),
      });

      sock.ev.on('creds.update', saveCreds);
      sock.ev.on('connection.update', onConnectionUpdate);
      sock.ev.on('messages.upsert', onMessages);
    } catch (error) {
      log(`[gateway] could not start: ${error.message}`);
      connecting = false;
      scheduleReconnect();
      return;
    }
    connecting = false;
  }

  function onConnectionUpdate(update) {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      // Rendered to the terminal ONLY. Never logged: the QR is a credential —
      // anyone who photographs it owns the session.
      log('[gateway] scan this with WhatsApp > Settings > Linked devices');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'open') {
      ready = true;
      attempts = 0;
      log('[gateway] connected to WhatsApp');
      return;
    }

    if (connection === 'close') {
      ready = false;
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const { reconnect, reason } = shouldReconnect(statusCode);
      if (!reconnect) {
        stopped = true;
        log(`[gateway] STOPPED - ${reason}. The saved session is dead; delete `
          + `${cfg.authDir}/ and restart to scan a fresh QR.`);
        return;
      }
      log(`[gateway] disconnected (${reason})`);
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    if (stopped) return;
    const delay = backoffMs(attempts);
    attempts += 1;
    log(`[gateway] reconnecting in ${Math.round(delay / 1000)}s`);
    setTimeout(connect, delay);
  }

  async function onMessages(batch) {
    for (const raw of batch?.messages ?? []) {
      const message = normalizeMessage(raw);
      if (!message) continue;               // fromMe, group, plumbing, no id

      // WhatsApp redelivers. The id is WhatsApp's own — the same key the
      // backend queue deduplicates on, so a slip here is caught there too.
      if (!dedupe.accept(message.message_id)) {
        log(`[gateway] duplicate ${message.message_id} ignored`);
        continue;
      }

      const { jid, ...forBackend } = message;
      chats.set(message.phone, jid);
      // The id, the sender and the kind. Never the message body.
      log(`[gateway] inbound ${message.message_id} from ${message.phone} `
        + `(${message.type})`);

      const result = await deliverInbound(forBackend, cfg, { log });
      if (!result.ok) {
        log(`[gateway] backend did not accept ${message.message_id} after `
          + `${result.attempts} attempt(s)`);
      }
    }
  }

  function jidFor(phone) {
    const digits = String(phone).replace(/\D/g, '');
    return chats.get(digits) || `${digits}@s.whatsapp.net`;
  }

  async function sendText(phone, text) {
    if (!sock || !ready) {
      const error = new Error('WhatsApp is not connected');
      error.code = 'NOT_CONNECTED';
      throw error;
    }
    const jid = jidFor(phone);
    const sent = await sock.sendMessage(jid, { text });
    const id = sent?.key?.id ? String(sent.key.id) : '';
    log(`[gateway] sent ${id || '(no id)'} to ${phone} (${text.length} chars)`);
    return id;
  }

  return {
    connect,
    sendText,
    get connected() { return ready; },
    get stopped() { return stopped; },
    // For tests and /health only.
    _internals: { dedupe, chats, jidFor, onMessages, onConnectionUpdate },
  };
}

/** Baileys logs message contents and key material at debug. Silence it. */
function quietLogger() {
  const noop = () => {};
  const logger = {
    level: 'silent',
    trace: noop,
    debug: noop,
    info: noop,
    warn: noop,
    error: noop,
    fatal: noop,
  };
  logger.child = () => logger;
  return logger;
}
