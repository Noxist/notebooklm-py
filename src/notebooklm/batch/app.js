"use strict";
const el = id => document.getElementById(id);
const feedback = message => { el("feedback").textContent = message; };
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data));
  return data;
}
async function action(path) {
  try {
    const payload = JSON.parse(el("editor").value);
    const result = await request(path, payload);
    feedback(JSON.stringify(result, null, 2));
    await refresh();
  } catch (error) { feedback(error.message); }
}
function button(label, action) {
  const node = document.createElement("button");
  node.textContent = label;
  node.addEventListener("click", async () => {
    node.disabled = true;
    try { await action(); await refresh(); }
    catch (error) { feedback(error.message); }
    finally { node.disabled = false; }
  });
  return node;
}
const drafts = new Map();
async function refresh() {
  const jobs = await request("/api/jobs");
  const active = document.activeElement;
  const activeJobId = active?.dataset?.jobId;
  const cursor = (active && active.tagName === "INPUT") ? [active.selectionStart, active.selectionEnd] : null;
  el("jobs").replaceChildren();
  for (const job of jobs) {
    const row = document.createElement("tr");
    const cells = Array.from({length: 4}, () => document.createElement("td"));
    cells[0].textContent = job.id + (job.job?.title ? " — " + job.job.title : "");
    cells[1].textContent = job.status + (job.remote_status ? " / Google: " + job.remote_status : "");
    cells[2].textContent = [job.artifact_id, job.error].filter(Boolean).join("\n");
    if (["failed", "uncertain"].includes(job.status)) {
      if (job.status !== "uncertain") cells[3].append(button("Retry / resume", () => request("/api/jobs/" + encodeURIComponent(job.id) + "/retry", {})));
      if (job.status === "uncertain") {
        const id = document.createElement("input");
        id.placeholder = "Existing artifact ID";
        id.setAttribute("aria-label", "Existing artifact ID for " + job.id);
        id.dataset.jobId = job.id;
        if (drafts.has(job.id)) id.value = drafts.get(job.id);
        id.addEventListener("input", () => drafts.set(job.id, id.value));
        cells[3].append(id, button("Attach and resume", async () => {
          await request("/api/jobs/" + encodeURIComponent(job.id) + "/attach", {artifact_id: id.value});
          drafts.delete(job.id);
        }));
      }
    }
    row.append(...cells); el("jobs").append(row);
  }
  if (activeJobId) {
    const nextActive = el("jobs").querySelector(`input[data-job-id="${CSS.escape(activeJobId)}"]`);
    if (nextActive) {
      nextActive.focus();
      if (cursor && cursor[0] !== null) {
        try { nextActive.setSelectionRange(cursor[0], cursor[1]); } catch (_) {}
      }
    }
  }
}
el("file").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 1024 * 1024) { feedback("JSON file exceeds 1 MiB"); return; }
  try {
    const text = await file.text(); JSON.parse(text); el("editor").value = text;
    await action("/api/validate");
  } catch (error) { feedback(error.message); }
});
el("validate").addEventListener("click", () => action("/api/validate"));
el("dry").addEventListener("click", () => action("/api/dry-run"));
el("submit").addEventListener("click", () => action("/api/submit"));
el("refresh").addEventListener("click", () => refresh().catch(error => feedback(error.message)));
request("/api/schema").then(data => { el("schema").textContent = JSON.stringify(data, null, 2); }).catch(error => feedback(error.message));
refresh().catch(error => feedback(error.message));
setInterval(() => refresh().catch(error => feedback(error.message)), 5000);
