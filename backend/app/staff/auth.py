"""Staff authentication: OIDC single sign-on (Google Workspace, Microsoft Entra ID,
or any OpenID Connect provider) and our own short-lived session token.

Flow: /api/auth/login redirects to the IdP with PKCE + state + nonce kept in a
signed, HttpOnly cookie; /api/auth/callback exchanges the code, verifies the
ID token signature against the IdP's JWKS, maps the email to a staff account
and issues the `toktok_staff` session cookie.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

SESSION_COOKIE = "toktok_staff"
LOGIN_COOKIE = "toktok_oidc"


class AuthError(Exception):
    pass


@dataclass
class Principal:
    email: str
    roles: list[str]
    permissions: set[str]
    via: str  # "sso" | "dev" | "admin-key"

    def can(self, perm: str) -> bool:
        return perm in self.permissions


class Signer:
    """HS256 tokens for our own cookies (session + in-flight login state)."""

    def __init__(self, secret: str):
        self.secret = secret

    def sign(self, claims: dict[str, Any], ttl_seconds: int, purpose: str) -> str:
        now = int(time.time())
        return jwt.encode({**claims, "iat": now, "exp": now + ttl_seconds, "pur": purpose}, self.secret, "HS256")

    def verify(self, token: str, purpose: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"])
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid token: {e}") from e
        if claims.get("pur") != purpose:
            raise AuthError("token purpose mismatch")
        return claims


class OIDCClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, redirect_uri: str,
                 scopes: str = "openid email profile", transport: httpx.AsyncBaseTransport | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self._transport = transport
        self._meta: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_at = 0.0

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transport)

    async def metadata(self) -> dict[str, Any]:
        if self._meta is None:
            async with self._http() as c:
                r = await c.get(f"{self.issuer}/.well-known/openid-configuration")
                r.raise_for_status()
                self._meta = r.json()
        return self._meta

    async def start(self) -> tuple[str, dict[str, str]]:
        """Returns (authorize URL, login state to keep in a signed cookie)."""
        meta = await self.metadata()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        params = {
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": self.scopes, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}", {"state": state, "nonce": nonce, "cv": verifier}

    async def finish(self, code: str, login: dict[str, str]) -> dict[str, Any]:
        """Exchange the code and return verified ID-token claims."""
        meta = await self.metadata()
        async with self._http() as c:
            r = await c.post(meta["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                "client_id": self.client_id, "client_secret": self.client_secret, "code_verifier": login["cv"],
            })
        if r.status_code != 200:
            raise AuthError(f"token exchange failed: {r.status_code}")
        id_token = r.json().get("id_token")
        if not id_token:
            raise AuthError("no id_token in token response")
        claims = await self._verify_id_token(id_token, meta)
        if claims.get("nonce") != login["nonce"]:
            raise AuthError("nonce mismatch")
        return claims

    async def _verify_id_token(self, token: str, meta: dict[str, Any]) -> dict[str, Any]:
        kid = jwt.get_unverified_header(token).get("kid")
        key = await self._key(kid, meta)
        try:
            return jwt.decode(
                token, key, algorithms=["RS256", "ES256"], audience=self.client_id,
                # Microsoft's multi-tenant metadata uses a {tenantid} template; pin a tenant issuer instead.
                issuer=meta.get("issuer", self.issuer),
            )
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid id_token: {e}") from e

    async def _key(self, kid: str | None, meta: dict[str, Any]):
        for refresh in (False, True):
            if refresh or self._jwks is None or time.time() - self._jwks_at > 3600:
                async with self._http() as c:
                    r = await c.get(meta["jwks_uri"])
                    r.raise_for_status()
                    self._jwks, self._jwks_at = r.json(), time.time()
            for k in self._jwks.get("keys", []):
                if kid is None or k.get("kid") == kid:
                    return jwt.PyJWK(k).key
        raise AuthError("signing key not found")


def email_from_claims(claims: dict[str, Any]) -> str | None:
    """Google: email (+email_verified). Microsoft Entra: email or preferred_username."""
    if claims.get("email_verified") is False:
        return None
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
    return email.lower() if isinstance(email, str) and "@" in email else None
