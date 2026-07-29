"""Minimal async eSCL (AirScan) client for a network multifunction scanner.

Flow: GET ScannerCapabilities -> GET ScannerStatus -> POST ScanJobs (ScanSettings
XML) -> loop GET {job}/NextDocument (one JPEG per page; 404 = done) -> DELETE to
cancel. Platen yields 1 page, ADF yields N; the same NextDocument loop handles both.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
from lxml import etree

from ..config import settings

NS = {
    "scan": "http://schemas.hp.com/imaging/escl/2011/05/03",
    "pwg": "http://www.pwg.org/schemas/2010/12/sm",
}
# 300ths of an inch (eSCL content region unit)
PAGE_SIZES = {
    "A4": (2480, 3508),
    "Letter": (2550, 3300),
    "Legal": (2550, 4200),
}


class EsclError(Exception):
    pass


class ScannerBusy(EsclError):
    pass


class AdfEmptyOrJam(EsclError):
    pass


@dataclass
class SourceCaps:
    max_width: int = 0
    max_height: int = 0
    color_modes: list[str] = field(default_factory=list)
    resolutions: list[int] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)


@dataclass
class Capabilities:
    make_and_model: str = ""
    platen: SourceCaps | None = None
    adf: SourceCaps | None = None
    adf_duplex: bool = False


def _text(el, path):
    found = el.find(path, NS)
    return found.text if found is not None else None


def _parse_source(caps_el) -> SourceCaps:
    sc = SourceCaps()
    sc.max_width = int(_text(caps_el, "scan:MaxWidth") or 0)
    sc.max_height = int(_text(caps_el, "scan:MaxHeight") or 0)
    prof = caps_el.find("scan:SettingProfiles/scan:SettingProfile", NS)
    if prof is not None:
        sc.color_modes = [e.text for e in prof.findall("scan:ColorModes/scan:ColorMode", NS)]
        sc.formats = [e.text for e in prof.findall("scan:DocumentFormats/pwg:DocumentFormat", NS)]
        sc.resolutions = sorted({
            int(e.text)
            for e in prof.findall("scan:SupportedResolutions/scan:DiscreteResolutions/scan:DiscreteResolution/scan:XResolution", NS)
        } | {
            int(e.text)
            for e in prof.findall("scan:DiscreteResolutions/scan:DiscreteResolution/scan:XResolution", NS)
        })
    return sc


class EsclClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or settings.escl_base_url).rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or settings.escl_timeout, follow_redirects=True)

    async def capabilities(self) -> Capabilities:
        async with self._client() as c:
            r = await c.get(f"{self.base}/eSCL/ScannerCapabilities")
            r.raise_for_status()
        root = etree.fromstring(r.content)
        caps = Capabilities(make_and_model=_text(root, "pwg:MakeAndModel") or "")
        platen = root.find("scan:Platen/scan:PlatenInputCaps", NS)
        if platen is not None:
            caps.platen = _parse_source(platen)
        adf = root.find("scan:Adf", NS)
        if adf is not None:
            simplex = adf.find("scan:AdfSimplexInputCaps", NS)
            duplex = adf.find("scan:AdfDuplexInputCaps", NS)
            caps.adf = _parse_source(simplex if simplex is not None else duplex)
            caps.adf_duplex = duplex is not None
        return caps

    async def status(self) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self.base}/eSCL/ScannerStatus")
            r.raise_for_status()
        root = etree.fromstring(r.content)
        state = _text(root, "pwg:State") or "Unknown"
        adf_state = _text(root, "scan:AdfState")
        return {"state": state, "adf_state": adf_state, "idle": state == "Idle"}

    def _scan_settings(self, source: str, color: str, resolution: int, page_size: str, fmt: str) -> bytes:
        w, h = PAGE_SIZES.get(page_size, PAGE_SIZES["A4"])
        input_source = "Feeder" if source.lower() in ("adf", "feeder") else "Platen"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{NS['scan']}" xmlns:pwg="{NS['pwg']}">
  <pwg:Version>2.6</pwg:Version>
  <scan:Intent>Document</scan:Intent>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>{w}</pwg:Width>
      <pwg:Height>{h}</pwg:Height>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>{input_source}</pwg:InputSource>
  <scan:ColorMode>{color}</scan:ColorMode>
  <scan:XResolution>{resolution}</scan:XResolution>
  <scan:YResolution>{resolution}</scan:YResolution>
  <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>
</scan:ScanSettings>"""
        return xml.encode()

    async def scan(self, *, source="platen", color="RGB24", resolution=300,
                   page_size="A4", fmt="image/jpeg"):
        """Async generator yielding JPEG bytes, one per page."""
        feeder = source.lower() in ("adf", "feeder")
        body = self._scan_settings(source, color, resolution, page_size, fmt)
        print(f"[escl] scan start source={source} color={color} res={resolution} "
              f"size={page_size} base={self.base}", flush=True)
        async with self._client(timeout=settings.escl_job_timeout) as c:
            # HP eSCL is single-request: ANY other request too close to ScanJobs
            # (a status poll, or even our own pre-check) makes it return 503/409.
            # So post the job FIRST (nothing before it), and only diagnose on
            # failure. Retry with backoff to ride over transient states.
            r = await c.post(f"{self.base}/eSCL/ScanJobs", content=body,
                             headers={"Content-Type": "text/xml"})
            print(f"[escl] ScanJobs POST -> {r.status_code} loc={r.headers.get('Location')}", flush=True)
            attempt = 0
            while r.status_code in (409, 503) and attempt < 4:
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                r = await c.post(f"{self.base}/eSCL/ScanJobs", content=body,
                                 headers={"Content-Type": "text/xml"})
                print(f"[escl] ScanJobs POST retry {attempt} -> {r.status_code}", flush=True)
            if r.status_code in (409, 503):
                # dump the device state so we can see WHY it keeps rejecting us
                try:
                    raw = (await c.get(f"{self.base}/eSCL/ScannerStatus")).text
                    print(f"[escl] give up {r.status_code}; ScannerStatus:\n{raw[:900]}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[escl] give up {r.status_code}; status dump failed: {e}", flush=True)
                if feeder:
                    adf_state = (await self.status()).get("adf_state") or ""
                    if adf_state and "Loaded" not in adf_state:
                        raise AdfEmptyOrJam("Chargeur (ADF) vide — rechargez le document")
                raise ScannerBusy("scanner busy")
            if r.status_code not in (200, 201):
                raise EsclError(f"ScanJobs failed: HTTP {r.status_code}")
            job = r.headers.get("Location")
            if not job:
                raise EsclError("no job Location returned")
            job_url = job if job.startswith("http") else f"{self.base}{job}"

            page = 0
            while True:
                rr = await c.get(f"{job_url}/NextDocument")
                if rr.status_code in (404, 410):
                    break  # no more documents
                if rr.status_code == 503:
                    raise ScannerBusy("scanner busy mid-job")
                if rr.status_code != 200:
                    if page == 0:
                        raise AdfEmptyOrJam(f"NextDocument HTTP {rr.status_code}")
                    break
                if not rr.content:
                    break
                page += 1
                yield rr.content
            if page == 0:
                raise AdfEmptyOrJam("no pages produced (ADF empty or nothing on platen)")
