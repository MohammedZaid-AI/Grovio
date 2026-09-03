/**
 * WhatsApp gateway.
 *
 *   WhatsApp <-> Baileys <-> this process <-> HTTP <-> the backend
 *
 * The backend never imports Baileys and never sees a Baileys object. It posts
 * {phone, text} here, and receives {message_id, phone, text, timestamp, type}
 * back. JIDs, message keys and session state stop at this boundary.
 *
 * WARNING: Baileys is an UNOFFICIAL WhatsApp Web protocol client, not the Meta
 * WhatsApp Business Cloud API. WhatsApp can ban the number. Use a spare one,
 * never a number tied to a business account.
 */
import { assertConfigured, config, loadEnv } from './src/config.js';
import { createGateway } from './src/socket.js';
import { createServer } from './src/server.js';

loadEnv();
const cfg = config();

try {
  assertConfigured(cfg);
} catch (error) {
  console.error(`[gateway] ${error.message}`);
  process.exit(1);
}

console.log('[gateway] UNOFFICIAL WhatsApp Web client (Baileys). Not the Meta '
  + 'Business Cloud API - the number can be banned.');

const gateway = createGateway(cfg);
const server = createServer(gateway, cfg);

server.listen(cfg.port, () => {
  console.log(`[gateway] listening on :${cfg.port}`);
  console.log(`[gateway] backend at ${cfg.backendUrl}${cfg.inboundPath}`);
});

gateway.connect();

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    console.log('\n[gateway] shutting down');
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
  });
}
