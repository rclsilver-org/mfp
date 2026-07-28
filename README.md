# mfp — print & scan appliance

> **MFP = *MultiFunction Printer***. This repo bundles two services placed **in
> front of** a multifunction printer (eSCL-capable) to share it in a controlled
> way: **printing** (a proxy) and **scanning** (a web app).

## The two components

| Directory | Image | Role |
|---|---|---|
| `print/` | `ghcr.io/rclsilver-org/mfp-print` | **Print proxy** (CUPS). Exposes an IPP printer, authenticates users against LDAP, renders each job to PDF, **archives** it, then **forwards** it to the physical printer. Prometheus metrics + print history. |
| `scan/` | `ghcr.io/rclsilver-org/mfp-scan` | **Scan web app** (FastAPI + React). Drives the scanner over **eSCL**, authenticates via **OIDC (Keycloak)** with LDAP-based authorization, offers a multi-page workbench (reorder, rotate, **recto/verso interleave**), archives the PDF and keeps a history (download / email / re-open & edit). |

Documents (prints and scans) are archived as PDF, organized per user, and the
scan app exposes a **unified print + scan history** scoped per user (with broader
access for administrators).

## Repository layout

```
print/                 # mfp-print image (Dockerfile, rootfs/, entrypoint, exporter…)
scan/
  backend/             # FastAPI (scanapp package)
  frontend/            # React + Vite + TS
  dev/                 # local dev stack (docker-compose, throwaway Keycloak realm)
  README.dev.md        # how to run/test locally
.github/workflows/     # builds both images (mfp-print, mfp-scan)
```

## Local development

Everything is developed and tested **locally**. See [`scan/README.dev.md`](scan/README.dev.md):
a `docker compose` brings up the app, a **throwaway Keycloak** and a mail catcher,
with a **fake scanner** (`SCANAPP_FAKE_SCANNER=true`) that generates realistic
pages without any hardware.

## Configuration & deployment

**Nothing site-specific is baked into the images**: all configuration (LDAP,
printer address, OIDC, paths, groups…) is supplied at **runtime** via environment
variables (plus a mounted PPD for printing). Images are published on `ghcr.io`;
the `:latest` tag is pushed only on a git tag `vX.Y.Z`. Deployment
(Kubernetes/Terraform) is managed separately.
