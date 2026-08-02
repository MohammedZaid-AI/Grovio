"""
Local testing only: mark a phone as linked to a provider.

The Instamart adapter's search() does not use the stored token — the MCP client
authenticates itself. This satisfies the vault's gate so you can exercise the
full journey locally without a whitelisted OAuth callback.

    python scripts/dev_link.py whatsapp:+919876543210
"""
import os
import sys

# Run from anywhere, in any shell, without setting PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import db
from core import crypto

phone = sys.argv[1] if len(sys.argv) > 1 else None
if not phone:
    sys.exit("usage: python scripts/dev_link.py <phone, exactly as WhatsApp sends it>")

provider = sys.argv[2] if len(sys.argv) > 2 else "swiggy_instamart"

db.init_db()
db.get_or_create_user(phone)
db.save_provider_link(
    phone=phone,
    provider=provider,
    access_token=crypto.encrypt("dev-local-token"),
    refresh_token=None,
    expires_at=None,          # never expires -> no refresh attempted
)
db.update_user(phone, onboarding_status="LINKED")
print(f"linked {phone} -> {provider}")
print("providers now linked:", db.get_linked_providers(phone))
