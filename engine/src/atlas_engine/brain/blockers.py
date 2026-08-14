"""Genuine human-only gates — a direct port of the previous app's
`core/src/brain/blockers.ts` detectBlockers. When one of these is on the page,
no amount of selector/gesture correction helps; the honest move is a precise,
evidence-attached handoff instead of flailing."""

import re
from dataclasses import dataclass

_CHECKS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "captcha",
        re.compile(r'class="[^"]*(g-recaptcha|h-captcha|cf-turnstile)|\b(recaptcha|hcaptcha|funcaptcha|arkose)\b', re.IGNORECASE),
        "CAPTCHA widget present",
    ),
    (
        "captcha",
        re.compile(r"<iframe[^>]+(recaptcha|hcaptcha|turnstile|captcha-|challenge)", re.IGNORECASE),
        "captcha/challenge iframe",
    ),
    (
        "antibot",
        re.compile(r"cf-browser-verification|checking your browser|attention required.{0,40}cloudflare|px-captcha|datadome", re.IGNORECASE),
        "anti-bot interstitial",
    ),
    # Hardware/app MFA — VISIBLE prompt text only (not JS feature-detection like
    # navigator.credentials, which countless app bundles reference benignly).
    (
        "mfa",
        re.compile(r"security key|authenticator app|scan (the|this) qr|enter the (code|otp) (sent|from)|verification code (sent|was sent)", re.IGNORECASE),
        "hardware/app MFA step-up",
    ),
    # OTP input — a STRONG signal only: the standard autocomplete hint, or a
    # name/id that is a WHOLE otp token (not "otp" buried in a longer unrelated id).
    (
        "mfa",
        re.compile(r'autocomplete="one-time-code"|(name|id)="(otp|totp|otpcode|otp[_-]?code|one-?time-?code|mfacode|2fa[_-]?code|verification[_-]?code)"', re.IGNORECASE),
        "OTP input with no configured source",
    ),
]


@dataclass(frozen=True)
class Blocker:
    kind: str
    detail: str


def detect_blockers(html: str) -> Blocker | None:
    if not html:
        return None
    # Scan only the RENDERED markup: strip <script>/<style>/comments first. Real
    # app bundles reference "webauthn"/"navigator.credentials"/captcha-library
    # names in JS and would false-positive a blocker on a page with no actual
    # gate (this was firing a fake "OTP/MFA" blocker on eCW's app bundle).
    rendered = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    rendered = re.sub(r"<style[\s\S]*?</style>", " ", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"<!--[\s\S]*?-->", " ", rendered)
    for kind, pattern, detail in _CHECKS:
        if pattern.search(rendered):
            return Blocker(kind=kind, detail=detail)
    # Canvas-only app: a body with a canvas and almost no interactive elements.
    interactive = len(re.findall(r"<(a|button|input|select|textarea)\b", rendered, flags=re.IGNORECASE))
    if re.search(r"<canvas\b", rendered, flags=re.IGNORECASE) and interactive < 3 and len(rendered) > 20000:
        return Blocker(kind="no-dom", detail="canvas-rendered UI with no accessible elements")
    return None