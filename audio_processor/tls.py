from __future__ import annotations

import hashlib
import os
import ssl
from pathlib import Path


def ensure_python_https_trust(bundle_dir: Path) -> Path | None:
    """Prepare a Python CA bundle that also trusts Windows certificate stores."""

    existing = _existing_ca_bundle()
    if existing is not None:
        return existing

    base_bundle = _certifi_bundle_bytes()
    windows_certs = _windows_certificate_entries()
    if not base_bundle and not windows_certs:
        return None

    try:
        bundle_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    bundle_path = bundle_dir / "python-ca-bundle.pem"
    chunks: list[bytes] = []
    if base_bundle:
        chunks.append(base_bundle.rstrip() + b"\n")

    seen: set[str] = set()
    for cert_der in windows_certs:
        digest = hashlib.sha256(cert_der).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        chunks.append(ssl.DER_cert_to_PEM_cert(cert_der).encode("ascii"))

    try:
        temp_path = bundle_path.with_suffix(".tmp")
        temp_path.write_bytes(b"\n".join(chunks))
        temp_path.replace(bundle_path)
    except OSError:
        return None

    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(bundle_path))
    os.environ.setdefault("SSL_CERT_FILE", str(bundle_path))
    os.environ.setdefault("CURL_CA_BUNDLE", str(bundle_path))
    return bundle_path


def _existing_ca_bundle() -> Path | None:
    for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        raw = os.environ.get(name)
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.exists():
            return path
    return None


def _certifi_bundle_bytes() -> bytes:
    try:
        import certifi  # type: ignore
    except Exception:
        return b""

    try:
        return Path(certifi.where()).read_bytes()
    except OSError:
        return b""


def _windows_certificate_entries() -> tuple[bytes, ...]:
    enum_certificates = getattr(ssl, "enum_certificates", None)
    if enum_certificates is None:
        return ()

    entries: list[bytes] = []
    for store_name in ("ROOT", "CA"):
        try:
            certificates = enum_certificates(store_name)
        except Exception:
            continue
        for certificate, encoding_type, _trust in certificates:
            if encoding_type == "x509_asn":
                entries.append(certificate)
    return tuple(entries)
