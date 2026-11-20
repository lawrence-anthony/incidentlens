"use strict";
const $ = (id) => document.getElementById(id);

// Builds elements with textContent only; untrusted log and model text never becomes HTML.
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child) node.append(child);
  return node;
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  return data;
}

function say(text, isError = false) {
  $("message").textContent = text;
  $("message").className = "message" + (isError ? " error" : "");
}

function busy(on) {
  document.querySelectorAll("button").forEach((b) => { b.disabled = on; });
}

const chip = (id) => el("button", { class: "chip", type: "button", text: id, onclick: () => locate(id) });

function locate(id) {
  const item = document.querySelector(`[data-evidence="${CSS.escape(id)}"]`);
  if (!item) return;
  item.scrollIntoView({ behavior: "smooth", block: "center" });
  item.classList.add("flash");
  setTimeout(() => item.classList.remove("flash"), 1500);
}

async function loadStatus() {
  try {
    const health = await api("/health");
    $("status").replaceChildren(...Object.entries(health).map(([name, state]) => el("span", {
      class: "badge " + (state === "connected" || state === "configured" ? "ok" : state.startsWith("error") ? "err" : ""),
      text: `${name.replace("_", " ")}: ${state}`,
    })));
  } catch (error) {
    $("status").replaceChildren(el("span", { class: "badge err", text: "status unavailable" }));
  }
}

async function loadScenarios() {
  const list = await api("/api/scenarios");
  $("scenarios").replaceChildren(...list.map((s) => el("button", {
    class: "scenario", type: "button", onclick: () => startScenario(s.slug),
  }, el("span", { text: s.title }), el("small", { text: s.description }))));
}

async function loadHistory() {
  try {
    const rows = await api("/api/incidents");
    $("history").replaceChildren(...rows.map((r) => el("li", {},
      el("button", { type: "button", text: r.title, onclick: () => openIncident(r.id) }),
      el("span", { class: "muted small", text: `  ${r.id} · ${new Date(r.created_at).toLocaleString()} · ${
        r.verdict ? r.verdict.replace("_", " ") : "not analyzed"}` }))));
    if (!rows.length) $("history").replaceChildren(el("li", { class: "muted", text: "No investigations yet." }));
  } catch (error) {
    $("history").replaceChildren(el("li", { class: "muted", text: `History unavailable: ${error.message}` }));
  }
}

async function startScenario(slug) {
  await investigate(() => api("/api/incidents", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario: slug }),
  }));
}

async function investigate(create) {
  busy(true);
  try {
    say("Storing evidence in Object Storage and PostgreSQL...");
    const detail = await create();
    render(detail);
    await analyze(detail.incident.id);
  } catch (error) {
    say(`Error: ${error.message}`, true);
  } finally {
    busy(false);
    loadHistory();
  }
}

async function analyze(id) {
  say("Evidence stored and hashed. Asking qwen3-8-27b, then verifying every citation...");
  try {
    await api(`/api/incidents/${encodeURIComponent(id)}/analyze`, { method: "POST" });
    render(await api(`/api/incidents/${encodeURIComponent(id)}`));
    say("Analysis verified and saved.");
  } catch (error) {
    say(`Analysis failed: ${error.message}. The timeline above is still valid.`, true);
    const retry = el("button", { type: "button", class: "secondary", text: "Retry analysis",
      onclick: async () => { busy(true); await analyze(id); busy(false); loadHistory(); } });
    $("verdict").replaceChildren(el("h2", { text: "Not analyzed yet" }), retry);
    $("verdict").className = "verdict";
  }
}

async function openIncident(id) {
  busy(true);
  try {
    const detail = await api(`/api/incidents/${encodeURIComponent(id)}`);
    render(detail);
    say(detail.analysis ? "Loaded the saved analysis from PostgreSQL. No model call was made." : "Not analyzed yet.");
    if (!detail.analysis) {
      $("verdict").replaceChildren(el("h2", { text: "Not analyzed yet" }),
        el("button", { type: "button", text: "Analyze", onclick: async () => { busy(true); await analyze(id); busy(false); loadHistory(); } }));
    }
  } catch (error) {
    say(`Error: ${error.message}`, true);
  } finally {
    busy(false);
  }
}

function render(detail) {
  $("result").hidden = false;
  const { incident, evidence, signals, analysis } = detail;
  const byId = Object.fromEntries(evidence.map((e) => [e.evidence_id, e]));
  const top = analysis?.hypotheses?.find((h) => !h.rejected);
  const cited = new Set(top ? top.supporting.map((s) => s.evidence_id) : []);
  const linked = new Set(signals.linked_changes.map((l) => l.change_id));
  const after = new Set(signals.after_failure_changes);

  renderVerdict(incident, analysis, top);

  const order = [...signals.timeline, ...signals.untimed];
  $("timeline").replaceChildren(...order.map((id) => {
    const e = byId[id];
    const isChange = e.source_kind === "change_event";
    const classes = [isChange ? "change" : e.level || "", cited.has(id) ? "cited" : "", after.has(id) ? "after" : ""];
    const tags = [];
    if (id === signals.first_failure) tags.push(el("span", { class: "tag first", text: "first failure" }));
    if (linked.has(id)) tags.push(el("span", { class: "tag linked", text: "linked change" }));
    if (after.has(id)) tags.push(el("span", { class: "tag after", text: "after first failure" }));
    for (const sig of signals.error_signatures[id] || []) tags.push(el("span", { class: "tag", text: sig }));
    const who = isChange ? [e.event_type, e.actor, e.target].filter(Boolean).join(" · ") : [e.level, e.component].filter(Boolean).join(" · ");
    return el("li", { class: classes.join(" "), "data-evidence": id, title: e.raw },
      el("div", { class: "meta" }, chip(id), el("span", { text: e.ts ? e.ts.replace("T", " ").replace("Z", " UTC") : "no timestamp" }),
        el("span", { text: who }), ...tags),
      el("div", { class: "text", text: e.message }));
  }));

  renderHypotheses(analysis);
  renderUnknowns(analysis);
  renderActions(analysis);
  renderVerification(analysis, signals);
  renderCustody(incident);
}

function renderVerdict(incident, analysis, top) {
  const box = $("verdict");
  if (!analysis) {
    box.className = "verdict";
    box.replaceChildren(el("h2", { text: incident.title }), el("p", { class: "muted", text: "Waiting for analysis." }));
    return;
  }
  const probable = analysis.verdict === "probable_cause";
  box.className = "verdict " + (probable ? "probable" : "insufficient");
  const heading = probable
    ? `Probable cause (${top?.confidence ?? "low"} confidence)`
    : "Insufficient evidence: root cause not established";
  const parts = [el("p", { class: "muted small", text: incident.title }), el("h2", { text: heading })];
  if (probable && top) parts.push(el("p", {}, el("strong", { text: top.statement })));
  parts.push(el("p", { text: analysis.summary }));
  if (analysis.model_verdict !== analysis.verdict) {
    parts.push(el("p", { class: "adjust", text: `The model proposed "${analysis.model_verdict.replace("_", " ")}"; the verifier lowered it because the evidence does not meet the policy.` }));
  }
  box.replaceChildren(...parts);
}

function renderHypotheses(analysis) {
  if (!analysis) return $("hypotheses").replaceChildren(el("p", { class: "muted", text: "No analysis yet." }));
  if (!analysis.hypotheses.length) return $("hypotheses").replaceChildren(el("p", { class: "muted", text: "The model proposed no hypotheses." }));
  $("hypotheses").replaceChildren(...analysis.hypotheses.map((h) => el("div", { class: "hyp" + (h.rejected ? " rejected" : "") },
    el("h3", {}, el("span", { class: "conf " + (h.rejected ? "rejected" : h.confidence), text: h.rejected ? "rejected" : h.confidence }),
      document.createTextNode(` ${h.id}: ${h.statement}`)),
    el("div", { class: "small" }, el("span", { class: "muted", text: "Supports: " }),
      ...(h.supporting.length ? h.supporting.map((s) => chip(s.evidence_id)) : [el("span", { text: "none valid" })])),
    h.contradicting.length ? el("div", { class: "small" }, el("span", { class: "muted", text: "Contradicts: " }), ...h.contradicting.map((c) => chip(c.evidence_id))) : null,
    el("p", { class: "small", text: h.reasoning }),
    ...h.adjustments.map((a) => el("p", { class: "adjust", text: `Capped from ${a.from} to ${a.to} (${a.rule}): ${a.reason}` })))));
}

function renderUnknowns(analysis) {
  const items = analysis?.missing_evidence ?? [];
  $("unknowns").replaceChildren(...(items.length ? items.map((m) => el("li", {}, el("strong", { text: m.need }), el("div", { class: "small muted", text: m.why })))
    : [el("li", { class: "muted", text: analysis ? "The model listed no missing evidence." : "No analysis yet." })]));
}

function renderActions(analysis) {
  const items = analysis?.recovery_actions ?? [];
  $("actions").replaceChildren(...(items.length ? items.map((a) => el("li", { class: "action" },
    el("div", {}, el("strong", { text: a.action })),
    el("div", { class: "small" }, el("span", { class: `risk-${a.risk}`, text: `Risk: ${a.risk}` }),
      el("span", { class: "lock", text: "  · Requires human approval · Not executed" }),
      a.flags.includes("broad_or_destructive") ? el("span", { class: "risk-high", text: `  · Flagged: ${a.risky_terms.join(", ")}` }) : null),
    a.rationale ? el("div", { class: "small", text: a.rationale }) : null,
    a.verify ? el("div", { class: "small muted", text: `Verify: ${a.verify}` }) : null,
    a.rollback ? el("div", { class: "small muted", text: `Rollback: ${a.rollback}` }) : null,
    a.evidence_ids.length ? el("div", {}, ...a.evidence_ids.map(chip)) : el("div", { class: "small muted", text: "General step (no evidence cited)" })))
    : [el("li", { class: "muted", text: analysis ? "No actions proposed." : "No analysis yet." })]));
}

function renderVerification(analysis, signals) {
  const rows = [["First failure", signals.first_failure ?? "none found"],
    ["Linked changes", signals.linked_changes.map((l) => `${l.change_id} via ${l.shared_tokens.join(", ")}`).join("; ") || "none"]];
  if (analysis) {
    const v = analysis.verification;
    const adjusted = analysis.hypotheses.reduce((n, h) => n + h.adjustments.length, 0);
    rows.push(["Model", `${analysis.model} · prompt ${analysis.prompt_version} · policy ${analysis.policy_version}`],
      ["Model verdict → final", `${analysis.model_verdict} → ${analysis.verdict}`],
      ["Citations checked", `${v.citations_total} total, ${v.citations_invalid.length} invalid${v.citations_invalid.length ? ` (${v.citations_invalid.join(", ")})` : ""}`],
      ["Unknown IDs in text", v.free_text_unknown_ids.join(", ") || "none"],
      ["Quotes matched", `${v.quotes_matched} of ${v.quotes_checked}`],
      ["Confidence caps applied", String(adjusted)],
      ["Evidence hash re-verified", v.evidence_sha256_verified ? "yes, before analysis" : "no"],
      ["Latency · tokens", `${analysis.latency_ms ?? "?"} ms · ${analysis.prompt_tokens ?? "?"} in / ${analysis.completion_tokens ?? "?"} out`]);
    for (const note of v.notes) rows.push(["Note", note]);
  }
  $("verification").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
}

function renderCustody(incident) {
  const rows = [["Incident", incident.id]];
  for (const a of incident.artifacts) {
    rows.push([a.kind.replace("_", " "), `${a.filename} · ${a.size_bytes} bytes`], ["object key", a.object_key], ["SHA-256", a.sha256]);
  }
  for (const w of incident.parse_warnings || []) rows.push(["Parse warning", w]);
  $("custody").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
}

$("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData($("upload"));
  investigate(() => api("/api/incidents", { method: "POST", body: form }));
});

loadStatus();
loadScenarios().catch((error) => say(`Could not load scenarios: ${error.message}`, true));
loadHistory();
