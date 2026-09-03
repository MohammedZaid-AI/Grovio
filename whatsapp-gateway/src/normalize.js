/**
 * Turning a Baileys message into the one shape the backend understands.
 *
 * Everything here is PURE — no socket, no network, no clock. That is
 * deliberate: this is where the loops and the wrong-recipient bugs live, so it
 * has to be testable without WhatsApp.
 *
 * The backend never sees a Baileys object. It gets:
 *   { message_id, phone, text, timestamp, type }
 */

export const DIRECT_SERVER = 's.whatsapp.net';
export const LID_SERVER = 'lid';

/**
 * The digits of a JID: "919876543210@s.whatsapp.net" -> "919876543210".
 * Strips the device suffix WhatsApp appends on multi-device (":12").
 */
function digitsOf(jid) {
  if (!jid || typeof jid !== 'string') return '';
  const user = jid.split('@')[0].split(':')[0];
  return user.replace(/\D/g, '');
}

export function serverOf(jid) {
  return typeof jid === 'string' && jid.includes('@') ? jid.split('@')[1] : '';
}

export function isLid(jid) {
  return serverOf(jid) === LID_SERVER;
}

/**
 * The stable identity for a human: their phone number, as bare digits — the
 * same format the backend already stores for every user.
 *
 * A @lid is a PRIVACY ID, not a phone number. Keying a user by one gives that
 * same person a second identity with no memory, no order history and no linked
 * account — and they stop matching the ordering allowlist, so they silently
 * lose the ability to order. When the JID is a @lid the real number is in
 * `senderPn` / `participantPn`; with neither, return "" and let the caller drop
 * the message rather than invent a user.
 */
export function resolvePhone(key = {}, message = {}) {
  const candidates = [
    key.senderPn,
    message.senderPn,
    key.participantPn,
    message.participantPn,
  ];
  for (const candidate of candidates) {
    const digits = digitsOf(candidate);
    if (digits) return digits;
  }

  const jid = key.participant || key.remoteJid;
  if (isLid(jid)) return '';           // a privacy id is not an identity
  return digitsOf(jid);
}

/** The text a person actually typed, wherever this message kind puts it. */
export function textOf(message) {
  if (!message || typeof message !== 'object') return '';
  if (typeof message.conversation === 'string' && message.conversation) {
    return message.conversation;
  }
  const extended = message.extendedTextMessage;
  if (extended && typeof extended.text === 'string' && extended.text) {
    return extended.text;
  }
  // A button or list reply: the chosen title IS what they said.
  for (const field of ['buttonsResponseMessage', 'listResponseMessage',
    'templateButtonReplyMessage']) {
    const reply = message[field];
    if (!reply) continue;
    const value = reply.selectedDisplayText || reply.title
      || reply.selectedButtonId || reply.selectedRowId;
    if (value) return String(value);
  }
  // A caption on media is still the user speaking.
  for (const field of ['imageMessage', 'videoMessage', 'documentMessage']) {
    const media = message[field];
    if (media && typeof media.caption === 'string' && media.caption) {
      return media.caption;
    }
  }
  return '';
}

/**
 * Media a PERSON sent. Anything not listed is WhatsApp's own plumbing.
 *
 * Every ordinary text also arrives alongside bodyless events —
 * senderKeyDistributionMessage, messageContextInfo, protocolMessage. Reading
 * "no text" as "must be an attachment" answers every one of them, so someone
 * saying "hi" gets an attachment reply to every message they send.
 */
export const MEDIA_KINDS = {
  imageMessage: 'image',
  videoMessage: 'video',
  audioMessage: 'audio',
  documentMessage: 'document',
  stickerMessage: 'sticker',
  locationMessage: 'location',
  liveLocationMessage: 'location',
  contactMessage: 'contact',
  contactsArrayMessage: 'contact',
  ptvMessage: 'video',
};

export function mediaKind(message) {
  if (!message || typeof message !== 'object') return null;
  for (const [field, kind] of Object.entries(MEDIA_KINDS)) {
    if (message[field]) return kind;
  }
  return null;
}

/** WhatsApp wraps some messages; unwrap to the real content. */
export function unwrap(message) {
  let current = message;
  for (let depth = 0; depth < 5 && current; depth += 1) {
    const inner = current.ephemeralMessage?.message
      || current.viewOnceMessage?.message
      || current.viewOnceMessageV2?.message
      || current.viewOnceMessageV2Extension?.message
      || current.documentWithCaptionMessage?.message;
    if (!inner) return current;
    current = inner;
  }
  return current;
}

/**
 * One Baileys message -> the backend's inbound shape, or null to ignore it.
 *
 * Returns null for everything the backend must not answer:
 *   - OUR OWN outgoing messages (key.fromMe) — this is the infinite loop
 *   - status/broadcast posts, groups and newsletters
 *   - protocol plumbing with no text and no media
 *   - anything that cannot be resolved to a real phone number
 *   - anything with no message id, which could not be deduplicated
 */
export function normalizeMessage(raw) {
  const key = raw?.key;
  if (!key || typeof key !== 'object') return null;

  // THE LOOP GUARD. Baileys delivers our own sends back through
  // messages.upsert. Answering them puts the assistant in a conversation with
  // itself, forever, spending real money on every lap.
  if (key.fromMe) return null;

  const remote = key.remoteJid || '';
  if (!remote) return null;
  if (remote === 'status@broadcast' || remote.endsWith('@broadcast')) return null;
  if (remote.endsWith('@g.us') || remote.endsWith('@newsletter')) return null;

  const message = unwrap(raw.message);
  if (!message) return null;

  const phone = resolvePhone(key, message);
  if (!phone) return null;

  const text = textOf(message);
  const media = mediaKind(message);
  if (!text && !media) return null;    // plumbing, not something a person sent

  const messageId = String(key.id || '');
  if (!messageId) return null;

  return {
    message_id: messageId,
    phone,
    text,
    timestamp: String(raw.messageTimestamp ?? ''),
    type: text ? 'text' : media,
    // The JID this conversation actually lives on. Kept OUT of the backend
    // payload: rebuilding <phone>@s.whatsapp.net is right for an ordinary chat
    // and WRONG for a @lid-addressed one, where it sends to nobody.
    jid: remote,
  };
}
