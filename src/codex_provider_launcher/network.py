"""HTTPS transport for source and portable builds, without global SSL changes."""
from __future__ import annotations

import os
import ssl
import sys
import urllib.request

import certifi


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authenticated API requests must not silently send keys to another URL.
        return None


def tls_context() -> ssl.SSLContext:
    """Use native trust on Mac/Windows plus a portable public CA bundle."""
    if sys.platform in ("darwin", "win32"):
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context = ssl.create_default_context()
    # A frozen app cannot depend on the Python installation/CA paths on its builder.
    context.load_verify_locations(cafile=certifi.where())
    # Preserve explicitly configured enterprise trust without disabling verification.
    cafile, capath = os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR")
    if cafile or capath:
        context.load_verify_locations(cafile=cafile or None, capath=capath or None)
    context.set_alpn_protocols(["http/1.1"])
    return context


def build_api_opener() -> urllib.request.OpenerDirector:
    try:
        context = tls_context()
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法加载 HTTPS 证书库，请检查安装包及 SSL_CERT_FILE / SSL_CERT_DIR 设置。") from exc
    return urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))


def https_self_test(report: str) -> None:
    """Public, keyless probe for the built executable on each target platform."""
    import json
    from pathlib import Path
    context = tls_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    request = urllib.request.Request("https://www.python.org/", headers={"User-Agent": "Codex-Easy-Switch-HTTPS-Check"})
    with build_api_opener().open(request, timeout=30) as response:
        assert response.status == 200
        response.read(1)
    Path(report).write_text(json.dumps({"ok": True, "https": True, "certificate_verification": True,
                                        "native_trust": sys.platform in ("darwin", "win32"),
                                        "bundled_ca": Path(certifi.where()).is_file()}), encoding="utf-8")
