"""Per-tenant secret encryption. Lane B, B4.

OWNER: Lane B. See `ADR-001-database.md`, "Secrets: the cipher is a seam".

WHY THIS IS STDLIB AND NOT `cryptography`. Measured, not assumed: `cryptography` is NOT in
this project's declared dependency closure. PyJWT requires it only under an extra, and CI
installs `.[dev]` and nothing else, so an import here works in the local venv and fails in
CI -- and this lane does not own `pyproject.toml`. So the construction below is
hashlib/hmac/secrets, and the cipher is a SEAM: every stored row records which cipher
wrote it, so binding this to KMS later is visible in the data rather than inferred from a
deployment date.

THE STDLIB CONSTRUCTION IS ADEQUATE FOR A DEMO AND IS NOT AES-GCM. Stated plainly because
the opposite claim is the failure shape this repository documents most: a check that reads
as stronger than it is. It is encrypt-then-MAC with independent subkeys and a per-record
random nonce, which is the right SHAPE; what it lacks is a reviewed, constant-time,
hardware-accelerated primitive. The deployed path should bind `CIPHER_KMS_V1` to
`boto3`, which IS declared.

NOTHING IN THIS MODULE LOGS, PRINTS OR REPRS A PLAINTEXT, A KEY OR A CIPHERTEXT.
`tests/test_tenancy_secrets.py` captures this module's log output while encrypting a known
secret and greps it for that value -- so a `logging` call added here with a plaintext in
it fails a named test rather than shipping. `EncryptedRecord.__repr__` is written by hand
for the same reason: a dataclass's generated repr puts every field in the string, and one
`logging.debug("%s", record)` anywhere downstream would then write the ciphertext to a log
aggregator.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

# WHICH CIPHER WROTE A ROW. Recorded per record, exactly as
# `SecurityResult.scan_provenance` records which scanner mode produced a verdict -- and
# for the same reason: without it, a silent downgrade from a KMS-backed cipher to this
# local one is indistinguishable from correct operation, and every row keeps decrypting.
CIPHER_LOCAL_V1 = "local-hmac-sha256-ctr-v1"

# The environment variable holding the master key. Read through `master_key()` at call
# time, never bound at import: CLAUDE.md records that `from ..common.config import X`
# binds a value before any fixture runs, so the knob silently ignores both the tests and
# the deployed environment.
MASTER_KEY_ENV = "AGENTORG_SECRET_KEY"

# scrypt parameters. n=2**14 is the interactive-login figure from the scrypt paper -- it
# costs ~16 MiB and a few milliseconds, which is right for a secret read per run and wrong
# for one read per HTTP request. If this ever moves onto a hot path, the answer is a
# cached derived key, NOT a lower n.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SUBKEY_BYTES = 32
_NONCE_BYTES = 16
_BLOCK = hashlib.sha256().digest_size


class SecretKeyMissing(RuntimeError):
    """The master key is absent or blank.

    Raised rather than defaulted. A default master key is a SHARED master key -- every
    deployment that forgot to set one would encrypt with the same bytes, so any operator
    of any instance could read every other instance's secrets, and nothing would look
    wrong from inside.
    """


class MacMismatch(ValueError):
    """The MAC did not verify, so the ciphertext was not decrypted.

    Carries no plaintext, no key and no ciphertext -- an exception message is the most
    reliable way for a secret to reach a log, because error paths are exactly where
    logging is added without much thought.
    """


def master_key() -> bytes:
    """The master key from the environment, or a refusal.

    A function rather than a module constant so it is read at call time. Returns bytes,
    and does not echo the value in any error.
    """
    raw = os.environ.get(MASTER_KEY_ENV, "")
    if not raw or not raw.strip():
        raise SecretKeyMissing(
            f"{MASTER_KEY_ENV} is unset or blank, so no secret can be encrypted or "
            f"decrypted. Refused rather than defaulted: a default master key is a key "
            f"shared by every deployment that forgot to set one. The value is never "
            f"echoed by this module."
        )
    return raw.encode("utf-8")


def _subkeys(key: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    """Two INDEPENDENT subkeys -- one for the keystream, one for the MAC.

    NEVER ONE KEY FOR BOTH. Reusing a single key for encryption and authentication is the
    classic construction error: the two uses interact, and a MAC computed with the
    keystream key can leak keystream material. Here one scrypt call produces 64 bytes and
    the halves are used for different purposes, so they are independent by construction.

    The nonce is the salt, so every record derives fresh subkeys. That is what makes the
    nonce-reuse hazard below survivable in practice: two records with different nonces
    never share a keystream even under the same master key.
    """
    derived = hashlib.scrypt(
        key,
        salt=nonce,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SUBKEY_BYTES * 2,
    )
    return derived[:_SUBKEY_BYTES], derived[_SUBKEY_BYTES:]


def _keystream(stream_key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 in counter mode.

    THIS IS A STREAM CIPHER, AND NONCE REUSE UNDER THE SAME KEY IS CATASTROPHIC: two
    ciphertexts made with one keystream XOR to the XOR of their plaintexts, so an attacker
    holding both learns their difference without holding the key -- and with one known
    plaintext, learns the other outright.

    That is why the nonce is `secrets.token_bytes` per record and is NEVER derived from
    the secret's name, the tenant id, or a counter. A nonce derived from the name would
    repeat every time a tenant rotated a secret under the same name, which is precisely
    the case where the two plaintexts are the old and new value of one credential.
    """
    blocks = bytearray()
    counter = 0
    while len(blocks) < length:
        blocks += hmac.new(
            stream_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        counter += 1
    return bytes(blocks[:length])


def _mac(mac_key: bytes, nonce: bytes, ciphertext: bytes, cipher: str) -> bytes:
    """ENCRYPT-THEN-MAC, over the nonce, the ciphertext AND the cipher label.

    The label is inside the MAC so a stored row cannot be edited to name a weaker cipher
    and still verify -- otherwise an attacker with write access to the table could
    downgrade the algorithm and the reader would obey the row.

    The nonce is inside it because a MAC over the ciphertext alone lets an attacker swap
    nonces between two rows: both still verify, and each decrypts to garbage the caller
    cannot distinguish from a corrupted secret.
    """
    return hmac.new(
        mac_key,
        nonce + ciphertext + cipher.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    # validate=True so a corrupted column raises here rather than silently decoding to
    # fewer bytes and failing the MAC with a message about authentication -- two
    # different faults deserve two different answers.
    return base64.b64decode(text, validate=True)


@dataclass(frozen=True)
class EncryptedRecord:
    """One encrypted secret, in the shape the `secret` table stores.

    The field names match the table's columns exactly, so there is no mapping layer free
    to drift. There is no plaintext field, and no column for one either -- so there is
    nowhere a careless write could put a token.
    """

    nonce: str
    ciphertext: str
    mac: str
    cipher: str

    def __repr__(self) -> str:
        """Redacted, deliberately.

        A dataclass's generated repr contains every field, so a single
        `logging.debug("%s", record)` downstream would write the ciphertext into a log
        aggregator -- where it outlives the database's access controls entirely. The
        cipher label is safe to show and is the one thing an operator actually wants from
        a repr.
        """
        return f"EncryptedRecord(cipher={self.cipher!r}, <redacted>)"

    __str__ = __repr__


def encrypt(plaintext: str, *, key: bytes | None = None) -> EncryptedRecord:
    """Encrypt `plaintext`. A fresh random nonce every call.

    `key` is keyword-only and defaults to reading the environment. Keyword-only so a
    caller cannot pass the plaintext and the key positionally in the wrong order -- which
    would encrypt the key under the secret and store the result, a mistake that produces
    a valid-looking row.
    """
    material = master_key() if key is None else key
    if not material:
        raise SecretKeyMissing(
            "an empty key was passed explicitly. Refused for the same reason a blank "
            "environment value is refused."
        )

    nonce = secrets.token_bytes(_NONCE_BYTES)
    stream_key, mac_key = _subkeys(material, nonce)
    raw = plaintext.encode("utf-8")
    ciphertext = bytes(
        a ^ b for a, b in zip(raw, _keystream(stream_key, nonce, len(raw)), strict=True)
    )
    return EncryptedRecord(
        nonce=_b64(nonce),
        ciphertext=_b64(ciphertext),
        mac=_b64(_mac(mac_key, nonce, ciphertext, CIPHER_LOCAL_V1)),
        cipher=CIPHER_LOCAL_V1,
    )


def decrypt(record: EncryptedRecord, *, key: bytes | None = None) -> str:
    """Verify, THEN decrypt. Raises `MacMismatch` if the MAC does not verify.

    ORDER IS THE REQUIREMENT. Decrypting first and checking afterwards means the caller's
    stack frame briefly holds attacker-chosen plaintext, and any code that logs on the
    error path logs it. Nothing here decrypts until the MAC verifies.

    `hmac.compare_digest`, never `==`. `==` on bytes returns at the first differing byte,
    so the time it takes reveals how many leading bytes matched, and an attacker who can
    submit candidate MACs recovers a valid one byte by byte. This is the same reasoning
    `infra/ingress/handler.py` records for the webhook signature.
    """
    material = master_key() if key is None else key

    if record.cipher != CIPHER_LOCAL_V1:
        # Refused rather than attempted. A row naming a cipher this build does not
        # implement is either from a newer deployment or has been tampered with, and
        # guessing which by trying the local cipher anyway would turn the second case
        # into a MAC error that reads like corruption.
        raise MacMismatch(
            f"this row records cipher {record.cipher!r}, which this build cannot read. "
            f"Refused rather than attempted with the local cipher."
        )

    nonce = _unb64(record.nonce)
    ciphertext = _unb64(record.ciphertext)
    stream_key, mac_key = _subkeys(material, nonce)

    expected = _mac(mac_key, nonce, ciphertext, record.cipher)
    if not hmac.compare_digest(expected, _unb64(record.mac)):
        raise MacMismatch(
            "the stored MAC did not verify, so the value was NOT decrypted. Either the "
            "master key is not the one that wrote this row, or the row has been "
            "modified. No plaintext, key or ciphertext is included in this message."
        )

    raw = bytes(
        a ^ b
        for a, b in zip(
            ciphertext, _keystream(stream_key, nonce, len(ciphertext)), strict=True
        )
    )
    return raw.decode("utf-8")
