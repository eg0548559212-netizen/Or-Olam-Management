"""הצפנת שדות רגישים (D-102, D-106). המפתח נמצא רק במשתנה הסביבה OR_OLAM_DB_KEY."""
import json
import os

from cryptography.fernet import Fernet

ENV_KEY = "OR_OLAM_DB_KEY"


def _fernet():
    key = os.environ.get(ENV_KEY)
    if not key:
        raise SystemExit(
            f"חסר משתנה הסביבה {ENV_KEY}. ליצירת מפתח: python3 -m app.crypto"
        )
    return Fernet(key.encode())


def encrypt(value):
    """מצפין ערך כלשהו (מקודד כ-JSON). מחזיר None עבור None."""
    if value is None:
        return None
    return _fernet().encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def decrypt(token):
    if token is None:
        return None
    return json.loads(_fernet().decrypt(token.encode()))


if __name__ == "__main__":
    print(Fernet.generate_key().decode())
