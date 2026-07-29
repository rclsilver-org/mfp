import { useEffect, useState } from "react";
import { api, keycloak } from "./keycloak";

type Me = { username: string; email?: string; display_name?: string; is_admin: boolean; can_scan: boolean };
type Page = { id: string; rotation: number; source: string; batch_id: string };
type Batch = { id: string; index: number; source: string; count: number };
type User = { uid: string; display_name?: string };

function useThumb(pageId: string, rotation: number) {
  const [url, setUrl] = useState<string>("");
  useEffect(() => {
    let live = true, obj = "";
    api(`/pages/${pageId}/thumb`).then(r => r.blob()).then(b => {
      if (!live) return; obj = URL.createObjectURL(b); setUrl(obj);
    });
    return () => { live = false; if (obj) URL.revokeObjectURL(obj); };
  }, [pageId, rotation]);
  return url;
}

function PageCard({ p, index, dragOver, batchLabel, onRotate, onDelete, onPreview, onDragStart, onDragEnter, onDrop, onDragEnd }: {
  p: Page; index: number; dragOver: boolean; batchLabel?: string;
  onRotate: () => void; onDelete: () => void; onPreview: () => void;
  onDragStart: () => void; onDragEnter: () => void; onDrop: () => void; onDragEnd: () => void;
}) {
  const url = useThumb(p.id, p.rotation);
  return (
    <div
      className={"page" + (dragOver ? " dragover" : "")}
      draggable
      onDragStart={onDragStart}
      onDragEnter={onDragEnter}
      onDragOver={e => e.preventDefault()}
      onDrop={e => { e.preventDefault(); onDrop(); }}
      onDragEnd={onDragEnd}
      title="Glisser pour réordonner"
    >
      <div className="page-num">{index + 1}</div>
      {batchLabel && <div className="batch-tag">{batchLabel}</div>}
      <span className="drag-handle">⠿</span>
      {url ? <img src={url} alt="" draggable={false} onClick={onPreview} style={{ cursor: "zoom-in" }} /> : <div style={{ height: 170 }} />}
      <div className="acts">
        <button className="secondary" title="Agrandir" onClick={onPreview}>🔍</button>
        <button className="secondary" title="Pivoter" onClick={onRotate}>⟳</button>
        <button className="secondary" title="Supprimer" onClick={onDelete}>🗑</button>
      </div>
    </div>
  );
}

function PreviewModal({ page, index, total, onClose, onPrev, onNext }: {
  page: Page; index: number; total: number; onClose: () => void; onPrev: () => void; onNext: () => void;
}) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let live = true, obj = "";
    setUrl("");
    api(`/pages/${page.id}/preview`).then(r => r.blob()).then(b => {
      if (!live) return; obj = URL.createObjectURL(b); setUrl(obj);
    });
    return () => { live = false; if (obj) URL.revokeObjectURL(obj); };
  }, [page.id, page.rotation]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onPrev();
      if (e.key === "ArrowRight") onNext();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, onPrev, onNext]);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-bar">
          <span>Page {index + 1} / {total}</span>
          <span className="sp" />
          <button className="secondary" onClick={onPrev} disabled={total < 2}>←</button>
          <button className="secondary" onClick={onNext} disabled={total < 2}>→</button>
          <button onClick={onClose}>Fermer</button>
        </div>
        <div className="modal-img">
          {url ? <img src={url} alt="" /> : <div className="note">Chargement…</div>}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [tab, setTab] = useState<"scan" | "history">("scan");
  const [caps, setCaps] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [resumable, setResumable] = useState<any[]>([]);

  const [sid, setSid] = useState<string>("");
  const [pages, setPages] = useState<Page[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [scanning, setScanning] = useState(false);
  const [msg, setMsg] = useState<string>("");
  const [interleaveOpen, setInterleaveOpen] = useState(false);
  const [ilFront, setIlFront] = useState("");
  const [ilBack, setIlBack] = useState("");
  const [ilRev, setIlRev] = useState(true);

  const [opts, setOpts] = useState({ source: "platen", color: "RGB24", resolution: 300, page_size: "A4" });
  const [onBehalf, setOnBehalf] = useState("");
  const [docName, setDocName] = useState("");
  const [finRes, setFinRes] = useState<any>(null);
  const [saved, setSaved] = useState<{ id: number; filename: string } | null>(null);
  const [saveDialog, setSaveDialog] = useState(false);

  const [history, setHistory] = useState<any[]>([]);
  const [histUser, setHistUser] = useState("");
  const [previewIdx, setPreviewIdx] = useState<number | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  async function movePage(from: number, to: number) {
    if (from === to) return;
    const next = pages.slice();
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setPages(next); // optimistic
    await api(`/sessions/${sid}/pages/reorder`, { method: "POST", body: JSON.stringify({ order: next.map(p => p.id) }) });
  }

  useEffect(() => {
    api("/me").then(r => r.json()).then((m: Me) => {
      setMe(m);
      if (m.can_scan) {
        api("/scanner/capabilities").then(r => r.json()).then(setCaps).catch(() => {});
        loadResumable();
      }
      if (m.is_admin) api("/users").then(r => r.json()).then(setUsers).catch(() => {});
    });
  }, []);

  useEffect(() => {
    if (!me?.can_scan) return;
    // pause polling while scanning: the eSCL device is single-request, so a status
    // poll colliding with the scan job makes the device return 503/409 ("busy")
    if (scanning) return;
    const t = setInterval(() => api("/scanner/status").then(r => r.json()).then(setStatus).catch(() => {}), 4000);
    api("/scanner/status").then(r => r.json()).then(setStatus).catch(() => {});
    return () => clearInterval(t);
  }, [me, scanning]);

  async function ensureSession(): Promise<string> {
    if (sid) return sid;
    const r = await api("/sessions", { method: "POST", body: JSON.stringify({ name: docName, on_behalf_of: onBehalf || null }) });
    const s = await r.json();
    setSid(s.id);
    return s.id;
  }

  async function refresh(id: string) {
    const s = await api(`/sessions/${id}`).then(r => r.json());
    setPages(s.pages || []);
    setBatches(s.batches || []);
    setSaved(s.saved_history_id ? { id: s.saved_history_id, filename: s.saved_filename } : null);
  }

  function loadResumable() {
    api("/sessions/resumable").then(r => r.json()).then(setResumable).catch(() => {});
  }

  async function resumeSession(id: string, name: string) {
    setFinRes(null); setMsg(""); setDocName(name || ""); setSid(id);
    await refresh(id);
  }

  async function discardSession(id: string) {
    await api(`/sessions/${id}`, { method: "DELETE" });
    loadResumable();
  }

  const batchLabel = (bid: string) => {
    const b = batches.find(x => x.id === bid);
    return b ? "L" + (b.index + 1) : "";
  };

  async function reverseBatch(bid: string) {
    await api(`/sessions/${sid}/batches/${bid}/reverse`, { method: "POST" });
    await refresh(sid);
  }
  function openInterleave() {
    const b = [...batches].sort((a, z) => a.index - z.index);
    setIlFront(b[0]?.id || ""); setIlBack(b[1]?.id || ""); setIlRev(true);
    setInterleaveOpen(true);
  }
  async function applyInterleave() {
    const r = await api(`/sessions/${sid}/interleave`, { method: "POST", body: JSON.stringify({ front_batch: ilFront, back_batch: ilBack, back_reversed: ilRev }) });
    if (!r.ok) { alert("Interleave: " + (await r.text())); return; }
    setInterleaveOpen(false); await refresh(sid);
  }

  async function doScan() {
    setMsg(""); setScanning(true); setFinRes(null);
    try {
      const id = await ensureSession();
      const r = await api(`/sessions/${id}/scan`, { method: "POST", body: JSON.stringify(opts) });
      if (!r.ok) { setMsg("Erreur scan: " + (await r.text())); }
      await refresh(id);
    } catch (e: any) { setMsg(String(e)); }
    finally { setScanning(false); }
  }

  async function rotate(p: Page) {
    await api(`/pages/${p.id}`, { method: "PATCH", body: JSON.stringify({ rotation: (p.rotation + 90) % 360 }) });
    await refresh(sid);
  }
  async function del(p: Page) { await api(`/pages/${p.id}`, { method: "DELETE" }); await refresh(sid); }

  function newDoc() { setSid(""); setPages([]); setBatches([]); setFinRes(null); setDocName(""); setMsg(""); setSaved(null); }

  function onSaveClick() {
    if (saved) finalize(true);        // re-opened document -> overwrite directly
    else setSaveDialog(true);          // first save -> ask for a name
  }

  async function confirmSave() {
    setSaveDialog(false);
    await finalize(false);
  }

  async function finalize(overwrite: boolean) {
    setMsg("");
    const r = await api(`/sessions/${sid}/finalize`, { method: "POST", body: JSON.stringify({ name: docName, deliveries: [], overwrite }) });
    if (!r.ok) { setMsg("Erreur enregistrement: " + (await r.text())); return; }
    setFinRes(await r.json());
    // the document is now saved and its scratch is gone: start a fresh document so
    // the next scan is a new document, not an edit of the one we just saved.
    setSid(""); setPages([]); setBatches([]); setSaved(null); setDocName("");
    loadResumable();
  }

  async function downloadDoc(id: number, filename: string) {
    const blob = await api(`/documents/${id}/download`).then(x => x.blob());
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = filename; a.click();
  }

  async function emailDoc(id: number) {
    const r = await api(`/documents/${id}/email`, { method: "POST" });
    alert(r.ok ? "Email envoyé." : "Échec de l'envoi: " + (await r.text()));
  }

  async function reopenDoc(id: number) {
    const r = await api(`/history/${id}/reopen`, { method: "POST" });
    if (!r.ok) { alert("Impossible de ré-ouvrir: " + (await r.text())); return; }
    const s = await r.json();
    setSid(s.session_id); setDocName(s.name || ""); setFinRes(null);
    await refresh(s.session_id);
    setTab("scan");
  }

  function loadHistory() {
    const q = new URLSearchParams();
    if (histUser) q.set("user", histUser);
    api("/history?" + q.toString()).then(r => r.json()).then(setHistory).catch(() => {});
  }
  useEffect(() => { if (tab === "history") loadHistory(); }, [tab]);

  if (!me) return <main>Chargement…</main>;

  return (
    <>
      <header>
        <strong>Scan</strong>
        <span className="sp" />
        <span>{me.display_name || me.username}</span>
        {me.is_admin && <span className="badge admin">admin</span>}
        {!me.can_scan && <span className="badge">lecture seule</span>}
        <button className="secondary" onClick={() => keycloak.logout()}>Déconnexion</button>
      </header>
      <main>
        <div className="tabs">
          <button className={tab === "scan" ? "active" : ""} onClick={() => setTab("scan")}>Scanner</button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Historique</button>
        </div>

        {tab === "scan" && (me.can_scan ? (
          <>
            {!sid && pages.length === 0 && resumable.length > 0 && (
              <div className="card">
                <b>Reprendre un document en cours</b>
                <p className="note">Des travaux non enregistrés ont été retrouvés.</p>
                {resumable.map(r => (
                  <div key={r.id} className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: ".4rem" }}>
                    <span>
                      <b>{r.name || "Sans titre"}</b> · {r.pages} page(s)
                      {r.owner !== me.username && <> · pour <b>{r.owner}</b></>}
                      <span className="note"> · {new Date(r.updated_at * 1000).toLocaleString()}</span>
                    </span>
                    <span style={{ display: "flex", gap: ".5rem" }}>
                      <button onClick={() => resumeSession(r.id, r.name)}>Reprendre</button>
                      <button className="secondary" onClick={() => discardSession(r.id)}>Annuler</button>
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div className="card">
              <div className="status">
                Scanner : {status ? <b>{status.state}{status.busy ? ` — occupé (${status.held_by})` : ""}</b> : "…"}
                {status?.adf_state && <span className="note"> · ADF: {status.adf_state}</span>}
              </div>
              <div className="row" style={{ marginTop: ".75rem" }}>
                <label>Source
                  <select value={opts.source} onChange={e => setOpts({ ...opts, source: e.target.value })}>
                    <option value="platen">Vitre</option>
                    <option value="adf">Chargeur (ADF)</option>
                  </select>
                </label>
                <label>Couleur
                  <select value={opts.color} onChange={e => setOpts({ ...opts, color: e.target.value })}>
                    <option value="RGB24">Couleur</option>
                    <option value="Grayscale8">Gris</option>
                    <option value="BlackAndWhite1">N&B</option>
                  </select>
                </label>
                <label>Résolution
                  <select value={opts.resolution} onChange={e => setOpts({ ...opts, resolution: +e.target.value })}>
                    {(caps?.platen?.resolutions || [150, 300, 600]).filter((r: number) => [150, 300, 600].includes(r)).map((r: number) => <option key={r} value={r}>{r} dpi</option>)}
                  </select>
                </label>
                <label>Format
                  <select value={opts.page_size} onChange={e => setOpts({ ...opts, page_size: e.target.value })}>
                    {(caps?.page_sizes || ["A4", "Letter", "Legal"]).map((s: string) => <option key={s}>{s}</option>)}
                  </select>
                </label>
                {me.is_admin && (
                  <label>Scanner pour
                    <select value={onBehalf} disabled={!!sid} onChange={e => setOnBehalf(e.target.value)}>
                      <option value="">moi ({me.username})</option>
                      {users.map(u => <option key={u.uid} value={u.uid}>{u.display_name || u.uid}</option>)}
                    </select>
                  </label>
                )}
                <button disabled={scanning || status?.busy} onClick={doScan}>{scanning ? "Scan en cours…" : "Scanner un lot"}</button>
                <button className="secondary" onClick={newDoc} disabled={!sid && pages.length === 0}>Nouveau document</button>
              </div>
              {msg && <p className="err">{msg}</p>}
              {onBehalf && <p className="note">Ce document sera attribué à <b>{onBehalf}</b>.</p>}
              {finRes && (
                <p className="note">
                  {finRes.overwritten ? "Écrasé" : "Enregistré"} : <b>{finRes.filename}</b>{" "}
                  <span className={String(finRes.results?.archive).startsWith("error") ? "err" : "ok"}>✓</span>
                  {finRes.id && <> · <button className="secondary" onClick={() => downloadDoc(finRes.id, finRes.filename)}>Télécharger</button></>}
                </p>
              )}
            </div>

            {pages.length > 0 && (
              <div className="card">
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                  <b>{pages.length} page(s){saved ? ` · ${saved.filename}` : ""}</b>
                  <span style={{ display: "flex", gap: ".5rem", alignItems: "center" }}>
                    <button onClick={onSaveClick}>{saved ? "Enregistrer (écraser)" : "Enregistrer"}</button>
                  </span>
                </div>
                {msg && <p className="err">{msg}</p>}
                {batches.length > 0 && (
                  <div className="row" style={{ gap: ".4rem", flexWrap: "wrap", margin: ".4rem 0" }}>
                    {batches.map(b => (
                      <span key={b.id} className="batch-chip">
                        L{b.index + 1} · {b.source === "adf" ? "ADF" : "Vitre"} · {b.count} p
                        <button className="secondary" title="Inverser l'ordre de ce lot" onClick={() => reverseBatch(b.id)}>⇅</button>
                      </span>
                    ))}
                    {batches.length >= 2 && <button className="secondary" onClick={openInterleave}>⇄ Interleave recto-verso</button>}
                  </div>
                )}
                <p className="note">Glisse-dépose les vignettes pour réordonner. Clique 🔍 pour agrandir.</p>
                <div className="grid" style={{ marginTop: ".5rem" }}>
                  {pages.map((p, i) => <PageCard key={p.id + p.rotation} p={p} index={i}
                    dragOver={dragOver === i && dragFrom !== i} batchLabel={batchLabel(p.batch_id)}
                    onRotate={() => rotate(p)} onDelete={() => del(p)} onPreview={() => setPreviewIdx(i)}
                    onDragStart={() => setDragFrom(i)}
                    onDragEnter={() => setDragOver(i)}
                    onDrop={() => { if (dragFrom !== null) movePage(dragFrom, i); }}
                    onDragEnd={() => { setDragFrom(null); setDragOver(null); }}
                  />)}
                </div>
              </div>
            )}

          </>
        ) : <div className="card">Tu n'es pas membre du groupe autorisé à scanner.</div>)}

        {tab === "history" && (
          <div className="card">
            <div className="row">
              {me.is_admin && (
                <label>Utilisateur
                  <select value={histUser} onChange={e => setHistUser(e.target.value)}>
                    <option value="">tous</option>
                    {users.map(u => <option key={u.uid} value={u.uid}>{u.display_name || u.uid}</option>)}
                  </select>
                </label>
              )}
              <button onClick={loadHistory}>Rafraîchir</button>
            </div>
            <table style={{ marginTop: ".75rem" }}>
              <thead><tr><th>Date</th><th>Type</th><th>Utilisateur</th><th>Document</th><th>Pages</th><th>IP / par</th><th></th></tr></thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={i}>
                    <td>{new Date(h.ts * 1000).toLocaleString("fr-FR")}</td>
                    <td>{h.type}</td>
                    <td>{h.user}</td>
                    <td>{h.document}</td>
                    <td>{h.pages}</td>
                    <td>{h.source_ip || h.performed_by || ""}</td>
                    <td>{h.type === "scan" && h.id && (
                      <span style={{ display: "flex", gap: ".3rem" }}>
                        {h.downloadable && <button className="secondary" title="Télécharger" onClick={() => downloadDoc(h.id, h.document + ".pdf")}>⬇︎</button>}
                        {h.downloadable && <button className="secondary" title="Envoyer par email" onClick={() => emailDoc(h.id)}>✉</button>}
                        <button className="secondary" title="Ré-ouvrir / modifier" onClick={() => reopenDoc(h.id)}>✎</button>
                      </span>
                    )}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {history.length === 0 && <p className="note">Aucune entrée.</p>}
          </div>
        )}
      </main>
      {previewIdx !== null && pages[previewIdx] && (
        <PreviewModal
          page={pages[previewIdx]}
          index={previewIdx}
          total={pages.length}
          onClose={() => setPreviewIdx(null)}
          onPrev={() => setPreviewIdx(i => (i === null ? i : (i - 1 + pages.length) % pages.length))}
          onNext={() => setPreviewIdx(i => (i === null ? i : (i + 1) % pages.length))}
        />
      )}
      {interleaveOpen && (
        <div className="overlay" onClick={() => setInterleaveOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ width: "min(92vw,480px)" }}>
            <div className="modal-bar"><b>Interleave recto-verso</b></div>
            <div style={{ padding: "1rem", display: "flex", flexDirection: "column", gap: ".6rem" }}>
              <p className="note" style={{ margin: 0 }}>Reconstitue un document recto-verso : entrelace le lot des rectos avec le lot des versos.</p>
              <label>Lot des rectos
                <select value={ilFront} onChange={e => setIlFront(e.target.value)}>
                  {batches.map(b => <option key={b.id} value={b.id}>L{b.index + 1} · {b.source === "adf" ? "ADF" : "Vitre"} · {b.count} p</option>)}
                </select>
              </label>
              <label>Lot des versos
                <select value={ilBack} onChange={e => setIlBack(e.target.value)}>
                  {batches.map(b => <option key={b.id} value={b.id}>L{b.index + 1} · {b.source === "adf" ? "ADF" : "Vitre"} · {b.count} p</option>)}
                </select>
              </label>
              <label style={{ flexDirection: "row", alignItems: "center", gap: ".4rem" }}>
                <input type="checkbox" checked={ilRev} onChange={e => setIlRev(e.target.checked)} />
                Versos scannés à l'envers (ADF simplex) — recommandé
              </label>
              <div className="row" style={{ justifyContent: "flex-end", marginTop: ".3rem" }}>
                <button className="secondary" onClick={() => setInterleaveOpen(false)}>Annuler</button>
                <button onClick={applyInterleave} disabled={!ilFront || !ilBack || ilFront === ilBack}>Appliquer</button>
              </div>
            </div>
          </div>
        </div>
      )}
      {saveDialog && (
        <div className="overlay" onClick={() => setSaveDialog(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ width: "min(92vw,420px)" }}>
            <div className="modal-bar"><b>Enregistrer le document</b></div>
            <div style={{ padding: "1rem" }}>
              <label>Nom du document
                <input type="text" autoFocus value={docName} placeholder="scan"
                  onChange={e => setDocName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") confirmSave(); if (e.key === "Escape") setSaveDialog(false); }} />
              </label>
              <div className="row" style={{ marginTop: ".9rem", justifyContent: "flex-end" }}>
                <button className="secondary" onClick={() => setSaveDialog(false)}>Annuler</button>
                <button onClick={confirmSave}>Enregistrer</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
