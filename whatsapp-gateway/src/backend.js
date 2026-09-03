/**
 * Posting inbound messages to the backend, and remembering what we have seen.
 *
 * The gateway holds the only copy of a message between WhatsApp and the
 * backend, so "the backend is restarting" must not mean "your message is gone".
 * Retries are bounded and classified: a 5xx or a timeout is worth another go, a
 * 4xx means we are sending something wrong and will fail identically forever.
 */

/**
 * Remembers WhatsApp message ids so a redelivery is answered once.
 *
 * Keyed on the WhatsApp message id — stable, provider-issued, and the same key
 * the backend's own queue deduplicates on. Never a locally generated or hashed
 * id, which would differ between processes and between restarts.
 */
export function createDedupe(limit = 2000) {
  const seen = new Set();
  const order = [];
  return {
    /** true the FIRST time an id is offered, false every time after. */
    accept(id) {
      if (!id) return false;
      if (seen.has(id)) return false;
      seen.add(id);
      order.push(id);
      while (order.length > limit) seen.delete(order.shift());
      return true;
    },
    get size() { return seen.size; },
  };
}

export function isRetryable(status) {
  // No status at all means the request never completed — a timeout, a refused
  // connection, a backend mid-restart. All worth retrying.
  if (status === null || status === undefined) return true;
  if (status === 408 || status === 429) return true;
  return status >= 500;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Deliver one normalized message. Returns {ok, status, attempts}.
 *
 * `fetchImpl` and `wait` are injected so the retry logic is testable without a
 * backend and without actually waiting.
 */
export async function deliverInbound(message, cfg, {
  fetchImpl = fetch, wait = sleep, log = () => {},
} = {}) {
  const url = `${cfg.backendUrl}${cfg.inboundPath}`;
  let attempt = 0;

  while (attempt <= cfg.backendRetries) {
    attempt += 1;
    let status = null;
    try {
      const response = await fetchImpl(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Gateway-Secret': cfg.secret,
        },
        body: JSON.stringify(message),
        signal: AbortSignal.timeout(cfg.backendTimeoutMs),
      });
      status = response.status;
      if (status >= 200 && status < 300) {
        return { ok: true, status, attempts: attempt };
      }
    } catch (error) {
      log(`backend unreachable (${error.name}) on attempt ${attempt}`);
    }

    if (!isRetryable(status)) {
      // Our payload is wrong. Retrying sends the same wrong thing again.
      log(`backend rejected the message with ${status}; not retrying`);
      return { ok: false, status, attempts: attempt };
    }
    if (attempt > cfg.backendRetries) break;
    await wait(Math.min(30000, 1000 * 2 ** (attempt - 1)));
  }

  return { ok: false, status: null, attempts: attempt };
}
