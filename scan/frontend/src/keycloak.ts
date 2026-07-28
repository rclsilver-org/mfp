import Keycloak from "keycloak-js";

const cfg = (window as any).__SCAN_CONFIG__;

export const apiBase: string = cfg.apiBase;

export const keycloak = new Keycloak({
  url: cfg.keycloakUrl,
  realm: cfg.realm,
  clientId: cfg.clientId,
});

export async function initAuth(): Promise<void> {
  await keycloak.init({
    onLoad: "login-required",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });
  // refresh token periodically
  setInterval(() => keycloak.updateToken(60).catch(() => keycloak.login()), 30000);
}

export async function api(path: string, opts: RequestInit = {}): Promise<Response> {
  await keycloak.updateToken(30).catch(() => {});
  const headers = new Headers(opts.headers || {});
  headers.set("Authorization", `Bearer ${keycloak.token}`);
  if (opts.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(apiBase + path, { ...opts, headers });
}
