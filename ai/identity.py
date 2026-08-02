"""
User identity.

The WhatsApp phone number IS the identity. There is no signup, no password and
no profile screen — WhatsApp already authenticated the person by virtue of them
controlling the number.

This module owns the user record and its lifecycle. It knows nothing about
OAuth or providers; it only records *whether* onboarding has progressed.
"""
from dataclasses import dataclass, field

import db
from ai import memory

# Onboarding lifecycle.
NEW = "NEW"            # never linked a provider
LINKED = "LINKED"      # can transact
COMPLETE = "COMPLETE"  # the few essential questions have been answered

# The only things worth asking for outright. Everything else is learned from
# conversation — see ai/memory.py. Each question here is friction, so the list
# stays short and allergies earn their place by being safety-critical.
ESSENTIAL_FACTS = ("home", "budget", "allergies")


@dataclass
class User:
    """A person, assembled for one turn: who they are, what we know, what they
    have connected."""
    phone: str
    display_name: str | None = None
    onboarding_status: str = NEW
    profile: memory.UserModel = None
    linked_providers: list = field(default_factory=list)

    # -- convenience passthroughs so callers don't reach through .profile --
    @property
    def facts(self) -> dict:
        return self.profile.facts

    @property
    def history(self) -> list:
        return self.profile.history

    @property
    def food(self) -> list:
        return self.profile.food

    @property
    def is_linked(self) -> bool:
        return bool(self.linked_providers)

    def has_linked(self, provider: str) -> bool:
        return provider in self.linked_providers

    def missing_essentials(self) -> list:
        return [key for key in ESSENTIAL_FACTS if not self.profile.facts.get(key)]

    def describe(self) -> str:
        """Profile text for the system prompt, plus onboarding context so the
        model knows what it may still ask about."""
        lines = [self.profile.describe()]
        if self.display_name:
            lines.insert(0, f"Name: {self.display_name}")

        if self.onboarding_status == COMPLETE:
            return "\n".join(lines)

        missing = self.missing_essentials()
        if missing:
            lines.append(
                "Still unknown (ask at most ONE of these, only when it is "
                f"naturally relevant): {', '.join(missing)}"
            )
        return "\n".join(lines)


def load(phone: str) -> User:
    """Get or create the user, then assemble everything known about them."""
    row = db.get_or_create_user(phone)
    return User(
        phone=phone,
        display_name=row.get("display_name"),
        onboarding_status=row.get("onboarding_status") or NEW,
        profile=memory.load(phone),
        linked_providers=db.get_linked_providers(phone),
    )


def set_display_name(phone: str, name: str) -> None:
    db.update_user(phone, display_name=name)


def mark_linked(phone: str) -> None:
    """Called when a first provider is connected. Never downgrades COMPLETE."""
    row = db.get_or_create_user(phone)
    if row.get("onboarding_status") == NEW:
        db.update_user(phone, onboarding_status=LINKED)


def refresh_onboarding_status(phone: str) -> str:
    """Promote to COMPLETE once the essentials are known. Called after a turn,
    so onboarding finishes by conversation rather than by interrogation."""
    user = load(phone)
    if user.onboarding_status == LINKED and not user.missing_essentials():
        db.update_user(phone, onboarding_status=COMPLETE)
        return COMPLETE
    return user.onboarding_status
