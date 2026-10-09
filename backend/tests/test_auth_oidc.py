"""OIDC SSO against a fake identity provider that signs real RS256 ID tokens."""

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import get_settings
from app.staff.auth import OIDCClient

ISSUER = "https://idp.test"


class FakeIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]}
        self.claims: dict = {}
        self.nonce = None
        self.code_verifier = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})
        if url.endswith("/jwks"):
            return httpx.Response(200, json=self.jwks)
        if url.endswith("/token"):
            form = parse_qs(request.content.decode())
            self.code_verifier = form["code_verifier"][0]
            now = int(time.time())
            token = jwt.encode({"iss": ISSUER, "aud": "client-1", "iat": now, "exp": now + 300,
                                "nonce": self.nonce, **self.claims}, self.key, "RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": token, "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
def setup(monkeypatch):
    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "OIDC_ISSUER": ISSUER,
                 "OIDC_CLIENT_ID": "client-1", "OIDC_CLIENT_SECRET": "secret",
                 "OIDC_ALLOWED_DOMAINS": '["toktok.mn"]', "STAFF_BOOTSTRAP_ADMINS": '["boss@toktok.mn"]',
                 "STAFF_SESSION_SECRET": "x" * 40, "FRONTEND_BASE_URL": "http://front.test"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    from app.main import create_app

    idp = FakeIdP()
    with TestClient(create_app()) as client:
        c = client.app.state.container
        c.oidc = OIDCClient(ISSUER, "client-1", "secret", "http://testserver/api/auth/callback",
                            transport=httpx.MockTransport(idp.handler))
        yield client, idp
    get_settings.cache_clear()


def sso(client, idp, claims, return_to="/admin"):
    r = client.get("/api/auth/login", params={"return_to": return_to}, follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["client-1"]
    idp.nonce, idp.claims = q["nonce"][0], claims
    return client.get("/api/auth/callback", params={"code": "c0de", "state": q["state"][0]}, follow_redirects=False)


def test_bootstrap_admin_logs_in_and_manages_staff(setup):
    client, idp = setup
    r = sso(client, idp, {"email": "boss@toktok.mn", "email_verified": True, "name": "Boss"}, "/operator")
    assert r.status_code == 302 and r.headers["location"] == "http://front.test/operator"
    me = client.get("/api/auth/me").json()
    assert me["email"] == "boss@toktok.mn" and "staff.manage" in me["permissions"] and me["via"] == "sso"
    assert idp.code_verifier  # PKCE verifier sent to the token endpoint

    h = {"X-Requested-With": "toktok-admin"}
    assert client.put("/api/admin/staff/ana@toktok.mn", json={"roles": ["editor"]}, headers=h).status_code == 200
    assert client.put("/api/admin/staff/boss@toktok.mn", json={"roles": ["editor"]}, headers=h).status_code == 400
    staff = {u["email"]: u for u in client.get("/api/admin/staff").json()}
    assert staff["ana@toktok.mn"]["roles"] == ["editor"]

    # A registered editor can sign in; permissions follow the role.
    client.post("/api/auth/logout")
    sso(client, idp, {"email": "ana@toktok.mn", "email_verified": True})
    me = client.get("/api/auth/me").json()
    assert me["roles"] == ["editor"] and "kb.publish" not in me["permissions"]
    assert client.get("/api/admin/staff").status_code == 403


@pytest.mark.parametrize("claims,error", [
    ({"email": "someone@gmail.com", "email_verified": True}, "domain_not_allowed"),
    ({"email": "new@toktok.mn", "email_verified": True}, "not_registered"),
    ({"email": "boss@toktok.mn", "email_verified": False}, "domain_not_allowed"),
])
def test_login_rejections(setup, claims, error):
    client, idp = setup
    r = sso(client, idp, claims)
    assert r.headers["location"].endswith(f"login_error={error}")
    assert client.get("/api/auth/me").status_code == 401


def test_state_and_nonce_are_enforced(setup):
    client, idp = setup
    r = client.get("/api/auth/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    idp.nonce, idp.claims = q["nonce"][0], {"email": "boss@toktok.mn"}
    bad = client.get("/api/auth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert bad.headers["location"].endswith("verification_failed")
    idp.nonce = "replayed"
    bad = client.get("/api/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert bad.headers["location"].endswith("verification_failed")


def test_dev_login_disabled_in_prod_settings():
    from app.config import Settings

    assert not Settings(_env_file=None, env="prod").dev_login_enabled
    assert Settings(_env_file=None, env="dev").dev_login_enabled
