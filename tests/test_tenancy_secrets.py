"""Per-tenant secret encryption. Lane B, B4.

OWNER: Lane B.

THE TEST THIS FILE EXISTS FOR is
`test_no_log_line_from_this_module_contains_the_secret` -- the brief asks for a test that
greps this module's own log output for a secret value, because the postmortem in CLAUDE.md
is about a live GitHub token that reached a build artifact, and the shape of that failure
is a secret written somewhere nobody was looking. At multi-tenant scale the same class of
mistake exposes every customer at once.

WHY A CAPTURING HANDLER ON THE ROOT LOGGER RATHER THAN `caplog`. `caplog` reports what
pytest's own handler saw, and the point here is stronger: NOTHING may emit, at any level,
from anywhere under `agentorg.tenancy.crypto`, on the success path OR the failure path. A
handler on the root logger at DEBUG sees every record any logger in the process produces,
so the assertion covers a `logging.debug` added by a future edit -- which is exactly how
a secret reaches a log, since error paths are where logging is added without much thought.

A DELIBERATELY FAKE CREDENTIAL. `ghp_` prefixed but not a real token, per CLAUDE.md's rule
that only fake credential literals appear in tests.
"""

import base64
import logging

import pytest

from agentorg.tenancy import crypto

# A fake token, shaped like a real one so a substring search for it is meaningful.
FAKE_TOKEN = "ghp_ThisIsAFakeTokenForTestsOnly0123456789"
TEST_KEY = b"a-test-master-key-which-is-not-a-real-one"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    """Every test gets a key, set through the environment the module actually reads.

    Through `monkeypatch.setenv` rather than by patching a module attribute, because
    `master_key()` reads `os.environ` at call time on purpose -- and a test that patched
    a cached constant would pass while the deployed path read nothing.
    """
    monkeypatch.setenv(crypto.MASTER_KEY_ENV, TEST_KEY.decode())


@pytest.fixture()
def captured_logs():
    """Every log record emitted anywhere in the process, at any level."""
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
            records.append(self.format(record))

    handler = _Capture()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


def _tamper_ciphertext(record: crypto.EncryptedRecord) -> crypto.EncryptedRecord:
    raw = bytearray(base64.b64decode(record.ciphertext))
    raw[0] ^= 0x01
    return crypto.EncryptedRecord(
        nonce=record.nonce,
        ciphertext=base64.b64encode(bytes(raw)).decode("ascii"),
        mac=record.mac,
        cipher=record.cipher,
    )


# ──────────────────────────────────────────────────────────────────────────────
# The requirement in the brief: never logged
# ──────────────────────────────────────────────────────────────────────────────

def test_no_log_line_from_this_module_contains_the_secret(captured_logs):
    """B4's named requirement. Both paths, because the failure path is the risky one.

    A `logging.exception` on a decrypt failure is the most natural line for somebody to
    add, and the most likely to carry the value that failed.
    """
    record = crypto.encrypt(FAKE_TOKEN)
    assert crypto.decrypt(record) == FAKE_TOKEN

    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(_tamper_ciphertext(record))
    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(record, key=b"the-wrong-key")

    for line in captured_logs:
        assert FAKE_TOKEN not in line, f"a log line contains the secret: {line!r}"
        assert TEST_KEY.decode() not in line, f"a log line contains the key: {line!r}"
        assert record.ciphertext not in line, "a log line contains the ciphertext"


def test_the_module_emits_no_log_records_at_all_on_either_path(captured_logs):
    """Stronger than "no secret in the logs", and the reason it can be stronger.

    This module has nothing to say. Any record at all is a line somebody added, and it
    is worth failing on that so the addition is reviewed rather than discovered.
    """
    record = crypto.encrypt(FAKE_TOKEN)
    crypto.decrypt(record)
    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(_tamper_ciphertext(record))
    assert captured_logs == [], f"unexpected log output: {captured_logs}"


def test_the_repr_redacts_and_is_not_the_generated_dataclass_one():
    """A dataclass's generated repr holds every field.

    One `logging.debug("%s", record)` downstream would then write the ciphertext into a
    log aggregator, where it outlives the database's access controls entirely.
    """
    record = crypto.encrypt(FAKE_TOKEN)
    for form in (repr(record), str(record), f"{record}"):
        assert record.ciphertext not in form, form
        assert record.nonce not in form, form
        assert record.mac not in form, form
        assert "redacted" in form.lower(), form


def test_the_mac_mismatch_message_carries_no_material():
    """An exception message is a reliable route for a secret to reach a log."""
    record = crypto.encrypt(FAKE_TOKEN)
    with pytest.raises(crypto.MacMismatch) as caught:
        crypto.decrypt(_tamper_ciphertext(record))
    message = str(caught.value)
    assert FAKE_TOKEN not in message
    assert TEST_KEY.decode() not in message
    assert record.ciphertext not in message


def test_the_missing_key_refusal_does_not_echo_the_environment_value(monkeypatch):
    monkeypatch.setenv(crypto.MASTER_KEY_ENV, "   ")
    with pytest.raises(crypto.SecretKeyMissing) as caught:
        crypto.encrypt(FAKE_TOKEN)
    assert "never echoed" in str(caught.value)


# ──────────────────────────────────────────────────────────────────────────────
# The construction
# ──────────────────────────────────────────────────────────────────────────────

def test_a_secret_round_trips():
    assert crypto.decrypt(crypto.encrypt(FAKE_TOKEN)) == FAKE_TOKEN


@pytest.mark.parametrize(
    "plaintext",
    ["", "x", "ünïcodé-ké¥-ø", "x" * 5000, "line\nbreak\ttab"],
    ids=["empty", "single", "unicode", "long", "whitespace"],
)
def test_awkward_plaintexts_round_trip(plaintext):
    """The empty string is in here on purpose: a length-0 keystream is an easy off-by-one,
    and a secret that round-trips as "" would read as a successfully stored blank."""
    assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext


def test_encrypting_the_same_secret_twice_produces_a_different_nonce():
    """THE CATASTROPHIC CASE FOR A STREAM CIPHER.

    Two ciphertexts under one keystream XOR to the XOR of their plaintexts, so an attacker
    holding both learns their difference without the key. A nonce derived from the
    secret's NAME would repeat on every rotation -- and the two plaintexts in that case
    are the old and the new value of one credential.
    """
    first = crypto.encrypt(FAKE_TOKEN)
    second = crypto.encrypt(FAKE_TOKEN)
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext, (
        "identical ciphertext for identical plaintext means the nonce is not random"
    )
    assert first.mac != second.mac


def test_the_nonce_is_not_derived_from_the_plaintext():
    """Two different secrets must not be able to collide on a nonce either."""
    nonces = {crypto.encrypt(f"secret-{n}").nonce for n in range(25)}
    assert len(nonces) == 25, f"nonce collision in 25 encryptions: {len(nonces)} unique"


def test_a_flipped_ciphertext_byte_fails_the_mac_and_nothing_is_returned():
    record = crypto.encrypt(FAKE_TOKEN)
    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(_tamper_ciphertext(record))


def test_a_swapped_nonce_fails_the_mac():
    """The nonce is inside the MAC. Without that, two rows' nonces can be exchanged:
    both still verify and each decrypts to garbage indistinguishable from corruption."""
    first = crypto.encrypt(FAKE_TOKEN)
    second = crypto.encrypt("another-value")
    swapped = crypto.EncryptedRecord(
        nonce=second.nonce,
        ciphertext=first.ciphertext,
        mac=first.mac,
        cipher=first.cipher,
    )
    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(swapped)


def test_a_downgraded_cipher_label_is_refused_rather_than_attempted():
    """The label is inside the MAC, so a row cannot be edited to name a weaker cipher.

    Refused rather than attempted with the local cipher: guessing would turn tampering
    into a MAC error that reads like corruption.
    """
    record = crypto.encrypt(FAKE_TOKEN)
    forged = crypto.EncryptedRecord(
        nonce=record.nonce,
        ciphertext=record.ciphertext,
        mac=record.mac,
        cipher="rot13",
    )
    with pytest.raises(crypto.MacMismatch, match="cannot read"):
        crypto.decrypt(forged)


def test_a_wrong_master_key_fails_the_mac_rather_than_returning_garbage():
    """Returning garbage would let a key rotation half-succeed silently: every secret
    would 'decrypt' to noise and the first symptom would be a failed API call."""
    record = crypto.encrypt(FAKE_TOKEN)
    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(record, key=b"a-different-master-key-entirely")


def test_the_verdict_is_reached_with_compare_digest_and_not_equality():
    """`==` on bytes returns at the first differing byte, leaking match length by timing.

    Asserted over the AST rather than by grepping the source, because a comment explaining
    why compare_digest is used would satisfy a substring check while an `==` sat below it
    -- a gap CLAUDE.md records being found twice in one lane.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(crypto))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compare_digest"
    ]
    assert calls, "crypto.py does not call compare_digest anywhere"

    decrypt_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "decrypt"
    )
    comparisons = [
        node for node in ast.walk(decrypt_fn)
        if isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
    ]
    for comparison in comparisons:
        # The one permitted equality in decrypt is the cipher LABEL check, which is not
        # secret material and carries no timing signal worth having.
        source = ast.unparse(comparison)
        assert "cipher" in source, (
            f"decrypt compares secret material with ==/!= rather than "
            f"compare_digest: {source}"
        )


def test_the_stream_key_and_the_mac_key_are_independent():
    """NEVER one key for both. The two uses interact, and a MAC computed with the
    keystream key can leak keystream material."""
    nonce = b"0123456789abcdef"
    stream_key, mac_key = crypto._subkeys(TEST_KEY, nonce)
    assert stream_key != mac_key
    assert len(stream_key) == len(mac_key) == 32


def test_different_nonces_derive_different_subkeys():
    """The nonce is the scrypt salt, which is what keeps two records' keystreams apart
    even under one master key."""
    first = crypto._subkeys(TEST_KEY, b"0" * 16)
    second = crypto._subkeys(TEST_KEY, b"1" * 16)
    assert first[0] != second[0]
    assert first[1] != second[1]


def test_a_missing_master_key_raises_rather_than_defaulting(monkeypatch):
    """A default master key is a key SHARED by every deployment that forgot to set one --
    so any operator of any instance could read every other instance's secrets."""
    monkeypatch.delenv(crypto.MASTER_KEY_ENV, raising=False)
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt(FAKE_TOKEN)
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.decrypt(
            crypto.EncryptedRecord(nonce="AA==", ciphertext="AA==", mac="AA==",
                                   cipher=crypto.CIPHER_LOCAL_V1)
        )


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_a_blank_master_key_is_refused(monkeypatch, blank):
    monkeypatch.setenv(crypto.MASTER_KEY_ENV, blank)
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt(FAKE_TOKEN)


def test_the_key_is_read_at_call_time_and_not_bound_at_import(monkeypatch):
    """CLAUDE.md's standing trap: a value bound at import ignores both the tests and the
    deployed environment, silently."""
    first = crypto.encrypt(FAKE_TOKEN)
    monkeypatch.setenv(crypto.MASTER_KEY_ENV, "a-rotated-master-key-value")
    with pytest.raises(crypto.MacMismatch):
        crypto.decrypt(first)


def test_the_record_fields_match_the_secret_tables_columns_exactly():
    """No mapping layer, so nothing can drift. A renamed column would otherwise be found
    by an INSERT failing at runtime."""
    import dataclasses

    from agentorg.db import schema

    fields = {f.name for f in dataclasses.fields(crypto.EncryptedRecord)}
    columns = {c.name for c in schema.TABLES_BY_NAME["secret"].columns}
    assert fields <= columns, f"EncryptedRecord has fields the table lacks: "\
                              f"{fields - columns}"
    assert fields == {"nonce", "ciphertext", "mac", "cipher"}


def test_the_cipher_label_is_recorded_so_a_downgrade_is_visible_in_the_data():
    """Mirrors SecurityResult.scan_provenance: without the label, a silent downgrade from
    a KMS-backed cipher to the local one is indistinguishable from correct operation."""
    assert crypto.encrypt(FAKE_TOKEN).cipher == crypto.CIPHER_LOCAL_V1
    assert "local" in crypto.CIPHER_LOCAL_V1, (
        "the label should say it is the local cipher, not merely version it"
    )


def test_this_module_does_not_import_cryptography():
    """MEASURED: `cryptography` is not in the declared dependency closure -- PyJWT
    requires it only under an extra, and CI installs `.[dev]` and nothing else. So an
    import here works locally and fails in CI, and this lane does not own pyproject.toml.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(crypto))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "cryptography" not in imported, (
        "crypto.py imports `cryptography`, which is absent from the declared "
        "dependency closure and so present locally and missing in CI"
    )
