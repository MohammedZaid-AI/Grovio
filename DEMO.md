# Demo Script

A single WhatsApp conversation, first message to delivered order, plus what the
system has learned by the end.

**Running time:** 4 minutes. **Setup:** one phone, one screen share.

> Everything a user sees below is produced by the system as built. Ratings,
> prices and ETAs come from live provider responses — if a provider returns no
> rating, the line simply does not appear. Nothing here is mocked for the demo,
> which is the point.

---

## Act 1 — First contact (0:00)

A brand new number. Nothing is known about this person.

> **User**
> I'm hungry

> **Concierge**
> Hey — I'm your food concierge 👋
>
> Tell me what you're in the mood for and I'll find something you'll actually
> want. To order it for you I'll need you to connect your Swiggy account, one
> time:
>
> https://concierge.app/link/swiggy/a8f3…
>
> Once that's done I'll pick up right where we left off.

**Say out loud:** *no signup, no app install, no password.* The phone number is
the identity — WhatsApp already authenticated them. And the assistant never asks
for a Swiggy password or OTP; authorisation happens on Swiggy's own domain.

---

## Act 2 — Connect, and resume itself (0:30)

Tap the link → Swiggy's consent screen → back to a confirmation page:

> **✓ Swiggy connected**
> You're all set — head back to WhatsApp. I'm already working on what you asked.

Switch back to WhatsApp. A message is already waiting:

> **Concierge**
> Nice — you're connected. It's Friday evening, so: what are you after tonight?
> Something comforting, something light, or shall I surprise you?

**Say out loud:** *they never repeated themselves.* The original "I'm hungry" was
stored when the link was generated and re-queued through the normal message
pipeline the moment authorisation completed. It also knows it is Friday evening
without being told.

---

## Act 3 — Recommendation (1:00)

> **User**
> craving biryani, nothing too expensive

> **Concierge**
> Three good options near you.
>
> 1. Meghana Foods
> ⭐ 4.6 · ₹340 · 22 min
> The spicy one you'd expect — and it's the highest rated nearby.
>
> 2. Empire Restaurant
> ⭐ 4.3 · ₹280 · 18 min
> Cheaper and quicker, and comfortably under budget.
>
> 3. Nagarjuna
> ⭐ 4.4 · ₹310 · 26 min
> Andhra style, a bit further out.
>
> Which one?

**Say out loud — this is the most important beat of the demo:**

- Every rating, price and ETA came from a live provider response. **Nothing is
  generated.** With no provider connected, this message would instead say it
  cannot search yet.
- The ordering of these three was decided **in code**, not by the model — budget
  fit, rating, ETA, and order history — so the one-line reason under each option
  is the reason that actually drove its rank. The model phrases it; it cannot
  invent it.

---

## Act 4 — Ordering (1:45)

> **User**
> the second one

> **Concierge**
> Empire it is — Mutton Biryani, ₹280. Confirm and I'll place it?

> **User**
> yes

> **Concierge**
> Done 🎉 About 18 minutes.
>
> Ask me any time where it's got to.

**Say out loud:** *"the second one" resolves to a real item, not a guess.* The
options were persisted in the order they were shown, and ordering takes the
**index** — never a dish name. The model physically cannot order something a
provider did not return. One confirmation before spending, then out of the way.

---

## Act 5 — Tracking (2:30)

> **User**
> where's my order

> **Concierge**
> Out for delivery — about 9 minutes away.

If the platform exposes no live status, it says so rather than inventing one:

> **Concierge**
> I can't see live status for this one, I'm afraid. It was placed at 8:42pm with
> a 22-minute estimate — the Swiggy app will have minute-by-minute.

**Say out loud:** an invented countdown would demo better and be a lie. The
provider declares whether it supports tracking, and the concierge is honest when
it doesn't.

---

## Act 6 — What it now knows (3:15)

Same user, the following Tuesday at 1pm:

> **User**
> lunch?

> **Concierge**
> Empire did you well on Friday — want the biryani again, or something lighter
> for a work day?

Nothing was configured. Between the two conversations it learned the budget
ceiling, that biryani is a repeat preference, that Empire was accepted, and the
difference between a Friday night and a Tuesday lunch.

And when a preference is corrected, it lands immediately:

> **User**
> I don't like mushrooms
>
> **Concierge**
> Noted — I'll keep those off the list.

**Say out loud:** it never announces that it remembered. No "According to my
records". It just behaves like someone who knows you.

---

## Closing (3:45)

> "Five minutes of conversation, and it knows how you eat. No app, no signup, no
> forms. And it will not invent a restaurant to fill a silence — if it can't
> find something real, it says so."

---

## Q&A — likely questions

**"What if the AI hallucinates a restaurant?"**
It structurally cannot. Venues, ratings, prices and ETAs only ever come from a
provider response in that conversation. With no provider available the capability
layer returns an explicit unavailable signal and the model is instructed to say
so. There is no stub provider in the codebase — restaurant search is switched
off rather than faked.

**"What if it orders the wrong thing?"**
Ordering takes the index of an option that was actually shown and persisted, not
a name. Out-of-range and stale selections are refused without contacting a
provider, and there is a confirmation step before any spend.

**"Where are the credentials?"**
Encrypted at rest with a key held outside the database. One module can decrypt
them; a test fails the build if any other layer can even name a token. We never
see a Swiggy password, OTP or card.

**"What happens if Swiggy goes down?"**
The user gets an honest apology and an offer to try again. Provider failures are
isolated per provider and never crash a conversation.

**"Can it do restaurants today?"**
Grocery works end to end right now. Restaurants are built up to the provider
boundary and waiting on Swiggy Builders Club production access — see
[PARTNERSHIP.md](PARTNERSHIP.md). We deliberately did not ship a fake one to
make the demo look finished.

---

## Demo checklist

- [ ] Test number connected and reachable
- [ ] `GET /health` returns `{"ready": true}`
- [ ] `PUBLIC_BASE_URL` is the live https URL
- [ ] A **fresh** phone number for Act 1 (the welcome only fires once)
- [ ] Swiggy account has a saved delivery address
- [ ] A second, already-onboarded number ready to skip to Act 3 if time is short
- [ ] Screen mirroring tested; notifications silenced
- [ ] `DEBUG` **off** — it logs message content
