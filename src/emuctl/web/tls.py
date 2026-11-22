# SPDX-License-Identifier: GPL-2.0-only
"""The daemon's certificate.

Unless the admin names one, the daemon makes a self-signed certificate on its
first start with the `openssl` program, so emuctl needs no crypto library.
"""
import hashlib
import os
import shutil
import socket
import ssl
import subprocess


class CertError(Exception):
    pass


def names_for(hostname):
    short = hostname.split(".")[0]
    return sorted({hostname, short, f"{short}.local", "localhost"})


def make_self_signed(cert, key, hostname=None, run=subprocess.run):
    """Write a new key and certificate. The key is mode 0600 from the start."""
    if not shutil.which("openssl"):
        raise CertError("the openssl program is needed to make a certificate "
                        "(apt install openssl), or set tls_cert and tls_key")
    hostname = hostname or socket.gethostname()
    san = ",".join(f"DNS:{n}" for n in names_for(hostname)) + ",IP:127.0.0.1,IP:::1"
    os.makedirs(os.path.dirname(cert), exist_ok=True)
    tmp_key, tmp_cert = key + ".new", cert + ".new"
    old = os.umask(0o077)
    try:
        r = run(["openssl", "req", "-x509", "-newkey", "ec",
                 "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "3650",
                 "-subj", f"/CN={hostname}", "-addext", f"subjectAltName={san}",
                 # A server certificate only: never a CA, even if someone trusts it.
                 "-addext", "basicConstraints=critical,CA:FALSE",
                 "-addext", "keyUsage=critical,digitalSignature",
                 "-addext", "extendedKeyUsage=serverAuth",
                 "-keyout", tmp_key, "-out", tmp_cert],
                capture_output=True, text=True, timeout=60)
    finally:
        os.umask(old)
    if r.returncode != 0:
        for p in (tmp_key, tmp_cert):
            if os.path.exists(p):
                os.unlink(p)
        raise CertError(f"openssl failed: {r.stderr.strip()[-300:]}")
    os.chmod(tmp_key, 0o600)
    os.chmod(tmp_cert, 0o644)
    os.replace(tmp_key, key)
    os.replace(tmp_cert, cert)


def ensure_cert(conf, hostname=None, run=subprocess.run):
    """Make the self-signed certificate if it is missing. True if one was made.

    A certificate the admin named must exist; it is never replaced.
    """
    cert, key = conf.cert_path, conf.key_path
    if os.path.exists(cert) and os.path.exists(key):
        return False
    if not conf.self_signed:
        raise CertError(f"tls_cert {cert} or tls_key {key} does not exist")
    make_self_signed(cert, key, hostname, run)
    return True


def server_context(cert, key):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as exc:
        raise CertError(f"cannot load {cert} and {key}: {exc}")
    return ctx


def fingerprint(cert):
    """The certificate's SHA-256 fingerprint, as browsers show it."""
    with open(cert) as fh:
        der = ssl.PEM_cert_to_DER_cert(fh.read())
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))
