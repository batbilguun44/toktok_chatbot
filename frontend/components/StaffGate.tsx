"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useState } from "react";
import { ApiError, AuthConfig, authApi, Me } from "@/lib/api";

const LOGIN_ERRORS: Record<string, string> = {
  domain_not_allowed: "Энэ и-мэйлийн домэйнээр нэвтрэх эрхгүй.",
  not_registered: "Таны бүртгэл системд алга. Админд хандана уу.",
  verification_failed: "Нэвтрэлт баталгаажсангүй. Дахин оролдоно уу.",
};

/** Renders children only for a signed-in staff member holding `permission`. */
export default function StaffGate({
  permission,
  title,
  returnTo,
  children,
}: {
  permission: string;
  title: string;
  returnTo: string;
  children: (me: Me, logout: () => void) => ReactNode;
}) {
  const [me, setMe] = useState<Me | null>(null);
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [devEmail, setDevEmail] = useState("admin@toktok.mn");
  const [devRole, setDevRole] = useState("admin");
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setMe(await authApi.me());
    } catch (e) {
      setMe(null);
      if (!(e instanceof ApiError && e.status === 401)) setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    authApi.config().then(setCfg).catch(() => setCfg(null));
    const code = new URLSearchParams(window.location.search).get("login_error");
    if (code) setError(LOGIN_ERRORS[code] ?? code);
    refresh();
  }, [refresh]);

  async function devLogin(e: FormEvent) {
    e.preventDefault();
    try {
      await authApi.devLogin(devEmail, [devRole]);
      setError(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  async function passwordLogin(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await authApi.passwordLogin(loginId.trim(), password);
      setPassword("");
      setError(null);
      await refresh();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(status === 401 ? "Нэвтрэх нэр эсвэл нууц үг буруу байна."
        : status === 429 ? "Хэт олон оролдлого хийлээ. Нэг минут хүлээгээд дахин оролдоно уу."
        : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    await authApi.logout().catch(() => {});
    setMe(null);
  }

  if (loading) return <div className="center muted">Ачаалж байна…</div>;

  if (!me) {
    return (
      <main className="login">
        <h1>{title}</h1>
        {error && <div className="alert">{error}</div>}
        {cfg?.sso_enabled && (
          <a className="button" href={authApi.ssoUrl(returnTo)}>
            Байгууллагын бүртгэлээр нэвтрэх (SSO)
          </a>
        )}
        {cfg?.password_login_enabled && (
          <form className="dev-login" onSubmit={passwordLogin}>
            <input value={loginId} onChange={(e) => setLoginId(e.target.value)} placeholder="Нэвтрэх нэр"
                   aria-label="Нэвтрэх нэр" autoComplete="username" required />
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   placeholder="Нууц үг" aria-label="Нууц үг" autoComplete="current-password" required />
            <button type="submit" disabled={busy}>{busy ? "Шалгаж байна…" : "Нэвтрэх"}</button>
          </form>
        )}
        {cfg?.dev_login_enabled && (
          <form className="dev-login" onSubmit={devLogin}>
            <p className="muted small">Хөгжүүлэлтийн горим: IdP-гүйгээр нэвтрэх</p>
            <input value={devEmail} onChange={(e) => setDevEmail(e.target.value)} aria-label="И-мэйл" />
            <select value={devRole} onChange={(e) => setDevRole(e.target.value)} aria-label="Эрх">
              {(cfg.roles ?? []).map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <button type="submit">Нэвтрэх</button>
          </form>
        )}
        {!cfg && <p className="muted">Сервертэй холбогдож чадсангүй.</p>}
      </main>
    );
  }

  if (!me.permissions.includes(permission)) {
    return (
      <main className="login">
        <h1>{title}</h1>
        <div className="alert">{me.email} — энэ хэсэгт хандах эрхгүй ({permission}).</div>
        <button className="ghost" onClick={logout}>Гарах</button>
      </main>
    );
  }
  return <>{children(me, logout)}</>;
}
