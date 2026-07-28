# Scan app — local dev

Local dev loop (Linux, host networking). **Nothing is printed, nothing touches
production.** Auth goes through a throwaway Keycloak; authorization is still done
against the real LDAP, and scanning uses eSCL (fake scanner by default, see
`SCANAPP_FAKE_SCANNER`).

## Prepare (once)

Site-specific values are not committed — start from the templates:

```bash
cd scan
cp .env.example .env                                # -> set your LDAP + printer
cp dev/realm-scan.example.json dev/realm-scan.json  # -> use REAL LDAP uids as usernames
```
- `.env`: `SCANAPP_LDAP_URL`, `SCANAPP_LDAP_USER_BASE`, `SCANAPP_LDAP_GROUP_BASE`, `SCANAPP_ESCL_BASE_URL`.
- `dev/realm-scan.json`: the test users' `username` must match **real LDAP uids** so that
  groups (`printers`/`administrators`) resolve. Dev passwords are up to you.

## Run

```bash
docker compose -f docker-compose.dev.yml up --build   # from scan/
```
Then open **http://localhost:5173**.

- Keycloak: http://localhost:8081 (admin/admin), realm `scan`.
- MailHog (scan emails): http://localhost:8025
- Local data (SQLite + scan archive): `scan/dev/data/` (gitignored).
- The fake scanner is on by default (`SCANAPP_FAKE_SCANNER=true`) → realistic pages without
  paper; set it to `false` in the compose file to drive the real device.

## Test

1. Log in as a test user (admin / a `printers` member / a non-`printers` user to see the 403).
2. "Scan a batch" (fake scanner) → thumbnails; reorder (drag), rotate, preview 🔍.
3. Two ADF batches → **⇄ recto/verso interleave** → reconstructed order.
4. "Save" (name prompt) → archived; **History** tab: download / email / re-open.
5. Admin: "scan on behalf of" another user; global, filterable history.
