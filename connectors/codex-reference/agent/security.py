"""Does a delivery touch the security surface? (The SDK's `security_gate` obligation.)

The surface is sign-in/auth/authz, credentials/secrets, data migrations and personal data — plus the
files that decide what CI and code ownership enforce, because changing those changes every other gate.
Detection is deliberately conservative: a path match OR Codex's own report puts a delivery on the surface,
since a false positive costs one human look and a false negative merges an unreviewed auth change. The
paths classified are every path the whole branch changes against its base — both sides of a rename, and
commits an earlier attempt pushed — never just the last pass's edits (see `gitops.delivery_paths`)."""
from __future__ import annotations

import re

AUTH = "sign-in/auth/authz"
SECRETS = "credentials/secrets"
MIGRATIONS = "data migrations"
PII = "personal data (PII)"
CI = "CI/ownership config"
SURFACES = (AUTH, SECRETS, MIGRATIONS, PII, CI)

# Substring matches on purpose (`authentication`, `AuthMiddleware`, `LoginController` all hit). Only the
# words that would drown everything in false positives (`token`, `key`, `role`) need a whole-token match.
_T = r"(^|[/_.-])"
_E = r"([/_.-]|$)"
_PATTERNS = (
    (AUTH, re.compile(r"auth|login|logout|signin|sign[-_]in|signup|sign[-_]up|password|passw|sso|saml|oauth|"
                      r"oidc|jwt|session|permission|rbac|acl\b|access[-_]?control|crypt|"
                      + _T + r"(roles?|guard|policy|policies)" + _E, re.I)),
    (SECRETS, re.compile(r"secret|credential|vault|keyring|kms|" + _T + r"(tokens?|api[-_]?keys?|keys?)" + _E
                         + r"|(^|/)\.env(\.|$)|(^|/)\.(npmrc|pypirc|netrc|dockercfg)$|(^|/)id_(rsa|ed25519|ecdsa)"
                           r"|\.(pem|key|crt|cer|p12|pfx|jks|keystore)$", re.I)),
    (MIGRATIONS, re.compile(r"(^|/)(migrations?|alembic|migrate)(/|$)|\.sql$|(^|/)schema\.[a-z]+$|\.prisma$",
                            re.I)),
    (PII, re.compile(r"pii|gdpr|ccpa|hipaa|personal[-_]?data|privacy|" + _T + r"(user[-_]?profiles?|ssn)" + _E,
                     re.I)),
    (CI, re.compile(r"(^|/)\.github/|(^|/)CODEOWNERS$|(^|/)\.gitlab-ci\.yml$|(^|/)Jenkinsfile$|"
                    r"(^|/)\.circleci/|(^|/)azure-pipelines\.ya?ml$|(^|/)bitbucket-pipelines\.yml$|\.tf$", re.I)),
)

_WORDS = {
    AUTH: r"\b(auth\w*|login|sign[- ]?in|sessions?|permissions?|roles?|sso|oauth\w*|access control)\b",
    SECRETS: r"\b(secrets?|credentials?|passwords?|tokens?|api[- ]?keys?|keys?)\b",
    MIGRATIONS: r"\b(migrations?|schema)\b",
    PII: r"\b(pii|personal|privacy|gdpr)\b",
    CI: r"\b(ci|codeowners|workflows?|pipelines?)\b",
}
_NONE = {"", "none", "n/a", "na", "no", "nothing", "-"}


def surfaces_for_paths(paths: list[str]) -> list[str]:
    hit = {name for path in paths for name, rx in _PATTERNS if rx.search(path)}
    return [s for s in SURFACES if s in hit]


def classify(paths: list[str], reported: list[str] | None = None) -> list[str]:
    """Named surfaces this delivery touches: path matches plus whatever Codex reported (normalised)."""
    hit = set(surfaces_for_paths(paths))
    for item in reported or []:
        text = str(item).strip()
        if text.lower().rstrip(".") in _NONE:
            continue
        matched = [s for s in SURFACES if re.search(_WORDS[s], text, re.I)]
        hit.update(matched or [text])
    ordered = [s for s in SURFACES if s in hit]
    return ordered + sorted(h for h in hit if h not in SURFACES)
