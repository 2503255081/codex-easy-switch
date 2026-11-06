"""Real TLS handshakes: trusted CA works; wrong host and untrusted CA fail."""
import datetime
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from codex_provider_launcher import catalog, network
from codex_provider_launcher.profiles import new_profile


class HttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ephemeral launcher test CA")])
        now = datetime.datetime.now(datetime.timezone.utc)
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
              .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
              .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),critical=False).sign(ca_key,hashes.SHA256()))
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"localhost")]))
                .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
                .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]),critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
                .sign(ca_key,hashes.SHA256()))
        cls.ca = root/'ca.pem';cls.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (root/'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cls.seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def handle(self):
                try: super().handle()
                except (ConnectionResetError, ssl.SSLEOFError): pass
            def do_GET(self):
                cls.seen.append((self.path,self.headers.get('Authorization')))
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','https://other.invalid/v1/models');self.end_headers();return
                raw=json.dumps({'data':[{'id':'fixture-model'}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'server.pem',root/'key.pem')
        cls.server.socket=context.wrap_socket(cls.server.socket,server_side=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()

    def test_model_discovery_over_tls_with_explicit_trust(self):
        p=new_profile('fixture','responses',f'https://localhost:{self.server.server_port}/v1','auto')
        with patch.dict(os.environ,{'SSL_CERT_FILE':str(self.ca),'SSL_CERT_DIR':'','NO_PROXY':'localhost,127.0.0.1'}):
            models,native=catalog.list_models(p,'fixture-only-key',Path('unused'),opencodex_header=False)
        self.assertEqual([m for m,_ in models],['fixture-model'])
        self.assertEqual(self.seen[-1],('/v1/models','Bearer fixture-only-key'))

    def test_untrusted_and_wrong_hostname_rejected_before_headers(self):
        for host,trusted in [('localhost',False),('127.0.0.1',True)]:
            before=len(self.seen)
            with self.subTest(host=host), patch.dict(os.environ,{'SSL_CERT_FILE':str(self.ca) if trusted else '',
                                                                'SSL_CERT_DIR':'','NO_PROXY':'localhost,127.0.0.1'}):
                req=urllib.request.Request(f'https://{host}:{self.server.server_port}/v1/models',headers={'Authorization':'Bearer fixture-only-key'})
                with self.assertRaises((urllib.error.URLError,ssl.SSLError)):
                    network.build_api_opener().open(req,timeout=5)
            self.assertEqual(len(self.seen),before)

    def test_authenticated_redirect_not_followed(self):
        with patch.dict(os.environ,{'SSL_CERT_FILE':str(self.ca),'SSL_CERT_DIR':'','NO_PROXY':'localhost,127.0.0.1'}):
            req=urllib.request.Request(f'https://localhost:{self.server.server_port}/redirect',headers={'Authorization':'Bearer fixture-only-key'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                network.build_api_opener().open(req,timeout=5)
            self.assertEqual(error.exception.code,302)
            self.assertIn('重定向',catalog.connection_error(error.exception))
            error.exception.close()

    def test_certificate_error_message_keeps_private_address_out(self):
        exc=urllib.error.URLError(ssl.SSLCertVerificationError(1,'private-relay.invalid fixture-secret'))
        message=catalog.connection_error(exc)
        self.assertIn('证书校验',message)
        self.assertNotIn('private-relay',message)
        self.assertNotIn('fixture-secret',message)
