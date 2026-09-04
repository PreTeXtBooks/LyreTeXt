// LyreTeXt API client — one thin wrapper per lyretext/api.py endpoint.
// Every call returns parsed JSON or throws an Error with a readable message.

const LyreAPI = (() => {
  async function req(path, options = {}) {
    const res = await fetch(path, {
      headers: options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json", ...(options.headers || {}) }
        : (options.headers || {}),
      ...options,
    });
    let body = null;
    const text = await res.text();
    if (text) {
      try { body = JSON.parse(text); } catch { body = text; }
    }
    if (!res.ok) {
      const message = (body && typeof body === "object" && body.detail) ? body.detail : (body || res.statusText);
      throw new Error(typeof message === "string" ? message : JSON.stringify(message));
    }
    return body;
  }

  const json = (obj) => JSON.stringify(obj);

  return {
    // -- filesystem / ingestion -------------------------------------------
    browseFolder: (initial) => req(`/api/browse?initial=${encodeURIComponent(initial || "")}`),
    demoPath: () => req("/api/demo-path"),
    readFile: (path) => req(`/api/files?path=${encodeURIComponent(path)}`),

    // -- runs ----------------------------------------------------------------
    listRuns: () => req("/api/runs"),
    startRun: (source, output_dir, temp_dir, run_config, run_id) =>
      req("/api/runs", { method: "POST", body: json({ source, output_dir, temp_dir, run_config, run_id }) }),
    startRunUpload: (formData) => req("/api/runs/upload", { method: "POST", body: formData }),
    getRun: (runId) => req(`/api/runs/${encodeURIComponent(runId)}`),
    getRunStatus: (runId) => req(`/api/runs/${encodeURIComponent(runId)}/status`),

    // -- chapter commands ------------------------------------------------
    chapterCommand: (runId, chapterId, action, instruction) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/commands`, {
        method: "POST", body: json({ action, instruction: instruction || undefined }),
      }),
    chaptersCommandBatch: (runId, chapterIds, action, instruction) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/commands`, {
        method: "POST", body: json({ chapter_ids: chapterIds, action, instruction: instruction || undefined }),
      }),
    remapChapter: (runId, chapterId, sourcePath) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/remap`, {
        method: "POST", body: json({ source_path: sourcePath }),
      }),
    writeChapterFile: (runId, chapterId, target, content) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/file`, {
        method: "PUT", body: json({ target, content }),
      }),
    chapterRender: (runId, chapterId) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/render`),
    chapterFindings: (runId, chapterId) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/findings`),
    chapterCheckpoints: (runId, chapterId) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/checkpoints`),
    revertChapter: (runId, chapterId, checkpointId) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/revert`, {
        method: "POST", body: json({ checkpoint_id: checkpointId }),
      }),
    dismissIssue: (runId, chapterId, issueIndex) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/issues/${issueIndex}/dismiss`, {
        method: "POST",
      }),
    fixIssue: (runId, chapterId, issueIndex) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/issues/${issueIndex}/fix`, {
        method: "POST",
      }),
    // Group variants: one call, one edit_chapter run, for a set of findings the
    // UI has grouped as the same fix repeated at different lines.
    dismissIssueGroup: (runId, chapterId, issueIndices) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/issues/dismiss-group`, {
        method: "POST", body: json({ issue_indices: issueIndices }),
      }),
    restoreIssues: (runId, chapterId, issueIndices) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/issues/restore`, {
        method: "POST", body: json({ issue_indices: issueIndices }),
      }),
    fixIssueGroup: (runId, chapterId, issueIndices) =>
      req(`/api/runs/${encodeURIComponent(runId)}/chapters/${encodeURIComponent(chapterId)}/issues/fix-group`, {
        method: "POST", body: json({ issue_indices: issueIndices }),
      }),

    // -- read / manifest gate ---------------------------------------------
    applyGateDecision: (runId, action, extra) =>
      req(`/api/runs/${encodeURIComponent(runId)}/gate`, { method: "POST", body: json({ action, ...(extra || {}) }) }),

    // -- output / jobs ------------------------------------------------------
    getOutput: (runId) => req(`/api/runs/${encodeURIComponent(runId)}/output`),
    downloadOutputUrl: (runId, filename) => `/api/runs/${encodeURIComponent(runId)}/output/${encodeURIComponent(filename)}`,
    getJobs: (runId) => req(`/api/runs/${encodeURIComponent(runId)}/jobs`),

    // -- settings -------------------------------------------------------------
    getSettings: () => req("/api/settings"),
    updateSettings: (patch) => req("/api/settings", { method: "PATCH", body: json(patch) }),
  };
})();
