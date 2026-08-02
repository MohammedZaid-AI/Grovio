"""
Find out whether a provider's OAuth can actually be discovered from here.

Answers one question: can a real user link this provider through WhatsApp today,
or are we blocked on the provider whitelisting us?

    python scripts/probe_oauth.py
    python scripts/probe_oauth.py https://mcp.swiggy.com/food
"""
import asyncio
import os
import sys

# Run from anywhere, in any shell, without setting PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import httpx

from ai.providers import oauth

DEFAULT = "https://mcp.swiggy.com/im"


async def main():
    server = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT).rstrip("/")
    print(f"\nProbing {server}\n" + "─" * 62)

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        print("\n1. Well-known protected-resource document (RFC 9728)")
        meta = await oauth._fetch_json(client, f"{server}/.well-known/oauth-protected-resource")
        print(f"   {'FOUND: ' + str(meta)[:160] if meta else 'not served'}")

        print("\n2. 401 challenge on the MCP endpoint (MCP auth spec)")
        hinted = await oauth._challenge_resource_metadata(client, server)
        print(f"   {'FOUND: ' + hinted if hinted else 'no resource_metadata hint returned'}")
        if hinted and not meta:
            meta = await oauth._fetch_json(client, hinted)
            print(f"   fetched: {str(meta)[:160] if meta else 'could not fetch'}")

    print("\n3. Full discovery")
    try:
        metadata = await oauth.discover(oauth.OAuthConfig(server_url=server))
        print("   ✅ endpoints resolved")
        for key in ("issuer", "authorization_endpoint", "token_endpoint",
                    "registration_endpoint"):
            if metadata.get(key):
                print(f"      {key:24} {metadata[key]}")
        if not metadata.get("registration_endpoint"):
            print("      registration_endpoint    ABSENT — a client_id must be issued to us")
    except oauth.OAuthError as e:
        print(f"   ❌ {e}")
        print("\n   → Real users cannot link this provider yet.")
        print("     Either the provider publishes no metadata, or access is gated.")
        return

    print("\n4. Authorisation URL we would send a user")
    try:
        url = await oauth.begin("probe", "probe_provider",
                                oauth.OAuthConfig(server_url=server), "probe")
        redacted = url.split("state=")[0] + "state=…"
        print(f"   {redacted}")
        print("\n   ✅ A real user could tap this — provided the provider has")
        print("      whitelisted our redirect_uri:")
        print(f"      {oauth.redirect_uri('swiggy_instamart')}")
    except oauth.OAuthError as e:
        print(f"   ❌ {e}")

    print()


asyncio.run(main())
