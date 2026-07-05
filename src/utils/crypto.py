import hashlib
import os

try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore
except Exception:
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore


def _load_keyring_from_env() -> tuple[int, dict[int, "Fernet"]]:
    """Load keyring from env. Example:
    KEY_VERSION=1
    KEYRING_1=<base64_fernet_key>
    """
    current = int(os.getenv("KEY_VERSION", "1"))
    keyring: dict[int, Fernet] = {}
    # If cryptography is not available, fall back to no-op encryption
    if Fernet is None:
        return current, {}
    for k, v in os.environ.items():
        if k.startswith("KEYRING_") and v:
            try:
                ver = int(k.split("_", 1)[1])
                keyring[ver] = Fernet(v.encode("utf-8"))
            except Exception:
                continue
    # If no keyring configured, use empty dict to signal no-op encryption
    if current not in keyring and len(keyring) == 0:
        return current, {}
    # If some keys exist but not the current, pick the smallest available version
    if current not in keyring and len(keyring) > 0:
        current = sorted(keyring.keys())[0]
    return current, keyring


_CURRENT_VERSION, _KEYRING = _load_keyring_from_env()


def encrypt_api_key(plaintext: str) -> tuple[str, int]:
    """Encrypt API key. Requires encryption to be properly configured.

    Raises RuntimeError if encryption keys are not configured, ensuring
    API keys are never stored in plaintext.
    """
    if Fernet is None:
        raise RuntimeError(
            "Cryptography library not available. Cannot encrypt API keys. "
            "Install cryptography package to continue."
        )

    f = _KEYRING.get(_CURRENT_VERSION) if isinstance(_KEYRING, dict) else None
    if f is None:
        raise RuntimeError(
            "No encryption keys configured. Set KEY_VERSION and KEYRING_<version> "
            "environment variables to enable API key encryption."
        )

    try:
        token = f.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8"), _CURRENT_VERSION
    except Exception as e:
        raise RuntimeError(f"Failed to encrypt API key: {str(e)}") from e


def decrypt_api_key(token: str, key_version: int | None = None) -> str:
    """Decrypt a token produced by ``encrypt_api_key``.

    Reversible counterpart used where the plaintext key must be recovered for
    outbound use (e.g. BYOK: calling an upstream provider with a customer's own
    key). API-key auth does NOT use this — it matches by hash. Tries the given
    key_version first, then every keyring entry (supports key rotation).

    Raises RuntimeError if encryption is unavailable and ValueError if the
    token cannot be decrypted with any configured key.
    """
    if Fernet is None:
        raise RuntimeError("Cryptography library not available. Cannot decrypt.")
    if not isinstance(_KEYRING, dict) or not _KEYRING:
        raise RuntimeError("No encryption keys configured. Set KEY_VERSION and KEYRING_<version>.")

    raw = token.encode("utf-8")
    versions = []
    if key_version is not None and key_version in _KEYRING:
        versions.append(key_version)
    versions.extend(v for v in _KEYRING if v not in versions)

    for ver in versions:
        try:
            return _KEYRING[ver].decrypt(raw).decode("utf-8")
        except InvalidToken:
            continue
        except Exception:
            continue
    raise ValueError("Failed to decrypt token with any configured key.")


def sha256_key_hash(plaintext: str) -> str:
    """Deterministic hash for lookup/rate limiting. Requires configured salt via env.

    Raises RuntimeError if KEY_HASH_SALT is not configured, preventing weak hashing.
    """
    salt = os.getenv("KEY_HASH_SALT", "")
    if not salt or len(salt) < 16:
        raise RuntimeError(
            "KEY_HASH_SALT must be configured with at least 16 characters. "
            "Generate a strong random salt and set it in your environment."
        )

    h = hashlib.sha256()
    h.update(salt.encode("utf-8"))
    h.update(plaintext.encode("utf-8"))
    return h.hexdigest()


def last4(plaintext: str) -> str:
    return plaintext[-4:] if len(plaintext) >= 4 else plaintext
