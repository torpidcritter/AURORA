"""TOTP against the RFC 6238 appendix B test vectors and the replay/drift rules."""

import base64

from app.core import totp

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_rfc6238_vectors():
    for t, expected in [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")]:
        assert totp.code_at(RFC_SECRET, t // 30, digits=8) == expected


def test_verify_window_and_replay():
    secret = totp.new_secret()
    now = 1_700_000_000.0
    step = totp.current_step(now)
    code = totp.code_at(secret, step)
    assert totp.verify(secret, code, now=now) == step
    assert totp.verify(secret, totp.code_at(secret, step - 1), now=now) == step - 1  # 30 s of drift is fine
    assert totp.verify(secret, totp.code_at(secret, step - 2), now=now) is None
    assert totp.verify(secret, code, last_step=step, now=now) is None  # used once already
    assert totp.verify(secret, "12345", now=now) is None and totp.verify(secret, "abcdef", now=now) is None
    assert totp.verify(secret, f"{code[:3]} {code[3:]}", now=now) == step  # spaces as shown by some apps


def test_provisioning_uri():
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "ana@example.com")
    assert uri.startswith("otpauth://totp/Aurora:ana@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Aurora")
    assert len(totp.new_secret()) == 32
