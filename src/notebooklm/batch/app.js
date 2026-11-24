"use strict";

const el = id => document.getElementById(id);
const feedback = message => { el("feedback").textContent = message; };

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data));
  }
  return data;
}

async function downloadBlob(url, payload, defaultFilename) {
  feedback("⏳ Download wird vorbereitet und generiert...");
  try {
    const options = payload ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    } : { method: "GET" };

    const response = await fetch(url, options);
    if (!response.ok) {
      let errText = "Fehler beim Download";
      try {
        const errJson = await response.json();
        errText = errJson.detail || JSON.stringify(errJson);
      } catch (_) {
        errText = await response.text();
      }
      throw new Error(errText);
    }

    let filename = defaultFilename;
    const disposition = response.headers.get("Content-Disposition");
    if (disposition && disposition.includes("filename=")) {
      const match = disposition.match(/filename="?([^";]+)"?/);
      if (match && match[1]) filename = match[1];
    }

    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
    feedback("✅ Download erfolgreich gestartet: " + filename);
  } catch (err) {
    feedback("❌ " + err.message);
  }
}

async function action(path) {
  try {
    const payload = JSON.parse(el("editor").value);
    const result = await request(path, payload);
    feedback(JSON.stringify(result, null, 2));
    await refresh();
  } catch (error) {
    feedback(error.message);
  }
}

function button(label, clickAction, className = "btn-sm") {
  const node = document.createElement("button");
  node.textContent = label;
  if (className) node.className = className;
  node.addEventListener("click", async () => {
    node.disabled = true;
    try {
      await clickAction();
      await refresh();
    } catch (error) {
      feedback(error.message);
    } finally {
      node.disabled = false;
    }
  });
  return node;
}

const drafts = new Map();
const selectedJobIds = new Set();
let cachedJobs = [];

function updateSelectionCount() {
  const countEl = el("selected-count");
  if (countEl) {
    countEl.textContent = `${selectedJobIds.size} ausgewählt`;
  }
  const headerBox = el("header-select-all");
  if (headerBox && cachedJobs.length > 0) {
    const allVisibleSelected = cachedJobs.every(j => selectedJobIds.has(j.id));
    const someVisibleSelected = cachedJobs.some(j => selectedJobIds.has(j.id));
    headerBox.checked = allVisibleSelected;
    headerBox.indeterminate = !allVisibleSelected && someVisibleSelected;
  }
}

async function refresh() {
  const filterVal = el("time-filter") ? el("time-filter").value : "24";
  const path = filterVal === "all" ? "/api/jobs?all=true" : `/api/jobs?hours=${encodeURIComponent(filterVal)}`;

  let jobs = [];
  try {
    jobs = await request(path);
  } catch (err) {
    feedback("Fehler beim Laden der Aufträge: " + err.message);
    return;
  }
  cachedJobs = jobs;

  const active = document.activeElement;
  const activeJobId = active?.dataset?.jobId;
  const cursor = (active && active.tagName === "INPUT") ? [active.selectionStart, active.selectionEnd] : null;

  const tbody = el("jobs");
  tbody.replaceChildren();

  if (jobs.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.style.textAlign = "center";
    td.style.color = "var(--text-muted)";
    td.style.padding = "2rem";
    td.textContent = filterVal === "24"
      ? "Keine Aufträge in den letzten 24 Stunden gefunden. Verwende oben 'Auftrag absenden' oder ändere den Zeitraum-Filter."
      : "Keine Aufträge vorhanden.";
    tr.append(td);
    tbody.append(tr);
    updateSelectionCount();
    return;
  }

  for (const item of jobs) {
    const tr = document.createElement("tr");
    const job = item.job || {};

    // 1. Checkbox
    const tdCheck = document.createElement("td");
    tdCheck.style.textAlign = "center";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = selectedJobIds.has(item.id);
    box.addEventListener("change", () => {
      if (box.checked) selectedJobIds.add(item.id);
      else selectedJobIds.delete(item.id);
      updateSelectionCount();
    });
    tdCheck.append(box);
    tr.append(tdCheck);

    // 2. ID & Title
    const tdTitle = document.createElement("td");
    const titleText = job.title ? job.title : item.id;
    const strong = document.createElement("strong");
    strong.textContent = titleText;
    const idSmall = document.createElement("small");
    idSmall.textContent = item.id;
    tdTitle.append(strong, idSmall);
    tr.append(tdTitle);

    // 3. Typ
    const tdType = document.createElement("td");
    const typeLabel = (job.type || "report").replace(/_/g, " ");
    tdType.textContent = typeLabel;
    tr.append(tdType);

    // 4. PDF Position / Order
    const tdPdf = document.createElement("td");
    const includeInPdf = job.include_in_pdf !== false;
    const pdfOrder = job.pdf_order ?? job.order ?? job.position;
    if (!includeInPdf) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.style.background = "#eaeef2";
      badge.style.color = "#8c959f";
      badge.textContent = "✕ Ausgeschlossen";
      tdPdf.append(badge);
    } else {
      const badge = document.createElement("span");
      badge.className = "badge badge-order";
      badge.textContent = pdfOrder !== undefined ? `#${pdfOrder}` : "Im PDF";
      tdPdf.append(badge);
    }
    tr.append(tdPdf);

    // 5. Status
    const tdStatus = document.createElement("td");
    const statusSpan = document.createElement("span");
    statusSpan.className = `badge badge-${item.status}`;
    const statusMap = {
      complete: "✅ Abgeschlossen",
      polling: "🔄 In Arbeit",
      queued: "⏳ Warteschlange",
      retry_queued: "⏳ Wartet auf Retry",
      submitting: "🚀 Wird gesendet",
      failed: "❌ Fehlgeschlagen",
      uncertain: "⚠️ Unklar"
    };
    statusSpan.textContent = statusMap[item.status] || item.status;
    tdStatus.append(statusSpan);
    if (item.error) {
      const errSmall = document.createElement("small");
      errSmall.style.color = "var(--danger)";
      errSmall.textContent = item.error;
      tdStatus.append(errSmall);
    }
    tr.append(tdStatus);

    // 6. Actions
    const tdActions = document.createElement("td");
    tdActions.style.display = "flex";
    tdActions.style.gap = "0.35rem";
    tdActions.style.flexWrap = "wrap";

    if (item.status === "complete" && item.artifact_id) {
      const dlBtn = button("📥 Herunterladen", () => {
        downloadBlob(`/api/jobs/${encodeURIComponent(item.id)}/download`, null, `${sanitizeFilename(job.title || item.id)}.download`);
      }, "btn-sm btn-success");
      tdActions.append(dlBtn);
    }

    if (["failed", "uncertain"].includes(item.status)) {
      if (item.status !== "uncertain") {
        tdActions.append(button("Wiederholen", () => request(`/api/jobs/${encodeURIComponent(item.id)}/retry`, {}), "btn-sm"));
      }
      if (item.status === "uncertain") {
        const idInput = document.createElement("input");
        idInput.placeholder = "Vorhandene Artifact ID";
        idInput.className = "btn-sm";
        idInput.style.maxWidth = "140px";
        idInput.dataset.jobId = item.id;
        if (drafts.has(item.id)) idInput.value = drafts.get(item.id);
        idInput.addEventListener("input", () => drafts.set(item.id, idInput.value));
        tdActions.append(idInput, button("Verknüpfen", async () => {
          await request(`/api/jobs/${encodeURIComponent(item.id)}/attach`, { artifact_id: idInput.value });
          drafts.delete(item.id);
        }, "btn-sm"));
      }
    }

    tdActions.append(button("Entfernen", () => request(`/api/jobs/${encodeURIComponent(item.id)}/delete`, {}), "btn-sm btn-danger"));
    tr.append(tdActions);

    tbody.append(tr);
  }

  updateSelectionCount();

  if (activeJobId) {
    const nextActive = tbody.querySelector(`input[data-job-id="${CSS.escape(activeJobId)}"]`);
    if (nextActive) {
      nextActive.focus();
      if (cursor && cursor[0] !== null) {
        try { nextActive.setSelectionRange(cursor[0], cursor[1]); } catch (_) {}
      }
    }
  }
}

function sanitizeFilename(name) {
  return (name || "document").replace(/[^a-zA-Z0-9äöüÄÖÜß_-]/g, "_");
}

// Quick Selection Handlers
el("select-latest-batch")?.addEventListener("click", () => {
  if (cachedJobs.length === 0) return;
  // Find highest created_at
  let maxTime = 0;
  for (const j of cachedJobs) {
    const t = j.created_at || 0;
    if (t > maxTime) maxTime = t;
  }
  selectedJobIds.clear();
  // Select jobs submitted within 3 seconds of the latest batch
  for (const j of cachedJobs) {
    if (Math.abs((j.created_at || 0) - maxTime) <= 3) {
      selectedJobIds.add(j.id);
    }
  }
  // If no timestamp was present or only 1 found, select the newest 5 or all from bottom
  if (selectedJobIds.size === 0) {
    const last = cachedJobs[cachedJobs.length - 1];
    if (last) selectedJobIds.add(last.id);
  }
  refresh();
  feedback(`⚡ Neuesten Auftrag ausgewählt (${selectedJobIds.size} Einträge).`);
});

el("select-completed")?.addEventListener("click", () => {
  selectedJobIds.clear();
  for (const j of cachedJobs) {
    if (j.status === "complete") selectedJobIds.add(j.id);
  }
  refresh();
  feedback(`✅ ${selectedJobIds.size} fertige Aufträge ausgewählt.`);
});

el("select-all")?.addEventListener("click", () => {
  for (const j of cachedJobs) selectedJobIds.add(j.id);
  refresh();
  feedback(`Alle ${selectedJobIds.size} Aufträge ausgewählt.`);
});

el("select-none")?.addEventListener("click", () => {
  selectedJobIds.clear();
  refresh();
  feedback("Auswahl aufgehoben.");
});

el("header-select-all")?.addEventListener("change", (e) => {
  const check = e.target.checked;
  for (const j of cachedJobs) {
    if (check) selectedJobIds.add(j.id);
    else selectedJobIds.delete(j.id);
  }
  refresh();
});

el("time-filter")?.addEventListener("change", () => {
  refresh();
});

// Download Handlers
el("download-combined-pdf")?.addEventListener("click", async () => {
  let targetIds = Array.from(selectedJobIds);
  if (targetIds.length === 0) {
    // If none selected, take all completed jobs in view
    targetIds = cachedJobs.filter(j => j.status === "complete" && j.artifact_id).map(j => j.id);
  }
  if (targetIds.length === 0) {
    feedback("⚠️ Keine fertigen Aufträge zum Erstellen der PDF vorhanden.");
    return;
  }
  await downloadBlob("/api/download/pdf", { job_ids: targetIds }, "notebooklm_batch_zusammenfassung.pdf");
});

el("download-zip")?.addEventListener("click", async () => {
  let targetIds = Array.from(selectedJobIds);
  if (targetIds.length === 0) {
    targetIds = cachedJobs.filter(j => j.status === "complete" && j.artifact_id).map(j => j.id);
  }
  if (targetIds.length === 0) {
    feedback("⚠️ Keine fertigen Aufträge zum Herunterladen ausgewählt.");
    return;
  }
  await downloadBlob("/api/download/zip", { job_ids: targetIds }, "notebooklm_batch_dateien.zip");
});

// Notebooks & Login Handlers
async function loadNotebooks() {
  const reloginBtn = el("relogin-btn");
  try {
    const response = await fetch("/api/notebooks");
    const data = await response.json();
    const sel = el("notebook-select");
    sel.replaceChildren();
    if (!response.ok) {
      const opt = document.createElement("option");
      opt.textContent = "⚠️ Google-Sitzung abgelaufen. Klicke 'Google neu anmelden'";
      sel.append(opt);
      if (reloginBtn) reloginBtn.style.display = "inline-block";
      feedback("⚠️ Google-Sitzung abgelaufen. Klicke auf 'Google neu anmelden'.");
      return;
    }
    if (reloginBtn) reloginBtn.style.display = "none";
    const list = Array.isArray(data) ? data : (data.notebooks || []);
    if (!list || list.length === 0) {
      const opt = document.createElement("option");
      opt.textContent = "Keine Notebooks gefunden";
      sel.append(opt);
      return;
    }
    const def = document.createElement("option");
    def.value = "";
    def.textContent = "-- Notebook auswählen --";
    sel.append(def);
    for (const nb of list) {
      const opt = document.createElement("option");
      opt.value = nb.id;
      opt.textContent = `${nb.title} (${nb.id.slice(0, 8)}…)`;
      sel.append(opt);
    }
  } catch (_) {
    if (reloginBtn) reloginBtn.style.display = "inline-block";
  }
}

el("relogin-btn")?.addEventListener("click", async () => {
  const btn = el("relogin-btn");
  btn.disabled = true;
  btn.textContent = "Verbinde mit Google…";
  feedback("Verbinde mit Google... Bitte Anmeldung im Browserfenster abschliessen.");
  try {
    const res = await request("/api/auth/login", {});
    feedback("✅ " + (res.message || "Google-Authentifizierung erfolgreich aktualisiert!"));
    btn.style.display = "none";
    await loadNotebooks();
  } catch (err) {
    feedback("Anmeldung fehlgeschlagen: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Google neu anmelden";
  }
});

el("apply-notebook")?.addEventListener("click", () => {
  const selectedId = el("notebook-select").value;
  if (!selectedId) {
    feedback("Bitte wähle zuerst ein Notebook aus der Liste aus.");
    return;
  }
  try {
    const data = JSON.parse(el("editor").value);
    const jobs = Array.isArray(data) ? data : data.jobs;
    if (Array.isArray(jobs)) {
      for (const j of jobs) j.notebook_id = selectedId;
      el("editor").value = JSON.stringify(data, null, 2);
      feedback(`Notebook ID ${selectedId} auf ${jobs.length} Job(s) angewendet.`);
    }
  } catch (_) {
    el("editor").value = el("editor").value
      .replaceAll("REPLACE_WITH_NOTEBOOK_ID", selectedId)
      .replaceAll("YOUR_NOTEBOOK_ID", selectedId);
    feedback("Platzhalter-IDs mit " + selectedId + " ersetzt.");
  }
});

el("copy-notebook-id")?.addEventListener("click", () => {
  const selectedId = el("notebook-select").value;
  if (!selectedId) {
    feedback("Bitte wähle zuerst ein Notebook aus der Liste aus.");
    return;
  }
  navigator.clipboard.writeText(selectedId).then(() => {
    feedback("Notebook-ID kopiert: " + selectedId);
  }).catch(() => {
    feedback("Notebook-ID: " + selectedId);
  });
});

el("copy-ai-prompt")?.addEventListener("click", () => {
  const text = el("ai-prompt-text")?.textContent || "";
  navigator.clipboard.writeText(text).then(() => {
    feedback("AI Prompt Spezifikation in Zwischenablage kopiert!");
  }).catch(() => {
    feedback("Bitte den Prompt-Text oben manuell kopieren.");
  });
});

el("open-file-btn")?.addEventListener("click", () => el("file")?.click());

el("file")?.addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 1024 * 1024) { feedback("JSON-Datei überschreitet 1 MiB"); return; }
  try {
    const text = await file.text();
    JSON.parse(text);
    el("editor").value = text;
    await action("/api/validate");
  } catch (error) { feedback(error.message); }
});

el("validate")?.addEventListener("click", () => action("/api/validate"));
el("dry")?.addEventListener("click", () => action("/api/dry-run"));

el("submit")?.addEventListener("click", async () => {
  const selId = el("notebook-select")?.value;
  let text = el("editor").value;
  if (selId && (text.includes("REPLACE_WITH_NOTEBOOK_ID") || text.includes("YOUR_NOTEBOOK_ID"))) {
    text = text.replaceAll("REPLACE_WITH_NOTEBOOK_ID", selId).replaceAll("YOUR_NOTEBOOK_ID", selId);
    el("editor").value = text;
    feedback("Ausgewählte Notebook-ID (" + selId + ") automatisch eingesetzt.");
  }
  await action("/api/submit");
});

el("refresh")?.addEventListener("click", () => refresh().catch(error => feedback(error.message)));

loadNotebooks().catch(() => {});
refresh().catch(error => feedback(error.message));
setInterval(() => refresh().catch(error => feedback(error.message)), 5000);
