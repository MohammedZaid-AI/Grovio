# website/

Three static pages. **This is not part of the product.**

CLAUDE.md says WhatsApp is the entire product and a feature needing a screen is
the wrong feature. That still holds — nothing here is a product surface, and the
app never serves these files. They exist for one reason:

> Meta disabled the business portfolio on 29 Jun 2025 because *"the website
> listed in its Business Manager profile does not have information needed to
> determine that your business complies with our Business Policy."*

A reviewer needs a reachable page that says what the business is, plus a privacy
policy and terms. That is what these are. Do not delete them as a pivot leftover.

## Before publishing — fill in the placeholders

Search for `[` across all three files:

| Placeholder | Where |
|---|---|
| `[LEGAL NAME]` | index footer, privacy §1, terms §13 |
| `[FULL ADDRESS]` | index footer, privacy §1, terms §13 |
| `[CITY]` | terms §12 |
| `hello@grovio.app` | everywhere — replace with an address you actually read |

The legal name and address **must match** what you enter in Meta Business
Settings → Business Info. A mismatch is a rejection.

```bash
grep -rn "\[LEGAL NAME\]\|\[FULL ADDRESS\]\|\[CITY\]\|hello@grovio.app" website/
```

## Publishing

No build step, no dependencies, no JavaScript. Any static host works.

**GitHub Pages** — settings → Pages → deploy from `main`, folder `/website`.
**Netlify / Cloudflare Pages** — drag the folder in.
**Lovable** — replace the current site's content with this copy.

The existing site at `kitchen-chat-genius.lovable.app` describes the pre-pivot
inventory ERP. Whatever you host, **that content must go** — a reviewer seeing
an inventory tool while the WhatsApp account does consumer food ordering is the
exact mismatch that caused the disable.

## Accuracy

Every claim is checked against the code, and it stays that way:

| Page says | Enforced by |
|---|---|
| never invents a restaurant, price or ETA | `ai/recommendation.py`, provider adapters return `None` for unknown fields |
| only orders something just shown to you | `skills.place_order` takes an index into `conversation_state`, never a name |
| never messages you first | there is no outbound-initiation path in the codebase |
| never sees your password | `ai/providers/oauth.py` — OAuth 2.1 + PKCE, no credential ever reaches us |
| tokens encrypted at rest | `core/crypto.py`, fails closed with no plaintext fallback |
| every webhook cryptographically verified | `whatsapp/cloud_api.py:verify_signature`, fails closed |
| allergy filtering is convenience, not a guarantee | matches `recommendation._is_avoided`'s documented ceiling |

If the product changes, these change. A privacy policy that overstates is worse
than none — it is the thing you get held to.
