"""Does a diff touch the security surface? (The SDK's `security_gate` obligation.)

The surface is sign-in/auth/authz, credentials/secrets, data migrations and personal data. Detection is
deliberately conservative: a path match OR Codex's own report puts a diff on the surface, because a false
positive costs one human look and a false negative merges an unreviewed auth change."""
from __future__ import annotations

import re

AUTH = "sign-in/auth/authz"
SECRETS = "credentials/secrets"
MIGRATIONS = "data migrations"
PII = "personal data (PII)"
SURFACES = (AUTH, SECRETS, MIGRATIONS, PII)

_PATTERNS = (
    (AUTH, re.compile(r"(^|[/_.-])(auth|authn|authz|login|logout|signin|sign[-_]in|signup|sso|saml|oauth2?|"
                      r"oidc|jwt|session|sessions|permissions?|rbac|acl|access[-_]control|roles?)([/_.-]|$)",
                      re.I)),
    (SECRETS, re.compile(r"(^|[/_.-])(secrets?|credentials?|passwords?|passwd|tokens?|api[-_]?keys?|vault|"
                         r"kms|keyring)([/_.-]|$)|(^|/)\.env(\.|$)|\.(pem|key|p12|pfx)$", re.I)),
    (MIGRATIONS, re.compile(r"(^|/)(migrations?|alembic|migrate)(/|$)|\.sql$", re.I)),
    (PII, re.compile(r"(^|[/_.-])(pii|gdpr|personal[-_]data|privacy|user[-_]profiles?)([/_.-]|$)", re.I)),
)


def surfaces_for_paths(paths: list[str]) -> list[str]:
    hit = {name for path in paths for name, rx in _PATTERNS if rx.search(path)}
    return [s for s in SURFACES if s in hit]


def classify(paths: list[str], reported: list[str] | None = None) -> list[str]:
    """Named surfaces this change touches: path matches plus whatever Codex reported (normalised)."""
    hit = set(surfaces_for_paths(paths))
    for item in reported or []:
        text = str(item).strip().lower()
        if not text:
            continue
        matched = [s for s in SURFACES if any(word in text for word in _WORDS[s])]
        hit.update(matched or [str(item).strip()])
    ordered = [s for s in SURFACES if s in hit]
    return ordered + sorted(h for h in hit if h not in SURFACES)


_WORDS = {
    AUTH: ("auth", "login", "sign-in", "sign in", "session", "permission", "role", "sso", "oauth"),
    SECRETS: ("secret", "credential", "password", "token", "key"),
    MIGRATIONS: ("migration", "schema"),
    PII: ("pii", "personal", "privacy", "gdpr"),
}
