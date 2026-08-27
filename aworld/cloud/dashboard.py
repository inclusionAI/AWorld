"""Minimal, dependency-free operations dashboard for AWorld Cloud."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from aworld.cloud.settings import CloudSettings

_WORKER_HEALTH_MAX_AGE_SECONDS = 20.0

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>AWorld Cloud</title>
  <style>
    :root {
      --black: #101318;
      --muted: #667085;
      --line: #d9dee8;
      --soft: #f5f7fa;
      --white: #ffffff;
      --blue: #175cd3;
      --blue-soft: #eaf2ff;
      --danger: #b42318;
      --success: #067647;
      --warning: #b54708;
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--black);
      background: var(--white);
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-width: 320px; background: var(--white); }
    button, input { font: inherit; }
    button, a { -webkit-tap-highlight-color: transparent; }
    .topbar {
      height: 62px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 28px;
      border-bottom: 1px solid var(--line);
    }
    .brand { display: flex; align-items: baseline; gap: 10px; }
    .brand strong { font-size: 17px; letter-spacing: -0.02em; }
    .brand span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .09em; }
    .health { display: flex; align-items: center; gap: 18px; font-size: 13px; }
    .health-item { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #98a2b3; }
    .dot.ok { background: var(--success); }
    .dot.bad { background: var(--danger); }
    .shell { max-width: 1440px; margin: 0 auto; padding: 30px 28px 56px; }
    .heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 22px; }
    h1 { font-size: 26px; line-height: 1.2; letter-spacing: -0.035em; margin: 0 0 6px; }
    .subtitle { margin: 0; color: var(--muted); font-size: 14px; }
    .controls { display: flex; align-items: center; gap: 14px; white-space: nowrap; }
    .auto-refresh { color: var(--muted); font-size: 13px; display: inline-flex; gap: 7px; align-items: center; }
    .auto-refresh input { accent-color: var(--blue); }
    .button {
      border: 1px solid var(--black);
      color: var(--white);
      background: var(--black);
      border-radius: 6px;
      padding: 8px 13px;
      cursor: pointer;
      font-weight: 600;
      font-size: 13px;
    }
    .button:hover { background: var(--blue); border-color: var(--blue); }
    .button:disabled { cursor: wait; opacity: .55; }
    .layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px; }
    .layout.with-detail { grid-template-columns: minmax(0, 1.6fr) minmax(340px, .8fr); }
    .panel { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--white); }
    .panel-header { min-height: 52px; display: flex; align-items: center; justify-content: space-between; padding: 0 16px; border-bottom: 1px solid var(--line); }
    .panel-header h2 { margin: 0; font-size: 14px; letter-spacing: -.01em; }
    .updated { color: var(--muted); font-size: 12px; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; min-width: 850px; border-collapse: collapse; font-size: 13px; }
    th { padding: 10px 12px; text-align: left; color: var(--muted); background: var(--soft); font-size: 11px; text-transform: uppercase; letter-spacing: .055em; font-weight: 650; }
    td { padding: 13px 12px; border-top: 1px solid #eaecf0; vertical-align: middle; }
    tbody tr { cursor: pointer; }
    tbody tr:hover, tbody tr:focus { background: #f8faff; outline: none; }
    tbody tr.selected { box-shadow: inset 3px 0 var(--blue); background: var(--blue-soft); }
    .run-id { display: block; max-width: 145px; overflow: hidden; text-overflow: ellipsis; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--blue); font-weight: 650; white-space: nowrap; }
    .primary { display: block; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .secondary { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge { display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; font-size: 11px; font-weight: 650; text-transform: capitalize; background: var(--white); }
    .badge.succeeded { color: var(--success); border-color: #a6f4c5; background: #ecfdf3; }
    .badge.failed, .badge.cancelled { color: var(--danger); border-color: #fecdca; background: #fef3f2; }
    .badge.running, .badge.claimed { color: var(--blue); border-color: #b2ccff; background: var(--blue-soft); }
    .badge.queued { color: var(--warning); border-color: #fedf89; background: #fffaeb; }
    .state { min-height: 220px; display: grid; place-items: center; padding: 34px; color: var(--muted); text-align: center; }
    .state strong { display: block; color: var(--black); margin-bottom: 5px; }
    .state.error strong { color: var(--danger); }
    .spinner { width: 18px; height: 18px; border: 2px solid var(--line); border-top-color: var(--blue); border-radius: 50%; animation: spin .75s linear infinite; margin: 0 auto 10px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .detail { min-width: 0; }
    .detail[hidden], .table-wrap[hidden], .state[hidden] { display: none; }
    .close { border: 0; background: transparent; color: var(--muted); font-size: 22px; line-height: 1; cursor: pointer; padding: 4px; }
    .close:hover { color: var(--black); }
    .detail-body { padding: 16px; max-height: calc(100vh - 175px); overflow: auto; }
    .detail-id { margin: 0 0 16px; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); overflow-wrap: anywhere; }
    .facts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 18px; margin: 0 0 18px; }
    .fact dt { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 3px; }
    .fact dd { margin: 0; font-size: 13px; overflow-wrap: anywhere; }
    .atif-actions { display: flex; align-items: stretch; gap: 8px; margin-bottom: 20px; }
    .preview-primary, .download-link { border: 1px solid #b2ccff; border-radius: 6px; padding: 9px 11px; color: var(--blue); background: var(--blue-soft); font-weight: 650; font-size: 12px; text-decoration: none; cursor: pointer; }
    .preview-primary { flex: 1; text-align: left; }
    .preview-primary:hover, .download-link:hover { border-color: var(--blue); }
    .detail-section { border-top: 1px solid var(--line); padding-top: 16px; margin-top: 16px; }
    .detail-section h3 { margin: 0 0 10px; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
    .event, .file { border-top: 1px solid #eaecf0; padding: 10px 0; }
    .event:first-child, .file:first-child { border-top: 0; }
    .event-head, .file { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
    .event-name, .file-name { font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
    .event-meta, .file-meta { color: var(--muted); font-size: 11px; white-space: nowrap; }
    .event pre { margin: 7px 0 0; padding: 8px; background: var(--soft); border-radius: 4px; color: #344054; font: 11px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
    .file-preview-button { border: 0; padding: 0; color: var(--blue); background: transparent; text-align: left; font: inherit; font-weight: 650; cursor: pointer; }
    .file-preview-button:hover { text-decoration: underline; }
    .canonical { color: var(--blue); font-size: 10px; text-transform: uppercase; letter-spacing: .04em; margin-left: 6px; }
    .minor-state { color: var(--muted); font-size: 12px; padding: 6px 0; }
    .preview { border: 1px solid #b2ccff; border-radius: 7px; margin: 0 0 20px; overflow: hidden; background: var(--white); }
    .preview[hidden] { display: none; }
    .preview-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 11px 12px; background: var(--blue-soft); border-bottom: 1px solid #b2ccff; }
    .preview-title { min-width: 0; }
    .preview-name { display: block; font-size: 12px; font-weight: 700; overflow-wrap: anywhere; }
    .preview-meta { display: block; color: var(--muted); font-size: 11px; margin-top: 3px; }
    .preview-actions { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
    .preview-download { color: var(--blue); background: var(--white); border: 1px solid #b2ccff; border-radius: 5px; padding: 6px 9px; text-decoration: none; font-size: 11px; font-weight: 650; }
    .preview-download:hover { border-color: var(--blue); }
    .preview-close { border: 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 19px; line-height: 1; padding: 4px; }
    .preview-close:hover { color: var(--black); }
    .preview-body { min-height: 86px; max-height: 420px; overflow: auto; }
    .preview-state { padding: 24px 14px; color: var(--muted); text-align: center; font-size: 12px; }
    .preview-state.error { color: var(--danger); }
    .preview-content { margin: 0; padding: 13px; color: #1d2939; background: var(--white); font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; tab-size: 2; }
    .preview-notice { padding: 8px 12px; border-top: 1px solid var(--line); color: var(--warning); background: #fffaeb; font-size: 11px; }
    @media (max-width: 980px) {
      .layout.with-detail { grid-template-columns: 1fr; }
      .detail-body { max-height: none; }
    }
    @media (max-width: 680px) {
      .topbar { height: auto; padding: 16px; align-items: flex-start; flex-direction: column; gap: 12px; }
      .health { width: 100%; justify-content: space-between; }
      .shell { padding: 22px 16px 40px; }
      .heading { align-items: flex-start; flex-direction: column; }
      .controls { width: 100%; justify-content: space-between; }
      .facts { grid-template-columns: 1fr; }
      .atif-actions { flex-direction: column; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand"><strong>AWorld Cloud</strong><span>Operations</span></div>
    <div class="health" aria-label="Service health">
      <span class="health-item"><i id="serverDot" class="dot"></i>Server <strong id="serverHealth">Checking</strong></span>
      <span class="health-item"><i id="workerDot" class="dot"></i>Worker <strong id="workerHealth">Checking</strong></span>
    </div>
  </header>
  <main class="shell">
    <div class="heading">
      <div><h1>Runs</h1><p class="subtitle">Live execution and benchmark activity</p></div>
      <div class="controls">
        <label class="auto-refresh"><input id="autoRefresh" type="checkbox" checked> Auto refresh</label>
        <button id="refreshButton" class="button" type="button">Refresh</button>
      </div>
    </div>
    <div id="layout" class="layout">
      <section class="panel" aria-labelledby="runsHeading">
        <div class="panel-header"><h2 id="runsHeading">Recent runs</h2><span id="lastUpdated" class="updated">Not updated</span></div>
        <div id="runsState" class="state" role="status"><div><div class="spinner"></div>Loading runs…</div></div>
        <div id="tableWrap" class="table-wrap" hidden>
          <table>
            <thead><tr><th>Run</th><th>Status</th><th>Mode</th><th>Task / benchmark</th><th>Created</th><th>Duration</th><th>Reward</th></tr></thead>
            <tbody id="runsBody"></tbody>
          </table>
        </div>
      </section>
      <section id="detailPanel" class="panel detail" aria-labelledby="detailHeading" hidden>
        <div class="panel-header"><h2 id="detailHeading">Run details</h2><button id="closeDetail" class="close" type="button" aria-label="Close run details">×</button></div>
        <div id="detailBody" class="detail-body"></div>
      </section>
    </div>
  </main>
  <script>
    "use strict";
    const API = "/api/v1/cloud";
    const REFRESH_MS = 10000;
    const PREVIEW_BYTES = 256 * 1024;
    const ui = {
      layout: document.querySelector("#layout"),
      runsState: document.querySelector("#runsState"),
      tableWrap: document.querySelector("#tableWrap"),
      runsBody: document.querySelector("#runsBody"),
      detailPanel: document.querySelector("#detailPanel"),
      detailBody: document.querySelector("#detailBody"),
      refreshButton: document.querySelector("#refreshButton"),
      autoRefresh: document.querySelector("#autoRefresh"),
      lastUpdated: document.querySelector("#lastUpdated"),
      serverDot: document.querySelector("#serverDot"),
      serverHealth: document.querySelector("#serverHealth"),
      workerDot: document.querySelector("#workerDot"),
      workerHealth: document.querySelector("#workerHealth"),
    };
    let selectedRunId = null;
    let refreshPending = false;
    let detailRequest = 0;
    let previewRequest = 0;
    let previewController = null;
    let previewState = null;

    function element(tag, className, text) {
      const item = document.createElement(tag);
      if (className) item.className = className;
      if (text !== undefined) item.textContent = text;
      return item;
    }

    async function fetchJSON(path) {
      const response = await fetch(path, {headers: {Accept: "application/json"}});
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const payload = await response.json();
          message = payload.error?.message || message;
        } catch (_) {}
        throw new Error(message);
      }
      return response.json();
    }

    function formatTime(value) {
      if (!value) return "—";
      const date = new Date(value);
      return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
    }

    function formatDuration(value) {
      if (value === null || value === undefined) return "—";
      const seconds = Math.max(0, Number(value));
      if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
      const minutes = Math.floor(seconds / 60);
      return `${minutes}m ${Math.round(seconds % 60)}s`;
    }

    function formatReward(run) {
      const reward = run.benchmark_outcome?.reward;
      return reward === null || reward === undefined ? "—" : String(reward);
    }

    function taskLabels(run) {
      if (run.mode === "benchmark" && run.benchmark) {
        return [`${run.benchmark.dataset} / ${run.benchmark.task_id}`, run.task];
      }
      return [run.task, run.model || "Query"];
    }

    function setHealth(kind, health) {
      const dot = kind === "server" ? ui.serverDot : ui.workerDot;
      const label = kind === "server" ? ui.serverHealth : ui.workerHealth;
      dot.className = `dot ${health.ok ? "ok" : "bad"}`;
      label.textContent = health.ok ? "Healthy" : (health.status || "Unavailable");
      label.title = health.detail || "";
    }

    async function loadHealth() {
      try {
        const health = await fetchJSON("/dashboard/health");
        setHealth("server", health.server);
        setHealth("worker", health.worker);
      } catch (error) {
        setHealth("server", {ok: false, status: "Unavailable", detail: error.message});
        setHealth("worker", {ok: false, status: "Unknown", detail: error.message});
      }
    }

    function showRunsState(title, message, isError = false) {
      ui.tableWrap.hidden = true;
      ui.runsState.hidden = false;
      ui.runsState.className = `state${isError ? " error" : ""}`;
      ui.runsState.replaceChildren();
      const content = element("div");
      content.append(element("strong", "", title), document.createTextNode(message));
      ui.runsState.append(content);
    }

    function renderRuns(runs) {
      ui.runsBody.replaceChildren();
      if (!runs.length) {
        showRunsState("No runs yet", "Submitted query and benchmark runs will appear here.");
        return;
      }
      ui.runsState.hidden = true;
      ui.tableWrap.hidden = false;
      for (const run of runs) {
        const row = element("tr");
        row.tabIndex = 0;
        row.dataset.runId = run.id;
        if (run.id === selectedRunId) row.classList.add("selected");
        const runCell = element("td");
        runCell.append(element("span", "run-id", run.id));
        const stateCell = element("td");
        stateCell.append(element("span", `badge ${run.state}`, run.state));
        const modeCell = element("td", "", run.mode);
        const taskCell = element("td");
        const [primary, secondary] = taskLabels(run);
        taskCell.append(element("span", "primary", primary), element("span", "secondary", secondary));
        row.append(
          runCell,
          stateCell,
          modeCell,
          taskCell,
          element("td", "", formatTime(run.created_at)),
          element("td", "", formatDuration(run.duration_seconds)),
          element("td", "", formatReward(run)),
        );
        const open = () => openRun(run.id);
        row.addEventListener("click", open);
        row.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
        });
        ui.runsBody.append(row);
      }
    }

    async function loadRuns({quiet = false} = {}) {
      if (refreshPending) return;
      refreshPending = true;
      ui.refreshButton.disabled = true;
      if (!quiet && !ui.runsBody.children.length) {
        ui.runsState.hidden = false;
        ui.tableWrap.hidden = true;
      }
      try {
        const payload = await fetchJSON(`${API}/runs?limit=100`);
        renderRuns(payload.items || []);
        ui.lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        if (selectedRunId) await loadRunDetail(selectedRunId);
      } catch (error) {
        showRunsState("Could not load runs", error.message, true);
      } finally {
        refreshPending = false;
        ui.refreshButton.disabled = false;
      }
    }

    function fact(label, value) {
      const wrapper = element("div", "fact");
      wrapper.append(element("dt", "", label), element("dd", "", value ?? "—"));
      return wrapper;
    }

    function renderEvents(events) {
      const section = element("section", "detail-section");
      section.append(element("h3", "", `Events (${events.length})`));
      if (!events.length) {
        section.append(element("div", "minor-state", "No events recorded."));
        return section;
      }
      for (const event of events) {
        const item = element("div", "event");
        const head = element("div", "event-head");
        head.append(element("span", "event-name", event.event_type), element("span", "event-meta", `#${event.sequence} · ${formatTime(event.created_at)}`));
        const payload = element("pre");
        payload.textContent = JSON.stringify(event.payload, null, 2);
        item.append(head, payload);
        section.append(item);
      }
      return section;
    }

    function validDownload(file, runId) {
      const prefix = `${API}/runs/${encodeURIComponent(runId)}/files/`;
      return typeof file.download_url === "string" && file.download_url.startsWith(prefix)
        ? file.download_url
        : `${prefix}${encodeURIComponent(file.id)}`;
    }

    function formatBytes(value) {
      const bytes = Math.max(0, Number(value) || 0);
      if (bytes < 1024) return `${bytes.toLocaleString()} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
    }

    function previewType(file) {
      if (file.trajectory) {
        return `${file.trajectory.format.toUpperCase()} ${file.trajectory.schema_version}`;
      }
      return file.kind;
    }

    function isJSONPreview(file) {
      const path = String(file.relative_path || "").toLowerCase();
      return file.kind === "trajectory" || file.kind === "result" || path.endsWith(".json") || path.endsWith(".atif");
    }

    function clearFilePreview() {
      previewRequest += 1;
      if (previewController) previewController.abort();
      previewController = null;
      previewState = null;
      const host = document.querySelector("#filePreview");
      if (host) { host.hidden = true; host.replaceChildren(); }
    }

    function renderFilePreview(file, run) {
      const host = document.querySelector("#filePreview");
      if (!host || !previewState || previewState.fileId !== file.id) return;
      host.hidden = false;
      host.replaceChildren();
      const head = element("div", "preview-head");
      const title = element("div", "preview-title");
      title.append(
        element("span", "preview-name", file.relative_path),
        element("span", "preview-meta", `${previewType(file)} · ${formatBytes(file.size_bytes)}`),
      );
      const actions = element("div", "preview-actions");
      const download = element("a", "preview-download", "Download");
      download.href = validDownload(file, run.id);
      download.download = "";
      const close = element("button", "preview-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "Close file preview");
      close.addEventListener("click", clearFilePreview);
      actions.append(download, close);
      head.append(title, actions);
      const body = element("div", "preview-body");
      if (previewState.status === "loading") {
        const loading = element("div", "preview-state", "Loading preview…");
        loading.setAttribute("role", "status");
        body.append(loading);
      } else if (previewState.status === "error") {
        const error = element("div", "preview-state error", `Could not preview file: ${previewState.message}`);
        error.setAttribute("role", "alert");
        body.append(error);
      } else if (previewState.status === "empty") {
        body.append(element("div", "preview-state", "This file is empty."));
      } else if (previewState.status === "binary") {
        body.append(element("div", "preview-state", "Binary preview is not available. Use Download to open the complete file."));
      } else {
        const content = element("pre", "preview-content");
        content.textContent = previewState.text;
        body.append(content);
      }
      host.append(head, body);
      if (previewState.truncated) {
        host.append(element("div", "preview-notice", `Preview limited to the first ${formatBytes(PREVIEW_BYTES)}. Download the file to view the rest.`));
      }
    }

    async function openFilePreview(file, run) {
      if (previewController) previewController.abort();
      const request = ++previewRequest;
      previewState = {runId: run.id, fileId: file.id, sha256: file.sha256, status: "loading", truncated: false};
      renderFilePreview(file, run);
      const host = document.querySelector("#filePreview");
      if (host) host.scrollIntoView({block: "nearest"});
      if (Number(file.size_bytes) === 0) {
        previewState = {...previewState, status: "empty"};
        renderFilePreview(file, run);
        return;
      }
      previewController = new AbortController();
      try {
        const response = await fetch(validDownload(file, run.id), {
          headers: {
            Accept: "text/plain, application/json;q=0.9, */*;q=0.1",
            Range: `bytes=0-${PREVIEW_BYTES - 1}`,
          },
          signal: previewController.signal,
        });
        if (!response.ok) {
          let message = `${response.status} ${response.statusText}`;
          try {
            const payload = await response.json();
            message = payload.error?.message || message;
          } catch (_) {}
          throw new Error(message);
        }
        const receivedBytes = new Uint8Array(await response.arrayBuffer());
        const bytes = receivedBytes.slice(0, PREVIEW_BYTES);
        if (request !== previewRequest) return;
        const contentRange = response.headers.get("content-range") || "";
        const totalMatch = contentRange.match(/\/(\d+)$/);
        const totalBytes = totalMatch ? Number(totalMatch[1]) : Math.max(Number(file.size_bytes), receivedBytes.byteLength);
        const truncated = Number.isFinite(totalBytes) && totalBytes > bytes.byteLength;
        if (bytes.slice(0, 8192).includes(0)) {
          previewState = {...previewState, status: "binary", truncated};
        } else {
          let text = new TextDecoder("utf-8", {fatal: false}).decode(bytes);
          if (!truncated && isJSONPreview(file)) {
            try { text = JSON.stringify(JSON.parse(text), null, 2); } catch (_) {}
          }
          previewState = {...previewState, status: "ready", text, truncated};
        }
        renderFilePreview(file, run);
      } catch (error) {
        if (error.name === "AbortError" || request !== previewRequest) return;
        previewState = {...previewState, status: "error", message: error.message, truncated: false};
        renderFilePreview(file, run);
      } finally {
        if (request === previewRequest) previewController = null;
      }
    }

    function renderFiles(files, run) {
      const section = element("section", "detail-section");
      section.append(element("h3", "", `Files (${files.length})`));
      if (!files.length) {
        section.append(element("div", "minor-state", "No files available."));
        return section;
      }
      for (const file of files) {
        const item = element("div", "file");
        const name = element("span", "file-name");
        const preview = element("button", "file-preview-button", file.relative_path);
        preview.type = "button";
        preview.setAttribute("aria-label", `Preview ${file.relative_path}`);
        preview.addEventListener("click", () => openFilePreview(file, run));
        name.append(preview);
        if (file.id === run.canonical_trajectory_file_id) name.append(element("span", "canonical", "Canonical ATIF"));
        item.append(name, element("span", "file-meta", `${file.kind} · ${formatBytes(file.size_bytes)}`));
        section.append(item);
      }
      return section;
    }

    function renderDetail(run, events, files) {
      ui.detailBody.replaceChildren();
      ui.detailBody.append(element("p", "detail-id", run.id));
      const facts = element("dl", "facts");
      facts.append(
        fact("Status", run.state),
        fact("Mode", run.mode),
        fact("Created", formatTime(run.created_at)),
        fact("Duration", formatDuration(run.duration_seconds)),
        fact("Reward", formatReward(run)),
        fact("Exit code", run.exit_code === null ? "—" : String(run.exit_code)),
        fact("Worker", run.worker_id),
        fact("Attempt", String(run.attempt)),
      );
      ui.detailBody.append(facts);
      const canonical = files.find(file => file.id === run.canonical_trajectory_file_id);
      if (canonical) {
        const actions = element("div", "atif-actions");
        const preview = element("button", "preview-primary", "Preview canonical ATIF");
        preview.type = "button";
        preview.addEventListener("click", () => openFilePreview(canonical, run));
        const download = element("a", "download-link", "Download");
        download.href = validDownload(canonical, run.id);
        download.download = "";
        actions.append(preview, download);
        ui.detailBody.append(actions);
      }
      const previewHost = element("section", "preview");
      previewHost.id = "filePreview";
      previewHost.hidden = true;
      previewHost.setAttribute("aria-live", "polite");
      ui.detailBody.append(previewHost);
      if (previewState?.runId === run.id) {
        const selectedFile = files.find(file => file.id === previewState.fileId);
        if (selectedFile) renderFilePreview(selectedFile, run);
        else clearFilePreview();
      }
      if (run.error_message) {
        const error = element("section", "detail-section");
        error.append(element("h3", "", run.error_code || "Run error"), element("div", "minor-state", run.error_message));
        ui.detailBody.append(error);
      }
      ui.detailBody.append(renderEvents(events), renderFiles(files, run));
    }

    async function loadRunDetail(runId) {
      const request = ++detailRequest;
      ui.detailBody.replaceChildren(element("div", "minor-state", "Loading run details…"));
      try {
        const encoded = encodeURIComponent(runId);
        const [run, events, files] = await Promise.all([
          fetchJSON(`${API}/runs/${encoded}`),
          fetchJSON(`${API}/runs/${encoded}/events?limit=1000`),
          fetchJSON(`${API}/runs/${encoded}/files`),
        ]);
        if (request !== detailRequest || selectedRunId !== runId) return;
        renderDetail(run, events.items || [], files.items || []);
      } catch (error) {
        if (request !== detailRequest) return;
        ui.detailBody.replaceChildren(element("div", "minor-state", `Could not load details: ${error.message}`));
      }
    }

    function openRun(runId) {
      if (selectedRunId !== runId) clearFilePreview();
      selectedRunId = runId;
      ui.detailPanel.hidden = false;
      ui.layout.classList.add("with-detail");
      for (const row of ui.runsBody.children) row.classList.toggle("selected", row.dataset.runId === runId);
      loadRunDetail(runId);
      if (window.matchMedia("(max-width: 980px)").matches) {
        window.requestAnimationFrame(() => ui.detailPanel.scrollIntoView({block: "start"}));
      }
    }

    function closeDetail() {
      clearFilePreview();
      selectedRunId = null;
      detailRequest += 1;
      ui.detailPanel.hidden = true;
      ui.layout.classList.remove("with-detail");
      for (const row of ui.runsBody.children) row.classList.remove("selected");
    }

    async function refreshAll(quiet = false) {
      await Promise.all([loadHealth(), loadRuns({quiet})]);
    }

    ui.refreshButton.addEventListener("click", () => refreshAll(false));
    document.querySelector("#closeDetail").addEventListener("click", closeDetail);
    window.setInterval(() => {
      if (ui.autoRefresh.checked && !document.hidden) refreshAll(true);
    }, REFRESH_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && ui.autoRefresh.checked) refreshAll(true);
    });
    refreshAll(false);
  </script>
</body>
</html>
"""


def _worker_health(path: Path) -> dict[str, Any]:
    try:
        modified_at = path.stat().st_mtime
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("worker heartbeat must be an object")
        age_seconds = max(0.0, time.time() - modified_at)
    except (OSError, TypeError, ValueError):
        return {
            "ok": False,
            "status": "Unavailable",
            "detail": "Worker heartbeat has not been observed",
            "age_seconds": None,
            "updated_at": None,
        }
    healthy = (
        payload.get("ok") is True and age_seconds <= _WORKER_HEALTH_MAX_AGE_SECONDS
    )
    return {
        "ok": healthy,
        "status": "Healthy" if healthy else "Stale",
        "detail": (
            "Worker heartbeat is current"
            if healthy
            else "Worker heartbeat is older than 20 seconds"
        ),
        "age_seconds": round(age_seconds, 3),
        "updated_at": datetime.fromtimestamp(modified_at, timezone.utc).isoformat(),
    }


def register_cloud_dashboard(app: FastAPI, settings: CloudSettings) -> None:
    """Register the unversioned operator UI without changing the Cloud API."""

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(
            DASHBOARD_HTML,
            headers={
                "cache-control": "no-store",
                "content-security-policy": (
                    "default-src 'self'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' data:; frame-ancestors 'none'"
                ),
                "x-content-type-options": "nosniff",
            },
        )

    @app.get("/dashboard/health", include_in_schema=False)
    async def dashboard_health() -> dict[str, object]:
        server_ready = bool(getattr(app.state, "cloud_ready", False))
        return {
            "server": {
                "ok": server_ready,
                "status": "Healthy" if server_ready else "Starting",
                "detail": (
                    "Cloud API and SQLite are ready"
                    if server_ready
                    else "Cloud API is starting"
                ),
            },
            "worker": _worker_health(settings.data_root / "worker-health.json"),
        }
