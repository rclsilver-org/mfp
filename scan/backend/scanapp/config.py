"""Runtime configuration (env-driven, nothing site-specific baked in)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCANAPP_", env_file=".env", extra="ignore")

    # --- OIDC (Keycloak) ---
    oidc_issuer: str = "http://localhost:8081/realms/scan"
    oidc_client_id: str = "scan"
    # audience to expect in access tokens; Keycloak access tokens usually have aud="account"
    oidc_audience: str = "account"
    # accept tokens whose azp matches the client even if aud differs (Keycloak default)
    oidc_accept_azp: bool = True

    # --- LDAP (authorization + user attributes) ---
    # Site-specific values are provided at runtime (env / compose / Terraform);
    # the defaults below are placeholders, nothing real is baked into the image.
    ldap_url: str = "ldap://ldap.example.com:389"
    ldap_user_base: str = "ou=people,dc=example,dc=com"
    ldap_group_base: str = "ou=groups,dc=example,dc=com"
    ldap_scan_group: str = "printers"          # members allowed to scan
    ldap_admin_group: str = "administrators"    # members who see everyone's history / scan-as
    ldap_cache_ttl: int = 300

    # --- Scanner (eSCL) ---
    escl_base_url: str = "http://printer.example.com"
    escl_timeout: float = 30.0
    escl_job_timeout: float = 300.0
    # dev only: generate realistic fake pages instead of driving the real device
    fake_scanner: bool = False

    # --- Storage ---
    # One shared "printer" dataset (root:administrators 2750). Everything the scan
    # app owns lives under scan-archive/ (like the CUPS proxy under print-archive/):
    #   scan-archive/.scanapp.db                       -> the app's SQLite DB
    #   scan-archive/<user>/YYYY/MM/DD/<ts>_<name>.pdf -> finalized documents
    #   scan-archive/<user>/.scratch/<session>/        -> in-progress page images (transient)
    printer_dir: str = "/printer"
    archive_group: str = "administrators"

    @property
    def scan_archive_dir(self) -> str:
        return f"{self.printer_dir}/scan-archive"

    @property
    def db_path(self) -> str:
        # kept next to the scan archive, mirroring print-archive/.print-metrics.db
        return f"{self.scan_archive_dir}/.scanapp.db"

    @property
    def print_metrics_db(self) -> str:
        return f"{self.printer_dir}/print-archive/.print-metrics.db"

    # --- Email delivery ---
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "scan@example.com"
    email_enabled: bool = True

    # --- Misc ---
    session_ttl_hours: int = 48
    cors_origins: str = "http://localhost:5173"


settings = Settings()
