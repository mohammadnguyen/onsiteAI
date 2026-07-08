"""PR 6 — Timeline attachment storage: presign issuance + config gate.

Two halves:

* **Config gate** — the four storage vars are required in any non-dev
  environment (missing/partial → fail-fast naming ONLY the missing var
  names, never a value); development constructs fine without them.
  Explicit ``None`` kwargs override the conftest-seeded env vars so
  the failure paths are exercisable inside the suite.
* **Service** — boto3 fully mocked (nothing reaches a network): client
  construction params (Tigris endpoint, s3v4, virtual addressing),
  presigned PUT/GET call shapes (bucket/key/content-type/expiry),
  client caching + reset hook, the dev-mode ``StorageNotConfigured``
  path, and storage-key sanitisation.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

import app.services.timeline_storage as storage
from app.config import Settings

_VALID_SECRET = "x" * 64
_VALID_DB_URL = "postgresql+asyncpg://u:p@localhost:5432/db"
_VALID_ORIGIN = "https://admin.example.com"

_STORAGE_KWARGS = {
    "storage_endpoint_url": "https://fly.storage.tigris.dev",
    "storage_bucket_name": "sitetracker-photos",
    "storage_access_key_id": "tid_access",
    "storage_secret_access_key": "tsec_secret_value",
}

_NO_STORAGE_KWARGS = {
    "storage_endpoint_url": None,
    "storage_bucket_name": None,
    "storage_access_key_id": None,
    "storage_secret_access_key": None,
}


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "production",
        "database_url": _VALID_DB_URL,
        "jwt_secret": _VALID_SECRET,
        "cors_allowed_origins": [_VALID_ORIGIN],
        **_STORAGE_KWARGS,
    }
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------------- #
# Config gate                                                                  #
# --------------------------------------------------------------------------- #
def test_production_missing_all_storage_vars_fails_fast():
    with pytest.raises(ValidationError) as exc:
        _settings(**_NO_STORAGE_KWARGS)
    msg = str(exc.value)
    assert "Attachment storage is not configured" in msg
    for name in (
        "AWS_ENDPOINT_URL_S3",
        "BUCKET_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert name in msg


def test_production_partial_storage_config_names_only_missing():
    with pytest.raises(ValidationError) as exc:
        _settings(storage_bucket_name=None)
    msg = str(exc.value)
    assert "BUCKET_NAME" in msg
    assert "AWS_ENDPOINT_URL_S3" not in msg  # present vars are not listed


def test_gate_error_never_leaks_secret_value(monkeypatch, tmp_path):
    """The failure path the app actually runs (get_settings ->
    SettingsValidationError) must not surface any fragment of the
    storage secret.

    Deliberately NOT asserted on a raw ``Settings(...)`` ValidationError:
    pydantic's own repr embeds a truncated ``input_value=`` dict that can
    include the secret's tail — the codebase's defence is the
    ``get_settings`` wrapper (same design as the JWT-secret leak guard in
    test_config.py), so that is the boundary this test pins, with the
    same sliding-window rigour.
    """
    from app.config import SettingsValidationError, get_settings

    sentinel = "super-secret-storage-key-DO-NOT-LEAK-0123456789"
    monkeypatch.chdir(tmp_path)  # no stray .env files
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", _VALID_DB_URL)
    monkeypatch.setenv("JWT_SECRET", _VALID_SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", _VALID_ORIGIN)
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://fly.storage.tigris.dev")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "tid_access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", sentinel)
    monkeypatch.delenv("BUCKET_NAME", raising=False)  # trip the gate
    get_settings.cache_clear()
    try:
        with pytest.raises(SettingsValidationError) as exc:
            get_settings()
        msg = str(exc.value)
        assert "BUCKET_NAME" in msg
        assert sentinel not in msg
        # Guard against substrings (any 10-char window) leaking, same
        # rigour as the JWT leak test.
        for i in range(0, len(sentinel) - 10):
            assert sentinel[i : i + 10] not in msg
    finally:
        get_settings.cache_clear()  # next caller re-resolves suite env


def test_blank_storage_value_counts_as_missing():
    with pytest.raises(ValidationError) as exc:
        _settings(storage_bucket_name="   ")
    assert "BUCKET_NAME" in str(exc.value)


def test_staging_enforces_storage_gate_too():
    with pytest.raises(ValidationError):
        _settings(app_env="staging", **_NO_STORAGE_KWARGS)


def test_development_boots_without_storage():
    s = _settings(app_env="development", **_NO_STORAGE_KWARGS)
    assert s.storage_is_configured is False


def test_configured_settings_report_true():
    s = _settings()
    assert s.storage_is_configured is True
    assert s.storage_presign_expiry_seconds == 600  # 10-minute default


def test_presign_expiry_bounds():
    with pytest.raises(ValidationError):
        _settings(storage_presign_expiry_seconds=10)  # below 60s floor
    with pytest.raises(ValidationError):
        _settings(storage_presign_expiry_seconds=999_999)  # above 1h cap


# --------------------------------------------------------------------------- #
# Service (boto3 fully mocked)                                                 #
# --------------------------------------------------------------------------- #
class _StubS3Client:
    def __init__(self):
        self.calls: list[dict] = []

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        self.calls.append(
            {
                "method": ClientMethod,
                "params": Params,
                "expires_in": ExpiresIn,
            }
        )
        return f"https://signed.example/{Params['Key']}?sig=stub"


@pytest.fixture
def stub_boto3(monkeypatch):
    """Replace boto3.client with a capturing stub; reset the singleton."""
    stub = _StubS3Client()
    captured: dict = {}

    def fake_client(service_name, **kwargs):
        captured["service"] = service_name
        captured["kwargs"] = kwargs
        return stub

    monkeypatch.setattr(storage.boto3, "client", fake_client)
    storage.reset_client_cache()
    yield stub, captured
    storage.reset_client_cache()


def test_client_built_with_tigris_requirements(stub_boto3):
    """s3v4 + virtual addressing + endpoint/creds from settings."""
    stub, captured = stub_boto3
    storage.generate_presigned_put("jobs/x/timeline/y/z.jpg", "image/jpeg")

    assert captured["service"] == "s3"
    kwargs = captured["kwargs"]
    # Endpoint + creds come from the conftest-seeded test settings.
    assert kwargs["endpoint_url"] == "https://storage.test.invalid"
    assert kwargs["aws_access_key_id"] == "test-storage-access-key"
    assert kwargs["aws_secret_access_key"] == "test-storage-secret-key-never-real"
    # Pinned region: keeps the SigV4 credential scope deterministic
    # instead of inheriting AWS_REGION / ~/.aws/config from the host.
    assert kwargs["region_name"] == "auto"
    cfg = kwargs["config"]
    assert cfg.signature_version == "s3v4"
    assert cfg.s3 == {"addressing_style": "virtual"}


def test_presigned_put_call_shape(stub_boto3):
    stub, _ = stub_boto3
    url = storage.generate_presigned_put("jobs/a/timeline/b/c.jpg", "image/jpeg")

    assert url.startswith("https://signed.example/")
    assert stub.calls == [
        {
            "method": "put_object",
            "params": {
                "Bucket": "sitetracker-test-bucket",
                "Key": "jobs/a/timeline/b/c.jpg",
                "ContentType": "image/jpeg",
            },
            "expires_in": 600,
        }
    ]


def test_presigned_get_call_shape(stub_boto3):
    stub, _ = stub_boto3
    storage.generate_presigned_get("jobs/a/timeline/b/c.jpg")

    assert stub.calls == [
        {
            "method": "get_object",
            "params": {
                "Bucket": "sitetracker-test-bucket",
                "Key": "jobs/a/timeline/b/c.jpg",
            },
            "expires_in": 600,
        }
    ]


def test_client_is_cached_and_reset_hook_works(stub_boto3, monkeypatch):
    stub, captured = stub_boto3
    storage.generate_presigned_put("k1", "image/jpeg")
    storage.generate_presigned_get("k2")
    # Two presign calls, one client construction.
    assert len(stub.calls) == 2
    first_kwargs = captured["kwargs"]

    calls = {"n": 0}

    def counting_client(service_name, **kwargs):
        calls["n"] += 1
        return stub

    monkeypatch.setattr(storage.boto3, "client", counting_client)
    storage.generate_presigned_get("k3")
    assert calls["n"] == 0  # cached client still in use

    storage.reset_client_cache()
    storage.generate_presigned_get("k4")
    assert calls["n"] == 1  # rebuilt after reset
    assert first_kwargs is not None  # sanity: original construction captured


def test_storage_not_configured_raises_in_dev(monkeypatch):
    dev = _settings(app_env="development", **_NO_STORAGE_KWARGS)
    monkeypatch.setattr(storage, "get_settings", lambda: dev)
    storage.reset_client_cache()
    try:
        with pytest.raises(storage.StorageNotConfigured):
            storage.generate_presigned_put("k", "image/jpeg")
    finally:
        storage.reset_client_cache()


# --------------------------------------------------------------------------- #
# Storage-key builder                                                          #
# --------------------------------------------------------------------------- #
def test_build_storage_key_shape_and_uniqueness():
    job_id, item_id = uuid.uuid4(), uuid.uuid4()
    k1 = storage.build_storage_key(job_id, item_id, "photo.jpg")
    k2 = storage.build_storage_key(job_id, item_id, "photo.jpg")

    assert k1.startswith(f"jobs/{job_id}/timeline/{item_id}/")
    assert k1.endswith("-photo.jpg")
    assert k1 != k2  # uuid prefix: same filename never collides
    assert len(k1) <= 512  # storage_key column bound


@pytest.mark.parametrize(
    "hostile,expected_suffix",
    [
        ("../../etc/passwd", "-passwd"),          # path stripped to basename
        ("..\\..\\boot.ini", "-boot.ini"),        # windows separators too
        ("工地 照片.jpg", "-jpg"),                  # CJK + space collapse
        ("a b?c*d.png", "-a-b-c-d.png"),
    ],
)
def test_build_storage_key_sanitises_hostile_filenames(hostile, expected_suffix):
    key = storage.build_storage_key(uuid.uuid4(), uuid.uuid4(), hostile)
    filename_part = key.rsplit("/", 1)[-1]
    assert "/" not in filename_part
    assert ".." not in key
    assert key.endswith(expected_suffix)


def test_build_storage_key_empty_after_sanitise_falls_back():
    key = storage.build_storage_key(uuid.uuid4(), uuid.uuid4(), "///")
    assert key.endswith("-file")


def test_build_storage_key_caps_filename_length():
    long_name = "a" * 500 + ".jpg"
    key = storage.build_storage_key(uuid.uuid4(), uuid.uuid4(), long_name)
    filename_part = key.rsplit("/", 1)[-1].split("-", 1)[1]
    assert len(filename_part) <= 80
    assert len(key) <= 512
