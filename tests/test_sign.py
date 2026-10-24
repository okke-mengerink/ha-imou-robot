"""Plain-Python test: request signing against Imou's documented test vector.

Run: python3 -I tests/test_sign.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import load, run_all  # noqa: E402

api = load("api")

VECTOR_SECRET = "test123456789test123456789"
VECTOR_TIME = 1706511734
VECTOR_NONCE = "f5a1ae2d-c09c-4d39-a744-83a5c2c653c2"
VECTOR_SIGN = "xjhCQBoJ9hRDsCjyDcHjtDNzRZ3ZJezcawsfWeiaoxU="


def test_sign_test_vector() -> None:
    assert api.sign(VECTOR_SECRET, VECTOR_TIME, VECTOR_NONCE) == VECTOR_SIGN


def test_request_body_shape() -> None:
    body = api.build_request_body(
        "app-id",
        VECTOR_SECRET,
        {"page": 1},
        timestamp=VECTOR_TIME,
        nonce=VECTOR_NONCE,
        request_id="req-1",
    )
    assert body == {
        "system": {
            "ver": "1.0",
            "appId": "app-id",
            "sign": VECTOR_SIGN,
            "time": VECTOR_TIME,
            "nonce": VECTOR_NONCE,
        },
        "id": "req-1",
        "params": {"page": 1},
    }


def test_nonce_and_id_are_fresh() -> None:
    first = api.build_request_body("a", "b", {})
    second = api.build_request_body("a", "b", {})
    assert first["system"]["nonce"] != second["system"]["nonce"]
    assert first["id"] != second["id"]


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
