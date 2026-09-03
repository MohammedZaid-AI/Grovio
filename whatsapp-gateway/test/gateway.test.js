/**
 * Gateway tests. `npm test` — node --test, no framework, no network.
 *
 * The socket is never opened: normalization, deduplication, delivery, auth and
 * the reconnect decision are all pure, which is exactly why they are the parts
 * worth testing.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { describe } from 'node:test';

import { createDedupe, deliverInbound, isRetryable } from '../src/backend.js';
import { assertConfigured, config } from '../src/config.js';
import {
  mediaKind, normalizeMessage, resolvePhone, unwrap,
} from '../src/normalize.js';
import { createServer, secretMatches, validateSend } from '../src/server.js';
import { backoffMs, shouldReconnect } from '../src/socket.js';

const CFG = {
  port: 0,
  secret: 'test-gateway-secret',
  backendUrl: 'http://backend.test',
  inboundPath: '/webhook/inbound',
  authDir: 'auth',
  backendTimeoutMs: 100,
  backendRetries: 2,
  dedupeSize: 50,
};

const textMessage = (overrides = {}) => ({
  key: {
    remoteJid: '919876543210@s.whatsapp.net',
    fromMe: false,
    id: 'WAMSG-1',
    ...(overrides.key || {}),
  },
  message: overrides.message ?? { conversation: 'I need milk' },
  messageTimestamp: overrides.messageTimestamp ?? 1755000000,
});

// ---------------------------------------------------------------------------
describe('1 & 2 & 3. incoming text, phone extraction, message id', () => {
  test('a plain text message becomes exactly the backend shape', () => {
    const out = normalizeMessage(textMessage());
    assert.deepEqual(out, {
      message_id: 'WAMSG-1',
      phone: '919876543210',
      text: 'I need milk',
      timestamp: '1755000000',
      type: 'text',
      jid: '919876543210@s.whatsapp.net',
    });
  });

  test('the WhatsApp message id is preserved verbatim', () => {
    const out = normalizeMessage(textMessage({ key: { id: '3EB0C767D82B9F41A1BE' } }));
    assert.equal(out.message_id, '3EB0C767D82B9F41A1BE');
  });

  test('the phone is bare digits - the identity the backend already stores', () => {
    assert.equal(normalizeMessage(textMessage()).phone, '919876543210');
  });

  test('the device suffix is stripped', () => {
    const out = normalizeMessage(textMessage({
      key: { remoteJid: '919876543210:12@s.whatsapp.net' },
    }));
    assert.equal(out.phone, '919876543210');
  });

  test('no raw JID reaches the backend payload', () => {
    const { jid, ...forBackend } = normalizeMessage(textMessage());
    assert.ok(jid, 'the gateway keeps it');
    assert.deepEqual(Object.keys(forBackend).sort(),
      ['message_id', 'phone', 'text', 'timestamp', 'type']);
  });

  test('an extendedTextMessage (a reply, or any link) is read', () => {
    assert.equal(normalizeMessage(textMessage({
      message: { extendedTextMessage: { text: 'the second one' } },
    })).text, 'the second one');
  });

  test('a button reply counts as what the user said', () => {
    assert.equal(normalizeMessage(textMessage({
      message: { buttonsResponseMessage: { selectedDisplayText: '1' } },
    })).text, '1');
  });

  test('an ephemeral wrapper is unwrapped', () => {
    assert.equal(normalizeMessage(textMessage({
      message: { ephemeralMessage: { message: { conversation: 'hi' } } },
    })).text, 'hi');
  });

  test('unwrap stops rather than looping on a self-reference', () => {
    const looped = {};
    looped.ephemeralMessage = { message: looped };
    assert.doesNotThrow(() => unwrap(looped));
  });
});

// ---------------------------------------------------------------------------
describe('7. self-messages are ignored - THE loop guard', () => {
  test('our own outgoing message never reaches the backend', () => {
    assert.equal(normalizeMessage(textMessage({ key: { fromMe: true } })), null);
  });

  test('even when it carries perfectly good text', () => {
    assert.equal(normalizeMessage(textMessage({
      key: { fromMe: true, id: 'OUR-REPLY' },
      message: { conversation: '1. Nandini Milk - Rs 26' },
    })), null);
  });

  test('status posts, groups and newsletters are ignored', () => {
    for (const remoteJid of ['status@broadcast', '12345-678@g.us', 'x@newsletter']) {
      assert.equal(normalizeMessage(textMessage({ key: { remoteJid } })), null,
        `${remoteJid} must not reach the backend`);
    }
  });
});

// ---------------------------------------------------------------------------
describe('14. unsupported and malformed message types', () => {
  test('protocol plumbing with no text and no media is dropped silently', () => {
    for (const message of [
      { senderKeyDistributionMessage: {} },
      { messageContextInfo: {} },
      { protocolMessage: {} },
      {},
    ]) {
      assert.equal(normalizeMessage(textMessage({ message })), null,
        `${Object.keys(message)[0] || 'empty'} must not reach the backend`);
    }
  });

  test('a message with no key, or no id, is dropped', () => {
    assert.equal(normalizeMessage({}), null);
    assert.equal(normalizeMessage(null), null);
    assert.equal(normalizeMessage(textMessage({ key: { id: '' } })), null);
  });

  test('a photo is reported honestly as media, not as empty text', () => {
    const out = normalizeMessage(textMessage({
      message: { imageMessage: { mimetype: 'image/jpeg' } },
    }));
    assert.equal(out.type, 'image');
    assert.equal(out.text, '');
  });

  test('a voice note is audio', () => {
    assert.equal(mediaKind({ audioMessage: { ptt: true } }), 'audio');
  });

  test('a caption on a photo is the user speaking, so it is text', () => {
    const out = normalizeMessage(textMessage({
      message: { imageMessage: { caption: 'is this one good?' } },
    }));
    assert.equal(out.type, 'text');
    assert.equal(out.text, 'is this one good?');
  });
});

// ---------------------------------------------------------------------------
describe('JID handling stays inside the gateway', () => {
  test('a @lid alone is NOT an identity - the message is dropped', () => {
    assert.equal(normalizeMessage(textMessage({
      key: { remoteJid: '188888888888888@lid' },
    })), null);
  });

  test('senderPn resolves the real number behind a @lid', () => {
    assert.equal(normalizeMessage(textMessage({
      key: { remoteJid: '188888888888888@lid', senderPn: '919876543210@s.whatsapp.net' },
    })).phone, '919876543210');
  });

  test('senderPn on the message body also resolves', () => {
    assert.equal(normalizeMessage(textMessage({
      key: { remoteJid: '188888888888888@lid' },
      message: { conversation: 'hi', senderPn: '+91 98765 43210' },
    })).phone, '919876543210');
  });

  test('the reply JID stays the @lid chat, not a rebuilt phone JID', () => {
    assert.equal(normalizeMessage(textMessage({
      key: { remoteJid: '188888888888888@lid', senderPn: '919876543210@s.whatsapp.net' },
    })).jid, '188888888888888@lid');
  });

  test('participantPn is read too', () => {
    assert.equal(
      resolvePhone({ remoteJid: '111@lid', participantPn: '919999900000@s.whatsapp.net' }),
      '919999900000',
    );
  });
});

// ---------------------------------------------------------------------------
describe('6. duplicate inbound messages', () => {
  test('the same WhatsApp id is accepted once', () => {
    const dedupe = createDedupe(10);
    assert.equal(dedupe.accept('WAMSG-1'), true);
    assert.equal(dedupe.accept('WAMSG-1'), false);
    assert.equal(dedupe.accept('WAMSG-2'), true);
  });

  test('an empty id is never accepted - it cannot be deduplicated', () => {
    assert.equal(createDedupe(10).accept(''), false);
  });

  test('the window is bounded and forgets oldest first', () => {
    const dedupe = createDedupe(3);
    for (const id of ['a', 'b', 'c', 'd']) dedupe.accept(id);
    assert.equal(dedupe.size, 3);
    assert.equal(dedupe.accept('d'), false, 'd is still remembered');
    assert.equal(dedupe.accept('a'), true, 'a aged out');
  });
});

// ---------------------------------------------------------------------------
describe('12. backend unavailable', () => {
  const noWait = async () => {};

  test('a 200 is delivered on the first attempt', async () => {
    let calls = 0;
    const result = await deliverInbound({ text: 'hi' }, CFG, {
      fetchImpl: async () => { calls += 1; return { status: 200 }; },
      wait: noWait,
    });
    assert.deepEqual([result.ok, result.attempts, calls], [true, 1, 1]);
  });

  test('a 500 is retried, then reported as not delivered', async () => {
    let calls = 0;
    const result = await deliverInbound({ text: 'hi' }, CFG, {
      fetchImpl: async () => { calls += 1; return { status: 500 }; },
      wait: noWait,
    });
    assert.equal(result.ok, false);
    assert.equal(calls, CFG.backendRetries + 1);
  });

  test('a backend that comes back gets the message', async () => {
    let calls = 0;
    const result = await deliverInbound({ text: 'hi' }, CFG, {
      fetchImpl: async () => {
        calls += 1;
        return calls < 3 ? { status: 503 } : { status: 200 };
      },
      wait: noWait,
    });
    assert.equal(result.ok, true);
    assert.equal(calls, 3);
  });

  test('a timeout is retried - the request never completed', async () => {
    let calls = 0;
    const result = await deliverInbound({ text: 'hi' }, CFG, {
      fetchImpl: async () => {
        calls += 1;
        const error = new Error('timed out');
        error.name = 'TimeoutError';
        throw error;
      },
      wait: noWait,
    });
    assert.equal(result.ok, false);
    assert.equal(calls, CFG.backendRetries + 1);
  });

  test('a 4xx is NOT retried - the same wrong payload fails identically', async () => {
    let calls = 0;
    const result = await deliverInbound({ text: 'hi' }, CFG, {
      fetchImpl: async () => { calls += 1; return { status: 400 }; },
      wait: noWait,
    });
    assert.equal(result.ok, false);
    assert.equal(calls, 1);
  });

  test('classification', () => {
    for (const status of [500, 502, 503, 429, 408, null, undefined]) {
      assert.equal(isRetryable(status), true, `${status} should retry`);
    }
    for (const status of [400, 401, 404, 422]) {
      assert.equal(isRetryable(status), false, `${status} should not retry`);
    }
  });

  test('the shared secret is sent as a header, and the payload is the message',
    async () => {
      let seen = null;
      await deliverInbound({ phone: '91', text: 'hi' }, CFG, {
        fetchImpl: async (url, options) => { seen = { url, options }; return { status: 200 }; },
        wait: noWait,
      });
      assert.equal(seen.url, 'http://backend.test/webhook/inbound');
      assert.equal(seen.options.headers['X-Gateway-Secret'], CFG.secret);
      assert.deepEqual(JSON.parse(seen.options.body), { phone: '91', text: 'hi' });
    });
});

// ---------------------------------------------------------------------------
describe('8 & 9. reconnect and logged-out state', () => {
  test('an ordinary close reconnects', () => {
    for (const code of [515, 428, 500, undefined]) {
      assert.equal(shouldReconnect(code).reconnect, true, `code ${code}`);
    }
  });

  test('a logout does NOT reconnect - the saved session is dead', () => {
    const decision = shouldReconnect(401);
    assert.equal(decision.reconnect, false);
    assert.match(decision.reason, /logged out/);
  });

  test('backoff grows, then holds, so logs stay readable', () => {
    const delays = [0, 1, 2, 3, 4, 5, 6, 7].map(backoffMs);
    assert.ok(delays[0] < delays[3]);
    assert.equal(delays.at(-1), 60000);
    assert.ok(Math.min(...delays) >= 2000, 'never a tight reconnect loop');
  });
});

// ---------------------------------------------------------------------------
describe('4, 5 & 13. outgoing messages, auth, malformed payloads', () => {
  function withServer(handler) {
    return async () => {
      const gateway = {
        connected: true,
        stopped: false,
        sent: [],
        async sendText(phone, text) { this.sent.push({ phone, text }); return 'WAMSG-OUT'; },
      };
      const server = createServer(gateway, CFG, { log: () => {} });
      await new Promise((resolve) => server.listen(0, resolve));
      const port = server.address().port;
      try {
        await handler(port, gateway);
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    };
  }

  const send = (port, body, secret) => fetch(`http://127.0.0.1:${port}/send`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(secret === undefined ? {} : { 'X-Gateway-Secret': secret }),
    },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });

  test('a correct secret sends, and reports id + recipient',
    withServer(async (port, gateway) => {
      const response = await send(port, { phone: '919876543210', text: 'hello' }, CFG.secret);
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), {
        status: 'sent', message_id: 'WAMSG-OUT', phone: '919876543210',
      });
      assert.deepEqual(gateway.sent, [{ phone: '919876543210', text: 'hello' }]);
    }));

  test('a wrong secret is rejected and nothing is sent',
    withServer(async (port, gateway) => {
      assert.equal((await send(port, { phone: '91', text: 'hi' }, 'wrong')).status, 401);
      assert.equal(gateway.sent.length, 0);
    }));

  test('a missing secret is rejected', withServer(async (port, gateway) => {
    assert.equal((await send(port, { phone: '91', text: 'hi' }, undefined)).status, 401);
    assert.equal(gateway.sent.length, 0);
  }));

  test('malformed JSON is refused after auth', withServer(async (port) => {
    assert.equal((await send(port, '{not json', CFG.secret)).status, 400);
  }));

  test('an invalid body is refused', withServer(async (port) => {
    assert.equal((await send(port, { text: 'no recipient' }, CFG.secret)).status, 400);
    assert.equal((await send(port, { phone: '91' }, CFG.secret)).status, 400);
    assert.equal((await send(port, { phone: '91', text: '  ' }, CFG.secret)).status, 400);
    assert.equal((await send(port, [1, 2], CFG.secret)).status, 400);
  }));

  test('a disconnected socket answers 503, which the backend retries',
    withServer(async (port, gateway) => {
      gateway.sendText = async () => {
        const error = new Error('not connected');
        error.code = 'NOT_CONNECTED';
        throw error;
      };
      const response = await send(port, { phone: '91', text: 'hi' }, CFG.secret);
      assert.equal(response.status, 503);
      assert.equal((await response.json()).error, 'whatsapp_not_connected');
    }));

  test('/health needs no secret and leaks nothing', withServer(async (port) => {
    const response = await fetch(`http://127.0.0.1:${port}/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: 'connected', stopped: false });
  }));

  test('an unknown path is 404', withServer(async (port) => {
    assert.equal((await fetch(`http://127.0.0.1:${port}/anything`)).status, 404);
  }));

  test('secret comparison is constant time and length safe', () => {
    assert.equal(secretMatches('abc', 'abc'), true);
    assert.equal(secretMatches('abc', 'abcd'), false);
    assert.equal(secretMatches('', ''), false);
    assert.equal(secretMatches(undefined, 'abc'), false);
  });

  test('validateSend strips formatting and enforces the WhatsApp limit', () => {
    assert.equal(validateSend({ phone: '+91 98765-43210', text: 'hi' }).phone, '919876543210');
    assert.equal(validateSend({ phone: '91', text: 'x'.repeat(4097) }).ok, false);
    assert.equal(validateSend(null).ok, false);
  });
});

// ---------------------------------------------------------------------------
describe('10 & 11. auth directory', () => {
  test('a missing auth directory is CREATED, not an error', async () => {
    const { useMultiFileAuthState } = await import('@whiskeysockets/baileys');
    const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-'));
    const missing = path.join(parent, 'does-not-exist-yet');
    try {
      assert.equal(fs.existsSync(missing), false);
      const { state, saveCreds } = await useMultiFileAuthState(missing);
      assert.ok(state.creds, 'credentials are generated on a first run');
      await saveCreds();
      assert.ok(fs.existsSync(path.join(missing, 'creds.json')));
    } finally {
      fs.rmSync(parent, { recursive: true, force: true });
    }
  });

  test('a session survives a restart, so the QR is scanned once', async () => {
    const { useMultiFileAuthState } = await import('@whiskeysockets/baileys');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-auth-'));
    try {
      const first = await useMultiFileAuthState(dir);
      await first.saveCreds();
      const second = await useMultiFileAuthState(dir);
      assert.equal(second.state.creds.registrationId, first.state.creds.registrationId);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  test('the auth directory is gitignored', () => {
    const ignore = fs.readFileSync(path.resolve('.gitignore'), 'utf8');
    assert.match(ignore, /auth\//, 'session credentials must never be committed');
    assert.match(ignore, /\.env/);
  });

  test('the gateway refuses to start with no shared secret', () => {
    assert.throws(() => assertConfigured({ secret: '' }), /WHATSAPP_GATEWAY_SECRET/);
    assert.doesNotThrow(() => assertConfigured({ secret: 'set' }));
  });

  test('no secret is defaulted in config', () => {
    const previous = process.env.WHATSAPP_GATEWAY_SECRET;
    delete process.env.WHATSAPP_GATEWAY_SECRET;
    assert.equal(config().secret, '');
    if (previous !== undefined) process.env.WHATSAPP_GATEWAY_SECRET = previous;
  });
});

// ---------------------------------------------------------------------------
describe('the round trip: WhatsApp -> gateway -> backend -> gateway -> WhatsApp',
  () => {
    test('completes, and cannot loop', async () => {
      const backendCalls = [];
      const dedupe = createDedupe(10);

      // 1. WhatsApp delivers "I need milk".
      const inbound = normalizeMessage(textMessage());
      assert.equal(dedupe.accept(inbound.message_id), true);

      // 2. The gateway posts it to the backend.
      const { jid, ...forBackend } = inbound;
      await deliverInbound(forBackend, CFG, {
        fetchImpl: async (url, options) => {
          backendCalls.push(JSON.parse(options.body));
          return { status: 200 };
        },
        wait: async () => {},
      });
      assert.deepEqual(backendCalls, [{
        message_id: 'WAMSG-1',
        phone: '919876543210',
        text: 'I need milk',
        timestamp: '1755000000',
        type: 'text',
      }]);

      // 3. The backend answers through /send.
      const reply = '1. Nandini Toned Milk - Rs 26\n2. Amul Taaza Milk - Rs 33';
      assert.equal(validateSend({ phone: inbound.phone, text: reply }).ok, true);

      // 4. Baileys hands our own reply straight back on messages.upsert.
      assert.equal(normalizeMessage({
        key: { remoteJid: jid, fromMe: true, id: 'WAMSG-OUT' },
        message: { conversation: reply },
        messageTimestamp: 1755000005,
      }), null, 'THE LOOP MUST DIE HERE');
      assert.equal(backendCalls.length, 1, 'the backend was called exactly once');

      // 5. The user replies "1" - a new message, which goes through.
      const selection = normalizeMessage(textMessage({
        key: { id: 'WAMSG-2' }, message: { conversation: '1' },
      }));
      assert.equal(selection.text, '1');
      assert.equal(dedupe.accept(selection.message_id), true);

      // 6. WhatsApp redelivers that selection. It must not order twice.
      assert.equal(dedupe.accept(selection.message_id), false);
    });
  });
