// LyreTeXt UI — single-page app wired to lyretext/api.py.
// Architecture: one global `state` object, one `render()` that rebuilds the
// DOM for the current tab from state + the last-fetched view model, and a
// single delegated listener that dispatches on `data-action`. No build step,
// no framework — see development/ux2.md for the behaviour this implements.

const ICONS = {
  newrun: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  chapters: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`,
  workspace: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M12 3 3 8l9 5 9-5-9-5Z"/><path d="m3 13 9 5 9-5"/></svg>`,
  jobs: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>`,
  output: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M12 2.5 3.5 7v10L12 21.5 20.5 17V7Z"/><path d="m3.5 7 8.5 4.6L20.5 7M12 11.6v9.9"/></svg>`,
  settings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 8 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>`,
  check: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`,
  spinner: `<svg class="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.2-8.5"/></svg>`,
  chevronRight: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>`,
  chevronLeft: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m15 18-6-6 6-6"/></svg>`,
  chevronDown: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="m6 9 6 6 6-6"/></svg>`,
  play: `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4.8 19 12 6 19.2Z"/></svg>`,
  retry: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>`,
  search: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>`,
  pin: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 6-9 12-9 12s-9-6-9-12a9 9 0 0 1 18 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>`,
  edit: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`,
  download: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5M12 15V3"/></svg>`,
  folder: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>`,
  wrap: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M3 18h7"/><path d="M3 12h14a3.5 3.5 0 0 1 0 7h-3"/><path d="m16 16-2.5 3L16 22"/></svg>`,
  copy: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
};

const state = {
  tab: "newrun",
  runId: null,
  view: null,
  runsList: [],
  runsListLoaded: false,
  chosenSource: "",
  chosenSourceLabel: "",
  uploadFiles: null,
  settings: null,
  starting: false,

  manifestEditing: false,
  manifestEdit: [],
  manifestSelected: new Set(),
  manifestInitialized: false,

  search: "",
  filter: "all",
  selectedChapters: new Set(),
  remapOpenFor: null,
  remapValue: "",

  chaptersView: "chapters", // "chapters" | "files"
  projectFileOpen: null,    // path of the currently-expanded project file, or null
  projectFileContent: {},   // path -> content (cache; undefined once fetch starts, string once loaded)

  wsChapterId: null,
  wsMode: "translation",
  wsFindingsOpen: true,
  wsFindings: undefined, // undefined = not fetched yet; null = fetched, none recorded
  wsFindingsChapter: null,
  // Collapse findings that describe the same fix at different lines into one
  // row with one Fix button. A view preference like wsWrap, so it survives
  // switching chapter/run and page reloads.
  wsFindingsGrouped: localStorage.getItem("lyretext.groupFindings") !== "0",
  // Sidecar indices the user has queued for fixing. One "Apply" sends the whole
  // set as a single edit_chapter run — a review cycle costs an editing call plus
  // every registered check, so fixing findings one at a time means sitting
  // through that repeatedly and paying for it each time.
  wsFixQueue: new Set(),
  wsLastExecuting: false,
  wsSourceContent: null,
  wsPtxContent: null,
  wsContentChapter: null,
  wsEditTarget: null,
  wsEditContent: "",
  wsEditLine: 1,              // caret position, shown as "Ln x, Col y"
  wsEditCol: 1,
  wsEditMarkers: null,        // {line: severity} finding marks, carried into the editor
  wsEditAnchors: null,        // the same marks anchored to line text, to survive edits
  wsSaving: false,
  wsRefine: "",
  // Soft-wrap in the code panes. A view preference, not run state, so it
  // survives switching chapter/run and page reloads.
  wsWrap: localStorage.getItem("lyretext.wrap") !== "0",
  // Set by "jump to line" (a finding's L-badge); consumed once by the next
  // render, which scrolls the pane and flashes the row.
  wsJumpTo: null,
  // Render pane. wsRenderStatus is explicit rather than inferred from whether
  // wsRender is set: "no request in flight" and "request in flight" are
  // different states, and conflating them is what let the pane sit on
  // "Rendering…" forever whenever a guard blocked the fetch.
  wsRender: null,
  wsRenderKey: null,          // chapter id the current render belongs to
  wsRenderStatus: "idle",     // idle | loading | ready | error | blocked
  wsRenderBlocked: null,      // why, when status is "blocked"
  // Checkpoint history/revert is implemented backend-side but not exposed
  // in this UI yet — see workspaceHeaderRight's "Checkpoints · soon" button.

  jobsOpen: new Set(),

  outputSelected: null,
  outputContent: null,

  settingsDraft: null,

  pollTimer: null,
  viewSig: null,   // hash of the last polled view, to skip no-op re-renders
  busy: false,
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Findings buttons carry the sidecar indices they act on as "3,7,12".
function issueIndices(el) {
  return String(el.dataset.idxs || "")
    .split(",")
    .map((s) => parseInt(s, 10))
    .filter((n) => Number.isFinite(n));
}

// Lightweight regex-based XML highlighter for PreTeXt output — operates
// entirely on already-escaped text, so a pattern that fails to match just
// leaves plain text rather than risking unescaped markup.
function highlightXml(raw) {
  let s = esc(raw);
  s = s.replace(/(&lt;!--[\s\S]*?--&gt;)/g, '<span class="xml-comment">$1</span>');
  s = s.replace(
    /(&lt;\/?)([a-zA-Z_][\w:.-]*)((?:\s+[a-zA-Z_:][\w:.-]*=&quot;[^&]*?&quot;)*)(\s*\/?&gt;)/g,
    (_m, open, name, attrs, close) => {
      const attrsHtml = attrs.replace(
        /([a-zA-Z_:][\w:.-]*)(=)(&quot;[^&]*?&quot;)/g,
        '<span class="xml-attr">$1</span>$2<span class="xml-str">$3</span>'
      );
      return `<span class="xml-tag">${open}${name}</span>${attrsHtml}<span class="xml-tag">${close}</span>`;
    }
  );
  return s;
}

// ---------------------------------------------------------------------------
// Code views (line-numbered source / PreTeXt panes)
// ---------------------------------------------------------------------------

// Split already-highlighted HTML into one entry per source line, re-balancing
// spans that straddle a newline. highlightXml() matches across lines (a comment,
// or a tag whose attributes are wrapped), so naively splitting its output would
// leave a <span> unclosed on one line and an orphan </span> on the next — which
// the browser silently "fixes" by bleeding the colour over the rest of the file.
// Every line is therefore closed at its end and reopened at the next line's
// start, leaving each entry independently well-formed.
function splitHighlightedLines(html) {
  const tagRe = /<span class="[^"]*">|<\/span>/g;
  const open = [];
  return html.split("\n").map((line) => {
    const prefix = open.join("");
    let m;
    tagRe.lastIndex = 0;
    while ((m = tagRe.exec(line))) {
      if (m[0] === "</span>") open.pop();
      else open.push(m[0]);
    }
    return prefix + line + "</span>".repeat(open.length);
  });
}

// Width of the line-number gutter, as a CSS custom property. Sized from the
// digit count so a 12-line file doesn't reserve room for four digits.
function gutterVar(lineCount) {
  return `--ln-w:${String(Math.max(lineCount, 1)).length}ch`;
}

// Highlighting + splitting is the one genuinely costly step here, and the poll
// re-renders unchanged files every 2s, so the result is memoised on the text
// itself. Two entries (the source and the PreTeXt pane) is all that's ever
// live at once; the cap only stops the map growing while paging through
// chapters. Cheap per-render work (markers, ids) stays outside the cache.
const _lineCache = new Map();
function highlightedLinesCached(text, xml) {
  const key = (xml ? "x\n" : "p\n") + text;
  let lines = _lineCache.get(key);
  if (!lines) {
    lines = splitHighlightedLines(xml ? highlightXml(text) : esc(text));
    if (_lineCache.size > 8) _lineCache.clear();
    _lineCache.set(key, lines);
  }
  return lines;
}

// A read-only, line-numbered code block.
//   opts.xml         — run the PreTeXt/XML highlighter over the text
//   opts.wrap        — soft-wrap long lines (otherwise the pane scrolls sideways)
//   opts.markers     — {lineNumber: severity} gutter marks, e.g. from findings
//   opts.id          — DOM id, needed for scroll-to-line
function codeView(text, opts = {}) {
  const { xml = false, wrap = true, markers = null, id = null } = opts;

  // data-keep lets rerender() move this subtree across untouched when nothing
  // about it changed — see harvestKeepNodes(). Everything that affects the
  // markup goes into the signature, and it is computed before any work is
  // done so an unchanged pane skips highlighting and row building entirely.
  //
  // Keyed on the caller's id, so two views can never share a signature: a node
  // can only be moved to one place, and a duplicate would leave whichever
  // placeholder lost the race permanently empty. A view with no id opts out.
  const sig = id
    ? `code:${id}:${wrap}:${xml}:${hashString(text)}:${markers ? hashString(JSON.stringify(markers)) : "-"}`
    : null;
  if (sig && canKeep(sig)) return keepPlaceholder(sig);

  const lines = highlightedLinesCached(text, xml);
  const rows = lines.map((html, i) => {
    const n = i + 1;
    const sev = markers && markers[n];
    return `<div class="code-line${sev ? ` flagged flagged-${esc(sev)}` : ""}" data-line="${n}"${id ? ` id="${id}-L${n}"` : ""}><span class="code-ln">${n}</span><span class="code-tx">${html}</span></div>`;
  });
  return `<div class="code-view ${sig ? KEEP_CLASS : ""} ${wrap ? "wrap" : "nowrap"}"${id ? ` id="${id}"` : ""}${sig ? ` data-keep="${esc(sig)}"` : ""} style="${gutterVar(lines.length)}">${rows.join("")}</div>`;
}

function lineCount(text) {
  return text ? text.split("\n").length : 0;
}

// Compact "1,204 lines" summary for a pane header. Lines rather than a byte
// size: the size of the file on disk is already listed on the Output tab, and
// text.length would only ever be a character count pretending to be bytes.
function codeStats(text) {
  if (text == null) return "";
  const n = lineCount(text);
  return `${n.toLocaleString()} line${n === 1 ? "" : "s"}`;
}

// Gutter markup for the editor. The editor cannot use codeView()'s per-line
// grid (a textarea is one element), so the gutter is a parallel column kept in
// step by a scroll listener; soft-wrap is off there precisely so one logical
// line always occupies exactly one visual row and the two columns stay aligned.
function editorGutter(text, activeLine = 1, markers = null) {
  const n = lineCount(text);
  const rows = [];
  for (let i = 1; i <= Math.max(n, 1); i++) {
    const sev = markers && markers[i];
    rows.push(`<div class="code-eln${i === activeLine ? " active" : ""}${sev ? ` flagged flagged-${esc(sev)}` : ""}">${i}</div>`);
  }
  return rows.join("");
}

// Finding markers follow the text into the editor — the whole point of opening
// it is usually to fix the flagged lines, and losing the marks at that moment
// meant cross-referencing the list above by eye.
//
// They cannot stay pinned to line numbers, though: inserting a line above one
// shifts it. Each marker therefore remembers the *text* of the line it was
// reported against, and is re-anchored to the nearest identical line whenever
// the line count changes. A marker whose line has since been rewritten finds
// no match and is dropped, which is the honest outcome — better a mark that
// disappears than one confidently pointing at the wrong line.
const SEV_RANK = { error: 3, warn: 2, info: 1 };

function editMarkerAnchors(text, markers) {
  const lines = text.split("\n");
  return Object.entries(markers || {})
    .map(([line, severity]) => ({ severity, line: Number(line), text: lines[Number(line) - 1] }))
    // A blank line matches everywhere, so it can never be re-anchored safely.
    .filter((a) => a.text && a.text.trim());
}

function reanchorEditMarkers(anchors, text) {
  const lines = text.split("\n");
  const out = {};
  for (const a of anchors) {
    // Search outward from where the line used to be, so an unremarkable line
    // that repeats (a lone "</p>") anchors to the nearest occurrence.
    let found = 0;
    for (let d = 0; d < lines.length && !found; d++) {
      if (lines[a.line - 1 + d] === a.text) found = a.line + d;
      else if (d && lines[a.line - 1 - d] === a.text) found = a.line - d;
    }
    if (found && (!out[found] || SEV_RANK[a.severity] > SEV_RANK[out[found]])) out[found] = a.severity;
  }
  return out;
}

// Scroll a pane to the line requested by state.wsJumpTo and flash the row.
// Called from rerender() rather than from the click handler because the row
// only exists after renderApp() has rebuilt the DOM.
//
// Returns true if it moved the editor's caret, which rerender() uses to skip
// its restore-the-caret-where-it-was step — that runs afterwards and would
// otherwise put the caret straight back where the jump moved it from.
function applyPendingJump() {
  const jump = state.wsJumpTo;
  if (!jump) return false;
  state.wsJumpTo = null;

  // With the editor open the pane has no per-line rows — put the caret on the
  // line instead, which is what you want anyway if you're already editing.
  const area = state.wsEditTarget === jump.target && document.getElementById("wsEditArea");
  if (area) {
    const lines = area.value.split("\n");
    const line = Math.min(Math.max(jump.line, 1), lines.length);
    const offset = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0);
    area.focus();
    area.setSelectionRange(offset, offset + lines[line - 1].length);
    // Soft-wrap is off in the editor, so one line is one row of line-height.
    const lh = parseFloat(getComputedStyle(area).lineHeight) || 21;
    area.scrollTop = Math.max(0, (line - 1) * lh - area.clientHeight / 3);
    syncEditorGutter();
    return true;
  }

  const row = document.getElementById(`wsPane-${jump.target}-L${jump.line}`);
  if (!row) return false;
  const body = row.closest(".pane-body");
  if (body) {
    // Land the line a third of the way down rather than flush against the
    // top edge, so the surrounding markup is visible as context. Measured
    // from the rects, since .pane-body is not the row's offsetParent.
    const offset = row.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
    body.scrollTop = Math.max(0, offset - body.clientHeight / 3);
  }
  row.classList.add("jump-flash");
  setTimeout(() => row.classList.remove("jump-flash"), 1400);
  return false;
}

// Keep the editor's gutter in step with the textarea after a keystroke:
// the line count may have changed, and the active line almost certainly has.
// Done by direct DOM patch, not rerender() — typing must not rebuild the page.
function syncEditorGutter() {
  const area = document.getElementById("wsEditArea");
  const gutter = document.getElementById("wsEditGutter");
  if (!area || !gutter) return;
  const before = area.value.slice(0, area.selectionStart);
  const lines = before.split("\n");
  state.wsEditLine = lines.length;
  state.wsEditCol = lines[lines.length - 1].length + 1;

  const wanted = Math.max(lineCount(area.value), 1);
  if (gutter.childElementCount !== wanted) {
    // Lines were added or removed, so the marks may no longer sit on the lines
    // they were reported against — find them again by content. Only done here,
    // not on every keystroke: editing *within* a flagged line should leave its
    // mark in place (that line is precisely what's being fixed), and re-running
    // the search on each character would make marks flicker while typing.
    if (state.wsEditAnchors) state.wsEditMarkers = reanchorEditMarkers(state.wsEditAnchors, area.value);
    gutter.innerHTML = editorGutter(area.value, state.wsEditLine, state.wsEditMarkers);
    const editor = gutter.closest(".code-editor");
    if (editor) editor.style.setProperty("--ln-w", `${String(wanted).length}ch`);
  } else {
    const active = gutter.querySelector(".code-eln.active");
    if (active) active.classList.remove("active");
    const now = gutter.children[state.wsEditLine - 1];
    if (now) now.classList.add("active");
  }
  gutter.scrollTop = area.scrollTop;

  const pos = document.getElementById("wsEditPos");
  if (pos) pos.textContent = `Ln ${state.wsEditLine}, Col ${state.wsEditCol}`;
}

// Cheap non-cryptographic string hash (FNV-1a). Used only to tell "same
// content as last render" from "different", so collisions cost a redraw.
function hashString(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h.toString(36) + ":" + s.length;
}

// renderApp() replaces #app-root wholesale, which is fine for the chrome but
// ruinous for the two heavy subtrees: a 2,500-line chapter is ~13,000 DOM
// elements, and rebuilding all of it every poll tick was costing most of a
// second — plus it threw away MathJax's typeset output, forcing a full
// re-typeset of the chapter each time.
//
// Any element carrying data-keep="<signature>" is instead moved across from
// the previous DOM when the signature is unchanged. Moving a node is O(1)
// however large it is, so an unchanged pane costs nothing to "re-render".
//
// The template has to cooperate: when canKeep() says the subtree is already in
// the DOM it emits keepPlaceholder() — an empty div — instead of the real
// markup. Emitting the markup and swapping it out afterwards would still pay
// innerHTML's parse of ~700 KB, which is the cost this exists to avoid.
//
// The registry is deliberately valid for exactly one rerender: harvest fills
// it from the live DOM, restore empties it. A renderApp() from anywhere else
// therefore always sees it empty and emits complete markup, so there is no
// path that can emit a placeholder with no node to put in it.
let _keepRegistry = new Map();

// Found by class, not by [data-keep]: an attribute selector has to test every
// element in the document, and with the panes in place that is >13,000 of them
// — enough to cost more than the rebuild this is meant to avoid. Class lookups
// are indexed by the DOM implementation, so they stay flat.
const KEEP_CLASS = "keep-node";

function keepNodes() {
  // Snapshotted: getElementsByClassName is live, and restore mutates the DOM
  // while iterating.
  return Array.from(document.getElementsByClassName(KEEP_CLASS));
}

function harvestKeepNodes() {
  _keepRegistry = new Map();
  for (const el of keepNodes()) _keepRegistry.set(el.dataset.keep, el);
}

function canKeep(sig) {
  return _keepRegistry.has(sig);
}

function keepPlaceholder(sig) {
  return `<div class="${KEEP_CLASS}" data-keep="${esc(sig)}"></div>`;
}

function restoreKeepNodes() {
  if (!_keepRegistry.size) return;
  for (const placeholder of keepNodes()) {
    const previous = _keepRegistry.get(placeholder.dataset.keep);
    if (previous && previous !== placeholder) placeholder.replaceWith(previous);
  }
  _keepRegistry = new Map();
}

function toast(message, type = "info") {
  let root = document.getElementById("toast-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "toast-root";
    root.className = "toast-root";
    document.body.appendChild(root);
  }
  const el = document.createElement("div");
  el.className = `toast ${type === "error" ? "error" : type === "success" ? "success" : ""}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function fmtSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

async function safeAction(fn) {
  try {
    state.busy = true;
    await fn();
  } catch (err) {
    toast(err.message || String(err), "error");
  } finally {
    state.busy = false;
    rerender();
  }
}

// Re-render while preserving focus + text selection on the active input,
// so typing in the search box / textareas survives a full DOM rebuild.
function rerender() {
  const active = document.activeElement;
  const activeId = active && active.id;
  const selStart = active && "selectionStart" in active ? active.selectionStart : null;
  const selEnd = active && "selectionEnd" in active ? active.selectionEnd : null;
  // renderApp() replaces #app-root's entire innerHTML, which resets scroll
  // to 0 on every re-render — without this, the page snapped back to the
  // top every 2s while polling, mid-scroll. Compare panes (source/PreTeXt)
  // are their own scroll containers (.pane-body), so they need the same
  // treatment, keyed by position since they're rebuilt in the same order.
  const mainBefore = document.querySelector(".main");
  const scrollTop = mainBefore ? mainBefore.scrollTop : 0;
  // scrollLeft matters too now: with wrapping off, a code pane scrolls
  // sideways, and losing that every 2s is as disruptive as losing the top.
  const paneScroll = Array.from(document.querySelectorAll(".pane-body")).map((el) => [el.scrollTop, el.scrollLeft]);
  // The editor scrolls itself, not its .pane-body, so it needs its own entry —
  // otherwise a 2s poll landing mid-edit snapped the textarea back to line 1.
  const editorBefore = document.getElementById("wsEditArea");
  const editorScroll = editorBefore ? [editorBefore.scrollTop, editorBefore.scrollLeft] : null;
  harvestKeepNodes();
  renderApp();
  // Before anything measures or scrolls, so the reused subtrees are in place.
  restoreKeepNodes();
  const mainAfter = document.querySelector(".main");
  if (mainAfter) mainAfter.scrollTop = scrollTop;
  document.querySelectorAll(".pane-body").forEach((el, i) => {
    if (!paneScroll[i]) return;
    el.scrollTop = paneScroll[i][0];
    el.scrollLeft = paneScroll[i][1];
  });
  const editorAfter = document.getElementById("wsEditArea");
  if (editorAfter && editorScroll) {
    editorAfter.scrollTop = editorScroll[0];
    editorAfter.scrollLeft = editorScroll[1];
    const gutter = document.getElementById("wsEditGutter");
    if (gutter) gutter.scrollTop = editorScroll[0];
  }
  const jumped = applyPendingJump();   // after the restore above, so a jump wins over it
  // renderApp() rebuilds the DOM wholesale, so any math MathJax already
  // typeset is gone with it — re-typeset whatever render pane now exists.
  typesetRender();
  if (activeId && !jumped) {
    const el = document.getElementById(activeId);
    if (el) {
      el.focus();
      if (selStart != null && "setSelectionRange" in el) {
        try { el.setSelectionRange(selStart, selEnd); } catch {}
      }
    }
  }
}

// Keep the URL in sync with the loaded run (?run=<id>) so a run can be
// bookmarked, shared, or reloaded directly instead of only being reachable
// by clicking through "Resume a previous run" in the same session.
function syncUrlToRun() {
  const url = new URL(location.href);
  if (state.runId) url.searchParams.set("run", state.runId);
  else url.searchParams.delete("run");
  history.replaceState(null, "", url.pathname + url.search);
}

function resetRunScopedState() {
  state.manifestSelected = new Set();
  state.manifestInitialized = false;
  state.manifestEditing = false;
  state.selectedChapters = new Set();
  state.search = ""; state.filter = "all";
  state.wsChapterId = null;
  resetWsPaneState();
  state.jobsOpen = new Set();
  state.outputSelected = null; state.outputContent = null;
}

// A chapter that's mid-run still carries the lifecycle from its last settled
// state, so counting it as approved makes the progress tally jump forward and
// then back when the run finishes. Only count chapters that have stopped.
function isApproved(c) {
  return c.lifecycle === "approved" && !c._executing;
}

function chapterById(id) {
  return (state.view?.chapters || []).find((c) => c.id === id) || null;
}

function currentChapterIndex() {
  const list = state.view?.chapters || [];
  return list.findIndex((c) => c.id === state.wsChapterId);
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function refreshRun({ silent } = {}) {
  if (!state.runId) return;
  try {
    const view = await LyreAPI.getRun(state.runId);
    // Signature of the payload the DOM is built from, so the poll can tell an
    // uneventful tick from a real change. Most ticks return byte-identical
    // data — re-rendering those was rebuilding the whole page for nothing.
    state.viewSig = hashString(JSON.stringify(view));
    state.view = view;
    if (!silent) rerender();
    // silent: caller decides whether/how to render — avoids rendering twice
    // per poll tick (each render resets any scroll not explicitly restored).
  } catch (err) {
    if (!silent) toast(`Could not load run: ${err.message}`, "error");
  }
}

// Poll cadence. 2s while work is in flight, where the UI is reporting progress
// and latency is visible; slower when everything has settled, since then the
// only thing a tick can discover is a change made from outside this browser.
const POLL_ACTIVE_MS = 2000;
const POLL_IDLE_MS = 10000;

function pollInterval() {
  const view = state.view;
  if (!view) return POLL_ACTIVE_MS;
  const busy = view._executing || (view.chapters || []).some((c) => c._executing);
  return busy ? POLL_ACTIVE_MS : POLL_IDLE_MS;
}

// A self-scheduling loop rather than setInterval: the callback awaits a
// request, and setInterval would happily start a second one before the first
// returned — which on a slow tick meant overlapping fetches and two renders
// racing each other.
//
// _pollGen makes stopPolling() authoritative even mid-tick. Clearing the
// timeout alone is not enough: a tick that is already awaiting its fetch when
// the run is closed would carry on and schedule the next one, quietly bringing
// the loop back from the dead.
let _pollGen = 0;

function startPolling() {
  stopPolling();
  const gen = ++_pollGen;
  const tick = async () => {
    state.pollTimer = null;
    if (gen !== _pollGen || !state.runId) return;
    const active = document.activeElement;
    const skip = state.wsEditTarget   // don't clobber an in-progress manual edit
      || (active && (active.tagName === "TEXTAREA" || (active.tagName === "INPUT" && active.type === "text")));
    if (!skip) {
      const before = state.viewSig;
      await refreshRun({ silent: true });
      if (gen !== _pollGen) return;
      // Nothing came back that the DOM depends on, so leave it alone entirely.
      if (state.viewSig !== before && document.getElementById("app-root")) rerender();
    }
    if (gen === _pollGen && state.runId) state.pollTimer = setTimeout(tick, pollInterval());
  };
  state.pollTimer = setTimeout(tick, POLL_ACTIVE_MS);
}

function stopPolling() {
  _pollGen++;
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
}

async function loadRunsList() {
  state.runsListLoaded = true;
  try {
    const { runs } = await LyreAPI.listRuns();
    const enriched = [];
    for (const r of runs || []) {
      try {
        const view = await LyreAPI.getRun(r.run_id);
        const total = (view.chapters || []).length;
        const done = (view.chapters || []).filter(isApproved).length;
        enriched.push({
          run_id: r.run_id,
          name: view.project?.name || r.run_id,
          gatePending: !!view.gate?.read_pending,
          done, total,
        });
      } catch { /* stale/incompatible checkpoint — skip it from the resume list */ }
    }
    state.runsList = enriched;
  } catch {
    state.runsList = [];
  }
  rerender();
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

function renderApp() {
  let root = document.getElementById("app-root");
  if (!root) return;
  const executingAny = !!(state.view && (state.view._executing || (state.view.chapters || []).some((c) => c._executing)));
  root.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <img src="assets/logomark.svg" alt="LyreTeXt" />
          <div>
            <div class="brand-name">LyreTeXt</div>
            <div class="brand-sub">PreTeXt Translation Studio</div>
          </div>
        </div>
        <div class="nav-label">Pipeline</div>
        <nav class="nav">
          ${navItem("newrun", ICONS.newrun, "New Run")}
          ${navItem("chapters", ICONS.chapters, "Chapters")}
          ${navItem("workspace", ICONS.workspace, "Workspace")}
          ${navItem("jobs", ICONS.jobs, "Jobs", executingAny)}
          ${navItem("output", ICONS.output, "Output")}
        </nav>
        <div class="nav-label nav-label-project">Project</div>
        <nav class="nav">
          ${navItem("settings", ICONS.settings, "Settings")}
        </nav>
        <div class="nav-spacer"></div>
        ${sideProgress()}
        ${sideAccount()}
        <div class="side-brand-footer"><img src="assets/idems.svg" alt="IDEMS" /></div>
      </aside>
      <main class="main">${renderTab()}</main>
    </div>
  `;
}

function navItem(tab, icon, label, badge) {
  const active = state.tab === tab;
  return `<div class="nav-item ${active ? "active" : ""}" data-action="nav" data-tab="${tab}">
    ${icon}<span style="flex:1">${label}</span>${badge ? `<span class="nav-badge"></span>` : ""}
  </div>`;
}

function sideProgress() {
  if (!state.view) return "";
  const chapters = state.view.chapters || [];
  const total = chapters.length;
  const done = chapters.filter(isApproved).length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  return `
    <div class="side-card">
      <div class="side-progress-row">
        <span class="side-progress-label">Chapters approved</span>
        <span class="side-progress-val">${done}/${total}</span>
      </div>
      <div class="side-progress-track"><div class="side-progress-fill" style="width:${pct}%"></div></div>
    </div>`;
}

// A generic, static account placeholder — this prototype has no auth, so it
// shows a neutral identity rather than a real person. Wire it to the signed-in
// user when the app grows one.
function sideAccount() {
  return `
    <div class="side-account">
      <div class="side-avatar">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      </div>
      <div class="side-account-meta">
        <div class="side-account-name">Local user</div>
        <div class="side-account-sub">This workspace</div>
      </div>
    </div>`;
}

function renderTab() {
  switch (state.tab) {
    case "newrun": return renderNewRun();
    case "manifest": return renderManifest();
    case "chapters": return renderChapters();
    case "workspace": return renderWorkspace();
    case "jobs": return renderJobs();
    case "output": return renderOutput();
    case "settings": return renderSettings();
    default: return renderNewRun();
  }
}

function topbar(title, sub, right) {
  return `<header class="topbar"><div><h1 class="page-title">${esc(title)}</h1><p class="page-sub">${sub || ""}</p></div>${right || ""}</header>`;
}

// ---------------------------------------------------------------------------
// New Run
// ---------------------------------------------------------------------------

function pipelineLabel(pipeline) {
  if (pipeline === "qmd") return "Quarto (.qmd)";
  if (pipeline === "tex") return "LaTeX (.tex)";
  return "R Markdown (.Rmd)";
}

function renderNewRun() {
  const g = state.settings?.global_options || {};
  const rows = [
    ["Model provider", g.provider === "anthropic" ? "Anthropic · Claude" : "Gemini (Google)"],
    ["Model name", g.llm_model || "default · env"],
    ["Pipeline", pipelineLabel(g.pipeline)],
    ["Apply mode", g.apply_mode === "dry_run" ? "Dry run (no writes)" : "Auto-apply"],
    ["Backups on edit", g.create_backup ? "On" : "Off"],
  ];
  const sourceReady = !!(state.chosenSource || (state.uploadFiles && state.uploadFiles.length));

  return `
    ${topbar("New Run", "Point LyreTeXt at a bookdown, Quarto, or LaTeX project to begin.")}
    <div class="content" style="display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:24px;align-items:start;max-width:1200px">
      <div style="display:flex;flex-direction:column;gap:22px">
        <div class="upload-drop" id="dropZone">
          ${ICONS.folder}
          ${sourceReady
            ? `<div class="mono" style="font-size:13.5px;font-weight:600;margin-top:12px;word-break:break-all;color:var(--text-soft)">${esc(state.chosenSourceLabel)}</div>`
            : `<div style="font-family:var(--font-heading);font-weight:600;font-size:21px;margin-top:12px">Choose a project source</div>`}
          <div style="font-size:13.5px;color:var(--text-faint);margin-top:8px">.Rmd / .qmd + _bookdown.yml, or .tex, from a local path or an upload</div>
          <div style="display:flex;justify-content:center;flex-wrap:wrap;gap:10px;margin-top:20px">
            <button class="btn" data-action="browseFolder">Browse local folder…</button>
            <button class="btn" data-action="triggerUpload">Upload folder…</button>
            <button class="btn btn-ghost" data-action="useDemo">Use demo project</button>
          </div>
          <input type="file" id="uploadInput" webkitdirectory multiple class="hidden" data-action="onFolderSelected" />
        </div>

        <div class="card">
          <div class="row" style="justify-content:space-between;margin-bottom:6px">
            <h2 style="margin:0;font-family:var(--font-heading);font-weight:600;font-size:19px">Configuration</h2>
            <button class="btn btn-ghost btn-sm" data-action="nav" data-tab="settings">Edit in Settings ${ICONS.chevronRight}</button>
          </div>
          <p style="margin:0 0 12px;font-size:13px;color:var(--text-faint)">This run will use your saved project defaults.</p>
          ${rows.map(([label, value]) => `
            <div class="row" style="justify-content:space-between;padding:10px 0;border-top:1px solid var(--divider)">
              <span style="font-size:13px;color:var(--text-muted)">${esc(label)}</span>
              <span style="font-size:13.5px;font-weight:600">${esc(value)}</span>
            </div>`).join("")}
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="card">
          <h3 style="margin:0 0 10px;font-family:var(--font-heading);font-weight:600;font-size:18px">Ready to start</h3>
          <p style="margin:0 0 16px;font-size:13.5px;line-height:1.55;color:var(--text-muted)">
            Once started, LyreTeXt reads the project, detects chapters and front/back matter,
            then stops at the <b style="color:var(--text-soft)">manifest gate</b> for your review before any chapter work begins.
          </p>
          <button class="btn btn-primary btn-block" data-action="startRun" ${!sourceReady || state.starting ? "disabled" : ""}>
            ${state.starting ? ICONS.spinner + " Starting…" : ICONS.play + " Start run"}
          </button>
        </div>
        <div class="card">
          <h3 style="margin:0 0 14px;font-family:var(--font-heading);font-weight:600;font-size:18px">Resume a previous run</h3>
          ${renderResumeList()}
        </div>
      </div>
    </div>
  `;
}

function renderResumeList() {
  if (!state.runsListLoaded) {
    setTimeout(loadRunsList, 0);
    return `<div class="empty-note">${ICONS.spinner} Loading previous runs…</div>`;
  }
  if (!state.runsList.length) return `<div class="empty-note">No previous runs yet.</div>`;
  return state.runsList.map((r) => `
    <div class="row" style="gap:12px;padding:11px 0;border-top:1px solid var(--divider);cursor:pointer" data-action="resumeRun" data-run="${esc(r.run_id)}">
      <span class="pill-dot" style="background:${r.gatePending ? "var(--warn-dot)" : "var(--ok-dot)"}"></span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13.5px;font-weight:600">${esc(r.name)}</div>
        <div style="font-size:11.5px;color:var(--text-faint)" class="mono">${esc(r.run_id)}</div>
      </div>
      <span class="pill ${r.gatePending ? "pill-warn" : "pill-neutral"}">${r.gatePending ? "Awaiting manifest" : `${r.done}/${r.total} approved`}</span>
    </div>`).join("");
}

// ---------------------------------------------------------------------------
// Manifest gate
// ---------------------------------------------------------------------------

function renderManifest() {
  const view = state.view;
  if (!view) return topbar("Manifest gate", "No run loaded.") + `<div class="content"><button class="btn" data-action="nav" data-tab="newrun">Start a run</button></div>`;
  const chapters = view.chapters || [];
  const warnings = view.gate?.warnings || [];

  if (!state.manifestInitialized) {
    chapters.forEach((c) => state.manifestSelected.add(c.id));
    state.manifestInitialized = true;
  }

  const rows = state.manifestEditing ? state.manifestEdit : chapters.map((c) => ({
    id: c.id, type: c.type, name: c.title, source_path: c.file, output_path: c.output_file,
  }));
  const selectedCount = state.manifestSelected.size;
  const allSelected = selectedCount === chapters.length;
  const primaryLabel = selectedCount === 0
    ? "Approve & continue"
    : allSelected ? "Approve & continue" : `Approve ${selectedCount} selected chapter${selectedCount === 1 ? "" : "s"}`;

  return `
    ${topbar("Manifest gate", "A mandatory checkpoint. Review the detected structure before any chapter work starts.",
      `<span class="pill pill-warn"><span class="pill-dot"></span>Awaiting human · paused</span>`)}
    <div class="content" style="max-width:1120px">
      <div class="row gap-3" style="flex-wrap:wrap;margin-bottom:20px">
        <div class="card card-sm" style="min-width:180px"><div style="font-size:11px;letter-spacing:.06em;font-weight:600;color:var(--text-ghost);text-transform:uppercase">Detected type</div><div style="font-size:15px;font-weight:600;margin-top:4px">${esc(view.project.source_type || "unknown")}</div></div>
        <div class="card card-sm" style="min-width:160px"><div style="font-size:11px;letter-spacing:.06em;font-weight:600;color:var(--text-ghost);text-transform:uppercase">Manifest entries</div><div style="font-size:15px;font-weight:600;margin-top:4px">${chapters.length} entries</div></div>
        <div class="card card-sm" style="min-width:160px"><div style="font-size:11px;letter-spacing:.06em;font-weight:600;color:var(--text-ghost);text-transform:uppercase">Warnings</div><div style="font-size:15px;font-weight:600;margin-top:4px;color:${warnings.length ? "var(--warn-text)" : "inherit"}">${warnings.length} warning${warnings.length === 1 ? "" : "s"}</div></div>
      </div>

      ${warnings.length ? `<div class="gate-banner" style="margin-bottom:20px">${warnings.map((w) => `<div style="font-size:13.5px;color:var(--warn-text)">${esc(typeof w === "string" ? w : (w.message || JSON.stringify(w)))}</div>`).join("")}</div>` : ""}

      <p style="font-size:13px;color:var(--text-muted);margin:0 0 10px;line-height:1.5">
        Each row is one file LyreTeXt found and how it will be classified. <b>Type</b> controls where it lands in the
        output (frontmatter/backmatter/appendix sort differently from chapters). <b>Source path</b> is the original
        file; <b>proposed output</b> is where its translated <span class="mono">.ptx</span> will be written. Uncheck a
        row to leave it out of this run.
      </p>

      <div class="table-wrap" style="margin-bottom:22px">
        <div class="table-head" style="grid-template-columns:26px 1.1fr 2fr 2fr">
          <span></span><span>Type</span><span>Source path</span><span>Proposed output</span>
        </div>
        ${rows.length
          ? rows.map((row, i) => manifestRow(row, i)).join("")
          : `<div class="empty-note" style="padding:18px 16px">No files were detected in this project. Check the folder you selected, and that <b>Pipeline</b> in Settings matches the format your source is authored in (.Rmd, .qmd, or .tex).</div>`}
      </div>

      ${state.manifestEditing ? `
        <p style="font-size:12.5px;color:var(--text-faint);margin:0 0 12px">
          Editing types, source paths, and output paths in place. <b>Save &amp; approve</b> applies these edits and
          immediately approves the manifest — every remaining chapter moves to the Chapters page.
        </p>
        <div class="row gap-2" style="margin-bottom:14px">
          <button class="btn btn-primary" data-action="saveManifestEdit">${ICONS.check} Save &amp; approve</button>
          <button class="btn" data-action="cancelManifestEdit">Cancel</button>
        </div>
      ` : `
        <p style="font-size:12.5px;color:var(--text-faint);margin:0 0 12px">
          Skip works the other way round from the checkboxes above: it removes the <i>checked</i> rows from this run
          (they stay visible on the Chapters page, marked "skipped") and approves the rest.
        </p>
        <div class="row gap-2" style="flex-wrap:wrap">
          <button class="btn btn-primary" data-action="gateSelect" ${selectedCount === 0 ? "disabled" : ""}>${ICONS.check} ${primaryLabel}</button>
          <button class="btn" data-action="startManifestEdit">Edit manifest</button>
          <button class="btn" data-action="gateSkip" ${selectedCount === 0 ? "disabled" : ""}>Skip checked, approve the rest</button>
          <div style="flex:1"></div>
          <button class="btn btn-danger" data-action="gateAbort">Abort run</button>
        </div>
      `}
    </div>
  `;
}

const MANIFEST_TYPES = ["chapter", "frontmatter", "backmatter", "appendix", "asset", "script"];

function manifestRow(row, i) {
  if (state.manifestEditing) {
    return `<div class="table-row" style="grid-template-columns:26px 1.1fr 2fr 2fr">
      <span></span>
      <select id="manifestEdit-${i}-type" class="select select-type-${row.type}" data-action="editManifestField" data-idx="${i}" data-field="type">
        ${MANIFEST_TYPES.map((t) => `<option value="${t}" ${row.type === t ? "selected" : ""}>${t}</option>`).join("")}
      </select>
      <input id="manifestEdit-${i}-source_path" class="input mono" data-action="editManifestField" data-idx="${i}" data-field="source_path" value="${esc(row.source_path)}" />
      <input id="manifestEdit-${i}-output_path" class="input mono" data-action="editManifestField" data-idx="${i}" data-field="output_path" value="${esc(row.output_path)}" />
    </div>`;
  }
  const checked = state.manifestSelected.has(row.id);
  return `<div class="table-row" style="grid-template-columns:26px 1.1fr 2fr 2fr">
    <input type="checkbox" class="checkbox" data-action="toggleManifestSelect" data-id="${esc(row.id)}" ${checked ? "checked" : ""} />
    <span class="tag tag-${row.type}">${esc(row.type)}</span>
    <span class="mono" style="font-size:12px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.source_path)}</span>
    <span class="mono" style="font-size:12px;color:var(--text-muted)">${esc(row.output_path)}</span>
  </div>`;
}

// ---------------------------------------------------------------------------
// Chapters
// ---------------------------------------------------------------------------

function renderChapters() {
  const view = state.view;
  if (!view) return topbar("Chapters", "No run loaded.") + `<div class="content"><button class="btn" data-action="nav" data-tab="newrun">Start a run</button></div>`;
  const all = view.chapters || [];
  const q = state.search.trim().toLowerCase();
  const matches = (c) => {
    if (state.filter === "review") return c.lifecycle === "review_required";
    if (state.filter === "running") return !!c._executing;
    if (state.filter === "approved") return isApproved(c);
    return true;
  };
  const visible = all.filter((c) => (!q || c.title.toLowerCase().includes(q)) && matches(c));
  const counts = {
    all: all.length,
    review: all.filter((c) => c.lifecycle === "review_required").length,
    running: all.filter((c) => c._executing).length,
    approved: all.filter(isApproved).length,
  };
  const eligibleToRun = all.filter((c) => c.lifecycle === "translation_ready" && !c._executing).map((c) => c.id);
  const selectedEligible = [...state.selectedChapters].filter((id) => eligibleToRun.includes(id));

  const resources = view.resources || [];

  return `
    ${topbar("Chapters", esc(view.project.name), `<span class="pill pill-neutral mono">rmd&nbsp;→&nbsp;ptx&nbsp;→&nbsp;render</span>`)}
    <div class="content">
      <div class="row" style="background:var(--surface);border:1px solid var(--border-strong);border-radius:9px;padding:2px;width:fit-content;margin-bottom:18px">
        <button class="btn btn-sm ${state.chaptersView === "chapters" ? "btn-primary" : ""}" style="border:none" data-action="setChaptersView" data-view="chapters">Chapters (${all.length})</button>
        <button class="btn btn-sm ${state.chaptersView === "files" ? "btn-primary" : ""}" style="border:none" data-action="setChaptersView" data-view="files">Project files (${resources.length})</button>
      </div>

      ${state.chaptersView === "files" ? renderProjectFiles(resources) : `
      <div class="row gap-3" style="flex-wrap:wrap;margin-bottom:16px">
        <div style="position:relative;flex:1;min-width:220px;max-width:420px">
          <span style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--text-ghost)">${ICONS.search}</span>
          <input id="chapterSearch" class="input" style="padding-left:38px" placeholder="Search chapters…" value="${esc(state.search)}" data-action="onSearch" />
        </div>
        <div style="flex:1"></div>
        <div class="row gap-2">
          ${["all", "review", "running", "approved"].map((f) => `
            <button class="btn btn-sm ${state.filter === f ? "btn-primary" : ""}" data-action="setFilter" data-filter="${f}">
              ${{ all: "All", review: "Needs review", running: "Running", approved: "Approved" }[f]} (${counts[f]})
            </button>`).join("")}
        </div>
      </div>

      <div class="row gap-3" style="margin-bottom:18px;flex-wrap:wrap">
        <button class="btn" data-action="runAll" ${!eligibleToRun.length ? "disabled" : ""}>${ICONS.play} Run all</button>
        ${selectedEligible.length ? `
          <button class="btn btn-primary" data-action="runSelected">Run selected (${selectedEligible.length})</button>
          <button class="btn btn-ghost" data-action="clearSelection">Clear</button>
        ` : ""}
        <div style="flex:1"></div>
        <span style="font-size:12.5px;color:var(--text-faint)">Chapters run concurrently &amp; independently.</span>
      </div>

      ${visible.length ? `<div class="chapter-grid">${visible.map(chapterCard).join("")}</div>` : `<div class="empty-note">No chapters match this filter.</div>`}
      `}
    </div>
  `;
}

function renderProjectFiles(resources) {
  if (!resources.length) {
    return `<div class="empty-note">No supporting project files were detected outside the chapters (e.g. _common.R, manifest config, bibliography).</div>`;
  }
  return `<div style="display:flex;flex-direction:column;gap:10px;max-width:820px">
    ${resources.map((r) => {
      const open = state.projectFileOpen === r.path;
      const content = state.projectFileContent[r.path];
      return `
      <div class="card card-sm">
        <div class="row gap-3" style="cursor:pointer" data-action="toggleProjectFile" data-path="${esc(r.path)}">
          <span style="transform:rotate(${open ? 90 : 0}deg);transition:transform .15s;display:inline-flex">${ICONS.chevronRight}</span>
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:13.5px">${esc(r.name)}</div>
            <div class="mono" style="font-size:11.5px;color:var(--text-faint);margin-top:2px">${esc(r.path)}</div>
          </div>
          <span class="pill pill-neutral">${esc(r.kind || "file")}</span>
        </div>
        ${r.desc ? `<div style="font-size:12.5px;color:var(--text-faint);margin:8px 0 0 27px">${esc(r.desc)}</div>` : ""}
        ${open ? `
          <div style="margin:12px 0 0 27px;border-top:1px solid var(--divider);padding-top:12px">
            ${content === undefined
              ? `<div class="empty-note">${ICONS.spinner} Loading…</div>`
              : `<div class="code-pane" style="max-height:420px;overflow:auto">${codeView(content, { xml: r.path.endsWith(".ptx") || r.path.endsWith(".xml"), wrap: state.wsWrap, id: "projectFile" })}</div>`}
          </div>` : ""}
      </div>`;
    }).join("")}
  </div>`;
}

function chapterCard(c) {
  const selected = state.selectedChapters.has(c.id);
  const pill = lifecyclePill(c);
  const banner = chapterBanner(c);
  return `
  <div class="chapter-card ${selected ? "selected" : ""}">
    <div class="row" style="align-items:flex-start;gap:12px">
      <input type="checkbox" class="checkbox" style="margin-top:4px" data-action="toggleChapterSelect" data-id="${esc(c.id)}" ${selected ? "checked" : ""} />
      <div style="flex:1;min-width:0;cursor:pointer" data-action="openWorkspace" data-id="${esc(c.id)}">
        <div class="chapter-kind">${esc((c.type || "").toUpperCase())}</div>
        <div class="chapter-title">${esc(c.title)}</div>
      </div>
      ${pill}
    </div>
    <div class="chapter-source">${esc(c.file)}</div>
    ${stepRow(c)}
    ${banner}
    ${state.remapOpenFor === c.id ? `
      <div class="remap-inline">
        <input class="input mono" id="remapInput" placeholder="new/source/path.Rmd" value="${esc(state.remapValue)}" data-action="onRemapInput" />
        <button class="btn btn-sm btn-primary" data-action="submitRemap" data-id="${esc(c.id)}">Save</button>
        <button class="btn btn-sm" data-action="cancelRemap">Cancel</button>
      </div>` : ""}
    <div class="chapter-footer">
      ${chapterActions(c)}
      <div class="chapter-footer-spacer"></div>
      ${!c._executing ? `<button class="btn btn-sm btn-icon" title="Remap source" data-action="toggleRemap" data-id="${esc(c.id)}">${ICONS.pin}</button>` : ""}
    </div>
  </div>`;
}

function stepRow(c) {
  const st = c.stages || {};
  const steps = [
    ["read", "Read"],
    ["translate", "Translate"],
    ["validate", "Validate"],
    ["enhance", "Enhance · soon"],
  ];
  // While executing, only one of translate/validate is actually "in
  // progress" at a time: translate first (no output yet), then — once
  // translate has produced output (stages.translate reads "done" even
  // before the chapter is fully resolved) — review/validate. Marking both
  // as running simultaneously looked like the whole pipeline was spinning
  // at once regardless of which stage was actually active.
  const translateDone = st.translate === "done";
  return `<div class="step-row">${steps.map(([key, label], i) => {
    let visual = st[key] || "pending";
    if (c._executing) {
      if (key === "translate" && !translateDone) visual = "running";
      else if (key === "validate") visual = translateDone ? "running" : "pending";
    }
    const icon = visual === "done" ? ICONS.check : visual === "running" ? ICONS.spinner : "";
    return `${i > 0 ? '<div class="step-connector"></div>' : ""}
      <div class="step">
        <div class="step-circle ${visual}">${icon}</div>
        <span class="step-label ${visual}">${label}</span>
      </div>`;
  }).join("")}</div>`;
}

function lifecyclePill(c) {
  if (c._executing) return `<span class="pill pill-info">${ICONS.spinner} Running</span>`;
  // A chapter whose run died part-way (or whose background task raised) is not
  // "needs review" — nothing reviewed it. Say so plainly.
  if (c._error || c.gate?.failed) return `<span class="pill pill-warn" style="background:var(--danger-bg);color:var(--danger-text)"><span class="pill-dot" style="background:var(--danger-text)"></span>Failed</span>`;
  if (c.lifecycle === "review_required") return `<span class="pill pill-warn"><span class="pill-dot"></span>Needs review</span>`;
  if (c.lifecycle === "approved") return `<span class="pill pill-ok"><span class="pill-dot"></span>Approved</span>`;
  return `<span class="pill pill-neutral"><span class="pill-dot"></span>Not started</span>`;
}

function chapterBanner(c) {
  // Never show a failure/gate banner on a chapter that's mid-run — whatever
  // the last snapshot said is being superseded right now, and a red "stopped
  // before completing" on a chapter that is visibly still working is worse
  // than showing nothing until it settles.
  if (c._executing) return "";
  const bad = c.gate?.failed || c._error;
  if (c.gate?.stage === "translate" || c._error) {
    const message = c.gate?.message || "Chapter run failed";
    return `<div class="chapter-banner ${bad ? "" : "pill-warn"}" style="${bad ? "background:var(--danger-bg);color:var(--danger-text)" : ""}">
      <span class="pill-dot" style="background:${bad ? "var(--danger-text)" : "var(--warn-dot)"}"></span>${esc(message)}
      ${c._error ? `<div class="mono" style="margin-top:4px;font-size:11px;opacity:.85;word-break:break-word">${esc(c._error)}</div>` : ""}
    </div>`;
  }
  return "";
}

function chapterActions(c) {
  if (c._executing) return `<span class="spinner-inline">${ICONS.spinner} Working…</span> <button class="btn btn-sm" data-action="openWorkspace" data-id="${esc(c.id)}">Review</button>`;
  if (c.lifecycle === "review_required") {
    return `
      <button class="btn btn-sm" data-action="openWorkspace" data-id="${esc(c.id)}">Review</button>
      <button class="btn btn-sm" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="retry">${ICONS.retry} Retry</button>
      <button class="btn btn-sm btn-primary" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="approve">Accept</button>`;
  }
  if (c.lifecycle === "approved") {
    return `<button class="btn btn-sm" data-action="openWorkspace" data-id="${esc(c.id)}">Review</button>`;
  }
  return `<button class="btn btn-sm btn-primary" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="translate">${ICONS.play} Run</button>`;
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

function renderWorkspace() {
  const view = state.view;
  if (!view) return topbar("Workspace", "No run loaded.") + `<div class="content"><button class="btn" data-action="nav" data-tab="newrun">Start a run</button></div>`;
  const chapters = view.chapters || [];
  if (!chapters.length) return topbar("Workspace", "This run has no chapters.");
  if (!state.wsChapterId || !chapterById(state.wsChapterId)) state.wsChapterId = chapters[0].id;
  const c = chapterById(state.wsChapterId);
  const idx = currentChapterIndex();
  const atGate = c.gate?.stage === "translate";
  const started = c.lifecycle !== "translation_ready" || atGate;

  // A background action (retry/accept/edit/etc.) just finished on this
  // chapter while the workspace was sitting on it — force everything to
  // refetch instead of continuing to show what was loaded before it ran
  // (previously this only happened if you left the workspace and came back).
  if (state.wsLastExecuting && !c._executing) {
    state.wsFindingsChapter = null; state.wsFixQueue.clear(); state.wsFindings = undefined;
    state.wsContentChapter = null; state.wsSourceContent = null; state.wsPtxContent = null;
    state.wsRenderKey = null; state.wsRenderStatus = "idle";
  }
  state.wsLastExecuting = !!c._executing;

  if (state.wsFindingsChapter !== c.id && atGate) {
    state.wsFindingsChapter = c.id;
    LyreAPI.chapterFindings(state.runId, c.id).then((r) => { state.wsFindings = r.findings; rerender(); }).catch(() => { state.wsFindings = null; });
  }
  // Skip fetching content/checkpoints while the chapter is actively running —
  // the file may be mid-write, and the invalidation above guarantees a fresh
  // fetch fires as soon as it finishes.
  if (state.wsContentChapter !== c.id && !c._executing) {
    state.wsContentChapter = c.id;
    state.wsSourceContent = null; state.wsPtxContent = null;
    state.wsRenderKey = null;
    LyreAPI.readFile(c.file).then((r) => { state.wsSourceContent = r.content; rerender(); }).catch(() => { state.wsSourceContent = "(could not read source file)"; });
    if (started) LyreAPI.readFile(c.output_file).then((r) => { state.wsPtxContent = r.content; rerender(); }).catch(() => { state.wsPtxContent = "(no PreTeXt output yet)"; });
  }
  ensureRenderLoaded(c, started);
  const subtitle = c._executing
    ? "Working — this chapter is currently running."
    : atGate ? "Reviewing the Translate output before validation."
    : (started ? "Translation approved." : "Not yet translated — run this chapter from Chapters.");

  return `
    ${topbar(c.title, subtitle, workspaceHeaderRight(c, idx, chapters.length))}
    <div class="content">
      ${!started && !c._executing ? emptyWorkspace(c) : `
        ${c._executing ? executingBanner() : (atGate ? gatePanel(c) : (c.lifecycle === "approved" ? approvedActionBar(c) : ""))}
        ${compareModeToggle()}
        ${comparePanes(c)}
        ${atGate && !c._executing ? resolveGrid(c) : ""}
      `}
    </div>
  `;
}

function workspaceHeaderRight(c, idx, total) {
  return `<div class="row gap-2">
    <div class="row" style="background:var(--surface);border:1px solid var(--border-strong);border-radius:9px;padding:2px">
      <button class="btn btn-icon btn-sm" style="border:none" data-action="wsPrev" ${idx <= 0 ? "disabled" : ""}>${ICONS.chevronLeft}</button>
      <span style="font-size:12px;font-weight:600;color:var(--text-muted);padding:0 6px">Chapter ${idx + 1} / ${total}</span>
      <button class="btn btn-icon btn-sm" style="border:none" data-action="wsNext" ${idx >= total - 1 ? "disabled" : ""}>${ICONS.chevronRight}</button>
    </div>
    <button class="btn btn-sm" disabled title="Checkpoint history &amp; revert — coming soon">Checkpoints · soon</button>
  </div>`;
}

function emptyWorkspace(c) {
  return `<div class="card" style="text-align:center;padding:50px 30px">
    <p style="margin:0 0 16px;color:var(--text-muted)">This chapter hasn't been translated yet.</p>
    <button class="btn btn-primary" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="translate">${ICONS.play} Run this chapter</button>
  </div>`;
}

function executingBanner() {
  return `<div class="card" style="margin-bottom:20px;display:flex;align-items:center;gap:12px">
    <span class="spinner-inline">${ICONS.spinner} Working…</span>
    <span style="font-size:13px;color:var(--text-faint)">This chapter is running — the page updates automatically when it finishes.</span>
  </div>`;
}

function approvedActionBar(c) {
  return `<div class="card card-sm" style="margin-bottom:16px">
    <div class="row gap-3" style="flex-wrap:wrap">
      <span class="pill pill-ok"><span class="pill-dot"></span>Approved</span>
      <span style="font-size:12.5px;color:var(--text-faint);flex:1">Not right after all? Both options below reopen the review gate for this chapter.</span>
    </div>
    <div class="row gap-2" style="margin-top:10px">
      <button class="btn btn-sm" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="revalidate" title="Re-run the automated checks against the current PreTeXt output, without retranslating">Re-check output</button>
      <button class="btn btn-sm" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="retry" title="Discard the current PreTeXt output and translate this chapter again from source">${ICONS.retry} Retranslate from source</button>
    </div>
  </div>`;
}

function gatePanel(c) {
  const counts = c.findings || { error: 0, warn: 0, info: 0 };
  const hasFindings = counts.error + counts.warn + counts.info > 0;
  const summary = hasFindings
    ? `${counts.error} error${counts.error === 1 ? "" : "s"} · ${counts.warn} warning${counts.warn === 1 ? "" : "s"} · ${counts.info} info`
    : "Nothing flagged";
  const issues = state.wsFindings?.issues || [];
  return `
  <div class="card gate-panel" style="margin-bottom:20px">
    <div class="row gap-3" style="flex-wrap:wrap">
      <span class="row gap-3" style="cursor:pointer;flex:1;min-width:0" data-action="toggleWsFindings">
        <span style="transform:rotate(${state.wsFindingsOpen ? 90 : 0}deg);transition:transform .15s;display:inline-flex">${ICONS.chevronRight}</span>
        <span class="pill pill-warn"><span class="pill-dot" style="background:var(--warn-dot)"></span>Review gate · human decision required</span>
        <span style="font-size:12.5px;font-weight:600;color:var(--text-faint)">${summary}</span>
        ${c.gate?.iteration_count > 1 ? `<span style="font-size:12px;color:var(--text-ghost)">revision ${esc(c.gate.iteration_count)}</span>` : ""}
      </span>
      ${!hasFindings ? `<button class="btn btn-sm btn-primary" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="approve">${ICONS.check} Accept as-is</button>` : ""}
    </div>
    ${state.wsFindingsOpen ? `
      ${issues.length ? findingsToolbar(c, issues) : ""}
      <div style="display:flex;flex-direction:column;gap:9px;margin:${issues.length ? "10px" : "14px"} 0 2px">
        ${issues.length
          ? findingGroups(issues).map((g) => findingRow(c, g)).join("")
          : `<div class="empty-note">${state.wsFindings === undefined ? "Loading findings…" : "No findings recorded."}</div>`}
      </div>
      ${issues.length ? fixQueueBar(c, issues) : ""}` : ""}
  </div>`;
}

// --- Finding grouping -------------------------------------------------------
// The check-agents report one systematic problem once per occurrence, so a
// chapter routinely comes back with twenty findings that say the same thing at
// twenty different lines. Grouped mode collapses those into one row whose Fix
// button hands the whole set to the editing agent in a single run.
//
// Two findings group only when the text of the finding is identical and just
// the location differs, per the grouping rule: their check, severity, message
// and suggestion must match once standalone integers are masked out. Masking is
// deliberately narrow — digits glued to other characters (2x, h2, 2.1) are left
// alone, so only line-number-shaped differences are absorbed.
// The identity itself is decided server-side and arrives as group_key (see
// lyretext/review/grouping.py): finalize_review needs the same rule to carry
// a dismissal across review passes, and two copies of it would drift apart.
// The local fallback only serves sidecars written before group_key existed.
function findingGroupKey(f) {
  if (f.group_key) return f.group_key;
  const mask = (s) => String(s == null ? "" : s).replace(/(?<![\w.])\d+(?![\w.])/g, "#");
  return [f.check_id || "", f.severity || "", mask(f.message), mask(f.suggestion)].join(" | ");
}

function findingGroups(issues) {
  if (!state.wsFindingsGrouped) {
    return issues.map((f, idx) => ({ rep: f, items: [f], indices: [idx] }));
  }
  const groups = [];
  const byKey = new Map();
  issues.forEach((f, idx) => {
    const key = findingGroupKey(f);
    let g = byKey.get(key);
    if (!g) { g = { rep: f, items: [], indices: [] }; byKey.set(key, g); groups.push(g); }
    g.items.push(f);
    g.indices.push(idx);
  });
  return groups;
}

function findingsToolbar(c, issues) {
  const grouped = findingGroups(issues).length;
  const label = state.wsFindingsGrouped
    ? `${issues.length} findings in ${grouped} group${grouped === 1 ? "" : "s"}`
    : `${issues.length} finding${issues.length === 1 ? "" : "s"}, listed individually`;
  const selectable = issues.reduce((n, f) => n + (f.ignored || f.status === "fixed" ? 0 : 1), 0);
  const queued = state.wsFixQueue.size;
  return `<div class="row gap-2" style="margin-top:12px;flex-wrap:wrap">
    <span style="font-size:11px;letter-spacing:.07em;font-weight:600;color:var(--text-ghost);margin-right:2px">FINDINGS</span>
    <button class="btn btn-sm ${state.wsFindingsGrouped ? "btn-primary" : ""}" data-action="setFindingsGrouped" data-grouped="1" title="Collapse findings that describe the same fix into one row, whatever their line numbers">Grouped</button>
    <button class="btn btn-sm ${state.wsFindingsGrouped ? "" : "btn-primary"}" data-action="setFindingsGrouped" data-grouped="0" title="List every finding separately, with its own Fix button">Individual</button>
    <span style="font-size:12px;color:var(--text-faint)">${esc(label)}</span>
    ${selectable ? `<button class="btn btn-sm btn-ghost" data-action="queueAllFixes" style="margin-left:auto" title="Tick every finding, then apply them all in one pass">${queued >= selectable ? "Deselect all" : "Select all"}</button>` : ""}
  </div>`;
}

// The queue bar. Only rendered with something in it, so the panel stays quiet
// until the user has actually started building a batch.
function fixQueueBar(c, issues) {
  const queued = state.wsFixQueue.size;
  if (!queued) return "";
  const passes = new Set(
    [...state.wsFixQueue].map((i) => issues[i] && findingGroupKey(issues[i])).filter(Boolean)
  ).size;
  return `<div class="row gap-2" style="margin-top:10px;padding:9px 11px;border:1px solid var(--accent-soft-border);background:var(--accent-soft-bg);border-radius:9px;flex-wrap:wrap">
    <span style="font-size:12.5px;font-weight:600;color:var(--accent-strong);flex:1;min-width:0">
      ${queued} finding${queued === 1 ? "" : "s"} queued${passes && passes !== queued ? ` · ${passes} distinct fix${passes === 1 ? "" : "es"}` : ""}
    </span>
    <button class="btn btn-sm btn-ghost" data-action="clearFixQueue">Clear</button>
    <button class="btn btn-sm btn-primary" data-action="applyFixQueue" data-id="${esc(c.id)}" title="Send all queued findings to the editing agent in a single pass">Apply ${queued} fix${queued === 1 ? "" : "es"}</button>
  </div>`;
}

function findingRow(c, g) {
  const rep = g.rep;
  // Only live occurrences are acted on: ignored ones have been dealt with and
  // re-sending them would resurrect a finding the user dismissed; fixed ones
  // are ones the latest review no longer sees (auto-resolved, #33).
  const live = g.indices.filter((_, k) => !g.items[k].ignored && g.items[k].status !== "fixed");
  const count = g.indices.length;
  // A group with no live occurrences is either user-dismissed or auto-resolved;
  // the latter (no ignored occurrence) gets a "resolved" affordance, not un-ignore.
  const resolved = live.length === 0 && g.items.every((f) => f.status === "fixed" && !f.ignored);
  const dimmed = live.length === 0;
  const idxs = live.join(",");
  const allIdxs = g.indices.join(",");
  const single = count === 1;

  // Findings now group by their suggestion, so a group's messages may differ
  // (a check that names the offending element writes a new one each time). The
  // badge tooltip carries that occurrence's own message, so nothing is lost
  // when the shared headline can't show it.
  const messages = [...new Set(g.items.map((f) => f.message))];
  const oneMessage = messages.length === 1;

  const lineBadges = g.items
    .map((f, k) => ({ line: f.line, message: f.message }))
    .filter((x) => x.line)
    .map((x) => {
      const hint = oneMessage ? `Show line ${x.line} in the PreTeXt pane` : x.message;
      return `<button class="sev sev-line sev-jump" data-action="jumpToLine" data-line="${esc(x.line)}" title="${esc(hint)}">L${esc(x.line)}</button>`;
    })
    .join("");

  // Every finding is fixable from here. auto_fixable governs only whether the
  // optional automatic loop may act on it unasked — at a gate whose whole point
  // is that a human is deciding, it has no say in what that human may ask for.
  const fixable = live.length > 0;
  const fixLabel = single ? "Fix" : `Fix all ${count}`;
  const ignoreLabel = single ? "Ignore" : `Ignore all ${count}`;
  const action = single ? "fixIssue" : "fixIssueGroup";
  const dismissAction = single ? "dismissIssue" : "dismissIssueGroup";

  const queued = live.length > 0 && live.every((i) => state.wsFixQueue.has(i));
  const headline = oneMessage
    ? esc(rep.message)
    : `${count} findings resolved by the same fix`;

  return `<div class="row" style="align-items:flex-start;gap:10px;${dimmed ? "opacity:0.4" : ""}">
    ${live.length
      ? `<input type="checkbox" data-action="toggleFixQueue" data-idxs="${esc(idxs)}" ${queued ? "checked" : ""} title="Queue this fix to apply with the others in one pass" style="margin-top:3px;flex-shrink:0;cursor:pointer">`
      : `<span style="width:13px;flex-shrink:0"></span>`}
    ${single ? lineBadges : `<span class="sev sev-line" title="${count} occurrences of this fix" style="flex-shrink:0">×${count}</span>`}
    <span class="sev sev-${esc(rep.severity)}">${esc(rep.severity)}</span>
    <span style="flex:1;min-width:0;font-size:13px;color:var(--text-soft);line-height:1.5">
      ${headline}
      ${rep.suggestion ? `<br><span style="color:var(--text-faint);font-style:italic">Suggestion: ${esc(rep.suggestion)}</span>` : ""}
      ${!single && lineBadges ? `<span class="row gap-2" style="flex-wrap:wrap;margin-top:6px">${lineBadges}</span>` : ""}
    </span>
    <span class="row gap-2" style="flex-shrink:0">
      ${fixable ? `<button class="btn btn-sm" data-action="${action}" data-id="${esc(c.id)}" data-idx="${esc(idxs)}" data-idxs="${esc(idxs)}">${esc(fixLabel)}</button>` : ""}
      ${live.length
        ? `<button class="btn btn-sm btn-ghost" data-action="${dismissAction}" data-id="${esc(c.id)}" data-idx="${esc(idxs)}" data-idxs="${esc(idxs)}">${esc(ignoreLabel)}</button>`
        : resolved
          ? `<span class="sev sev-info" title="The latest review no longer finds this — auto-resolved">${ICONS.check} Resolved</span>`
          : `<button class="btn btn-sm btn-ghost" data-action="restoreIssues" data-id="${esc(c.id)}" data-idxs="${esc(allIdxs)}" title="Dismissals persist across re-reviews — this brings the finding back">Un-ignore</button>`}
    </span>
  </div>`;
}

function compareModeToggle() {
  return `<div class="row gap-2" style="margin-bottom:14px;flex-wrap:wrap">
    <span style="font-size:11px;letter-spacing:.07em;font-weight:600;color:var(--text-ghost);margin-right:2px">COMPARE</span>
    <button class="btn btn-sm ${state.wsMode === "translation" ? "btn-primary" : ""}" data-action="setWsMode" data-mode="translation">Review translation · Source ↔ PreTeXt</button>
    <button class="btn btn-sm ${state.wsMode === "render" ? "btn-primary" : ""}" data-action="setWsMode" data-mode="render">Review render · PreTeXt ↔ Render</button>
  </div>`;
}

function comparePanes(c) {
  const translation = state.wsMode === "translation";
  const left = translation
    ? { label: "Source · R Markdown", target: "source", content: state.wsSourceContent }
    : { label: "PreTeXt", target: "output", content: state.wsPtxContent };
  const right = translation
    ? { label: "PreTeXt", target: "output", content: state.wsPtxContent }
    : { label: "Rendered output", target: null, content: null, render: true };
  return `<div class="compare-row">${pane(c, left, "left")}${pane(c, right, "right")}</div>`;
}

// Gutter marks for the PreTeXt pane, built from the review findings so a
// reported line is visible in the code itself and not only in the list above.
// Errors win over warnings on a shared line; dismissed findings drop out, in
// step with the counts.
function findingMarkers() {
  const issues = state.wsFindings?.issues || [];
  const markers = {};
  for (const f of issues) {
    const line = parseInt(f.line, 10);
    if (f.ignored || !Number.isFinite(line) || line < 1) continue;
    const sev = f.severity in SEV_RANK ? f.severity : "info";
    if (!markers[line] || SEV_RANK[sev] > SEV_RANK[markers[line]]) markers[line] = sev;
  }
  return markers;
}

function pane(c, spec, side) {
  const editing = state.wsEditTarget === spec.target && spec.target;
  const isXml = spec.target === "output";
  const paneId = spec.target ? `wsPane-${spec.target}` : null;
  const text = spec.content;

  let body;
  if (spec.render) {
    body = renderPaneBody(c);
  } else if (editing) {
    body = `<div class="code-editor" style="${gutterVar(lineCount(state.wsEditContent))}">
      <div class="code-editor-gutter" id="wsEditGutter">${editorGutter(state.wsEditContent, state.wsEditLine, state.wsEditMarkers)}</div>
      <textarea id="wsEditArea" wrap="off" spellcheck="false" data-action="onWsEditInput">${esc(state.wsEditContent)}</textarea>
    </div>`;
  } else if (text == null) {
    body = `<div class="empty-note">${ICONS.spinner} Loading…</div>`;
  } else {
    body = codeView(text, {
      xml: isXml,
      wrap: state.wsWrap,
      id: paneId,
      markers: isXml ? findingMarkers() : null,
    });
  }

  const meta = editing
    ? `<span class="code-meta mono" id="wsEditPos">Ln ${state.wsEditLine}, Col ${state.wsEditCol}</span>`
    : spec.render || text == null ? "" : `<span class="code-meta mono">${codeStats(text)}</span>`;

  const tools = editing
    ? `<button class="btn btn-sm" data-action="cancelEdit">Cancel</button>
       <button class="btn btn-sm btn-primary" data-action="saveEdit" data-id="${esc(c.id)}" data-target="${spec.target}" ${state.wsSaving ? "disabled" : ""}>${state.wsSaving ? ICONS.spinner : ICONS.check} Save</button>`
    : spec.render
      ? `<button class="btn btn-sm btn-ghost" data-action="refreshRender" data-id="${esc(c.id)}">${ICONS.retry} refresh</button>`
      : `${text != null ? `
          <button class="btn btn-icon btn-sm btn-ghost" data-action="toggleWrap" title="${state.wsWrap ? "Wrapping long lines — click for no wrap" : "Not wrapping — click to wrap long lines"}" aria-pressed="${state.wsWrap}">${ICONS.wrap}</button>
          <button class="btn btn-icon btn-sm btn-ghost" data-action="copyPane" data-target="${spec.target}" title="Copy all to clipboard">${ICONS.copy}</button>` : ""}
        ${spec.target
          ? `<button class="btn btn-sm btn-ghost" data-action="startEdit" data-target="${spec.target}">${ICONS.edit} edit directly</button>`
          : `<span style="font-size:11px;letter-spacing:.07em;font-weight:600;color:var(--text-ghost)">READ-ONLY</span>`}`;

  return `<div class="pane">
    <div class="pane-head">
      <span class="row gap-3" style="min-width:0">
        <span style="font-size:14px;font-weight:600">${esc(spec.label)}</span>
        ${meta}
      </span>
      <div class="row gap-2">${tools}</div>
    </div>
    <div class="pane-body${spec.render ? "" : " code-pane"}${editing ? " editing" : ""}">${body}</div>
  </div>`;
}

// Fetch the render for the current chapter, exactly once per chapter.
//
// The status is set explicitly at every exit, so there is no path that leaves
// the pane spinning: if a guard blocks the fetch the pane says so and offers a
// button, and the request itself is raced against a timeout so a hung or
// dropped response becomes a visible error rather than a permanent spinner.
const RENDER_TIMEOUT_MS = 20000;

function ensureRenderLoaded(c, started) {
  if (state.wsMode !== "render") return;

  const blocked = c._executing ? "running" : !started ? "not-translated" : null;
  if (blocked) {
    state.wsRenderKey = null;
    state.wsRenderStatus = "blocked";
    state.wsRenderBlocked = blocked;
    return;
  }
  if (state.wsRenderKey === c.id) return;  // already loading, loaded, or failed

  // A browser holding a cached api-client.js from before this endpoint
  // existed would otherwise throw here, mid-template — which aborts the whole
  // re-render and freezes the page on its previous DOM. Say what's wrong.
  if (typeof LyreAPI.chapterRender !== "function") {
    state.wsRenderKey = c.id;
    state.wsRenderStatus = "error";
    state.wsRender = { fetchError: "The UI scripts in your browser are out of date. Reload with Ctrl+Shift+R." };
    return;
  }

  const key = c.id;
  state.wsRenderKey = key;
  state.wsRenderStatus = "loading";
  state.wsRender = null;

  const timeout = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`no response after ${RENDER_TIMEOUT_MS / 1000}s`)), RENDER_TIMEOUT_MS)
  );
  Promise.race([LyreAPI.chapterRender(state.runId, key), timeout])
    .then((r) => {
      if (state.wsRenderKey !== key) return;   // chapter changed under us
      state.wsRender = r;
      state.wsRenderStatus = "ready";
      rerender();
    })
    .catch((err) => {
      if (state.wsRenderKey !== key) return;
      console.error("chapter render failed:", err);
      state.wsRender = { fetchError: err.message || String(err) };
      state.wsRenderStatus = "error";
      rerender();
    });
}

// The rendered PreTeXt. The HTML comes from lyretext/render/pretext_html.py,
// which builds it from a parsed tree with every text node escaped — it is not
// pass-through of anything a user typed, so injecting it is safe.
function renderPaneBody(c) {
  const retry = `<button class="btn btn-sm btn-primary" data-action="refreshRender" data-id="${esc(c.id)}">${ICONS.retry} Render this chapter</button>`;

  if (state.wsRenderStatus === "loading") {
    return `<div class="empty-note">${ICONS.spinner} Rendering…</div>`;
  }
  if (state.wsRenderStatus === "blocked") {
    return `<div class="empty-note">${state.wsRenderBlocked === "running"
      ? "This chapter is running — the render appears when it finishes."
      : "This chapter hasn't been translated yet, so there's nothing to render."}</div>`;
  }
  if (state.wsRenderStatus === "error") {
    return `<div class="render-error">
      <div style="font-weight:600;margin-bottom:6px">Could not fetch the render</div>
      <div class="mono" style="font-size:12px;word-break:break-word">${esc(state.wsRender?.fetchError || "")}</div>
      <div style="font-size:12.5px;margin:10px 0">If this persists, reload the page with Ctrl+Shift+R — a cached copy of the UI scripts will do this.</div>
      ${retry}
    </div>`;
  }
  if (state.wsRenderStatus !== "ready") {
    return `<div class="empty-note" style="display:flex;flex-direction:column;gap:10px;align-items:flex-start">
      <span>Not rendered yet.</span>${retry}</div>`;
  }

  const r = state.wsRender;
  if (r && r.error) {
    const line = r.error.line ? ` (line ${r.error.line})` : "";
    return `<div class="render-error">
      <div style="font-weight:600;margin-bottom:6px">This chapter's PreTeXt could not be parsed${line}</div>
      <div class="mono" style="font-size:12px;word-break:break-word">${esc(r.error.message || "")}</div>
      <div style="font-size:12.5px;margin-top:10px;opacity:.85">Fix the markup in the PreTeXt pane, or retry the chapter, then refresh.</div>
    </div>`;
  }
  if (!r || !r.html) {
    return `<div class="empty-note" style="display:flex;flex-direction:column;gap:10px;align-items:flex-start">
      <span>The chapter rendered as empty — its <span class="mono">.ptx</span> may not have been written yet.</span>${retry}</div>`;
  }
  const notes = [];
  if (r.unsupported?.length) {
    notes.push(`${r.unsupported.length} element type${r.unsupported.length === 1 ? "" : "s"} not recognised: <span class="mono">${r.unsupported.map(esc).join(", ")}</span>`);
  }
  for (const w of new Set(r.warnings || [])) notes.push(esc(w));
  // Reusing this subtree matters twice over: it is the largest single block of
  // DOM in the app, and it carries MathJax's typeset output, which is far more
  // expensive to reproduce than the HTML itself.
  const sig = `render:${c.id}:${hashString(r.html)}`;
  const notesHtml = notes.length
    ? `<div class="render-notes">${notes.map((n) => `<div>${n}</div>`).join("")}</div>` : "";
  if (canKeep(sig)) return notesHtml + keepPlaceholder(sig);
  return `
    ${notesHtml}
    <div class="ptx-render ${KEEP_CLASS}" id="ptxRender" data-keep="${esc(sig)}">${r.html}</div>`;
}

// MathJax is loaded on demand the first time a render is shown, and typesets
// after every re-render. It's a CDN load like the fonts in index.html; if it
// never arrives the math stays visible as raw TeX rather than disappearing.
let _mathJaxLoading = null;
function ensureMathJax() {
  if (window.MathJax?.typesetPromise) return Promise.resolve();
  if (_mathJaxLoading) return _mathJaxLoading;
  window.MathJax = {
    tex: { inlineMath: [["\\(", "\\)"]], displayMath: [["\\[", "\\]"]] },
    options: { skipHtmlTags: ["script", "noscript", "style", "textarea", "pre", "code"] },
    startup: { typeset: false },
  };
  _mathJaxLoading = new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => resolve(); // raw TeX is a fine degraded state
    document.head.appendChild(s);
  });
  return _mathJaxLoading;
}

function typesetRender() {
  const el = document.getElementById("ptxRender");
  // _ptxTypeset rides along with the node itself. When rerender() moves the
  // pane across instead of rebuilding it (see restoreKeepNodes), the typeset
  // output is still there, and re-running MathJax over the whole chapter every
  // poll tick was one of the largest costs on a long file.
  if (!el || el._ptxTypeset) return;
  ensureMathJax().then(() => {
    if (!window.MathJax?.typesetPromise) return;
    const target = document.getElementById("ptxRender");
    if (!target || target._ptxTypeset) return;
    target._ptxTypeset = true;
    window.MathJax.typesetPromise([target]).catch(() => { target._ptxTypeset = false; });
  });
}

function resolveGrid(c) {
  return `
  <div class="card" style="margin-top:20px">
    <div style="font-size:11px;letter-spacing:.07em;font-weight:600;color:var(--text-ghost);text-transform:uppercase">Resolve the gate — three ways</div>
    <div class="resolve-grid">
      <div class="resolve-card">
        <div class="resolve-title">Accept as-is</div>
        <div class="resolve-desc">Acknowledge the remaining findings and move this chapter on to validation.</div>
        <button class="btn btn-block" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="approve">Accept as-is</button>
      </div>
      <div class="resolve-card highlight">
        <div class="resolve-title">Retry with instructions</div>
        <textarea id="wsRefine" style="height:56px;font-size:12.5px" placeholder="e.g. Wrap all displayed formulas in &lt;me&gt; and add language=&quot;r&quot; to code listings." data-action="onRefineInput">${esc(state.wsRefine)}</textarea>
        <button class="btn btn-primary btn-block mt-6" data-action="chapterQuick" data-id="${esc(c.id)}" data-op="retry" data-instruction="1">Apply &amp; retry translate</button>
      </div>
      <div class="resolve-card">
        <div class="resolve-title">Edit directly</div>
        <div class="resolve-desc">Edit source (recompiles) or the PreTeXt output (revalidates) by hand, above.</div>
        <button class="btn btn-block" data-action="startEdit" data-target="source">Open editor</button>
      </div>
    </div>
    <div class="row gap-3" style="padding-top:16px;border-top:1px solid var(--divider)">
      <span style="font-size:12.5px;color:var(--text-faint);flex:1">Approving accepts the current PreTeXt and advances the chapter to Validate.</span>
      <button class="btn" data-action="nav" data-tab="chapters">Back to chapters</button>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

function renderJobs() {
  const view = state.view;
  if (!view) return topbar("Jobs", "No run loaded.") + `<div class="content"><button class="btn" data-action="nav" data-tab="newrun">Start a run</button></div>`;
  const jobs = view.jobs || [];
  const runLevel = jobs.filter((j) => !j.chapter_id);
  const cards = [{ key: "run", title: "Project setup", steps: runLevel, executing: false, lifecycle: null }];
  for (const c of view.chapters || []) {
    cards.push({ key: c.id, title: c.title, steps: jobs.filter((j) => j.chapter_id === c.id), executing: c._executing, lifecycle: c.lifecycle, gate: c.gate });
  }
  return `
    ${topbar("Jobs", "Pipeline runs and their per-chapter steps.", `<span class="pill pill-neutral mono">run ${esc(state.runId)}</span>`)}
    <div class="content" style="display:flex;flex-direction:column;gap:14px;max-width:1040px">
      ${cards.map(jobCard).join("")}
    </div>
  `;
}

function jobCard(card) {
  const open = state.jobsOpen.has(card.key);
  let status = "Done", statusClass = "pill-ok";
  if (card.key !== "run") {
    if (card.executing) { status = "Running"; statusClass = "pill-info"; }
    else if (card.gate?.failed) { status = "Needs review"; statusClass = "pill-warn"; }
    else if (card.gate) { status = "Needs review"; statusClass = "pill-warn"; }
    else if (card.lifecycle === "approved") { status = "Done"; statusClass = "pill-ok"; }
    else { status = "Queued"; statusClass = "pill-neutral"; }
  }
  return `<div class="job-card">
    <div class="job-head" data-action="toggleJob" data-key="${esc(card.key)}">
      <span class="caret ${open ? "open" : ""}">${ICONS.chevronRight}</span>
      <span style="font-family:var(--font-heading);font-weight:600;font-size:16px;flex:none">${esc(card.title)}</span>
      ${card.key !== "run" ? `<span class="pill ${statusClass}">${status}</span>` : ""}
      <div style="flex:1"></div>
      <span class="mono" style="font-size:11.5px;color:var(--text-ghost)">${card.steps.length} step${card.steps.length === 1 ? "" : "s"}</span>
    </div>
    ${open ? `<div class="job-steps">${card.steps.length ? card.steps.map((s) => `
      <div class="job-step-row">
        <span class="pill-dot" style="background:${s.status === "done" ? "var(--ok-dot)" : s.status === "failed" ? "var(--danger-text)" : s.status === "needs-review" ? "var(--warn-dot)" : "var(--neutral-dot)"}"></span>
        <span style="font-size:13.5px;color:var(--text-soft);flex:1">${esc(s.label)}</span>
        <span class="mono" style="font-size:11.5px;color:var(--text-ghost);width:52px;text-align:right">${esc(s.duration || "—")}</span>
      </div>`).join("") : `<div class="empty-note">No steps recorded yet.</div>`}</div>` : ""}
  </div>`;
}

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

function renderOutput() {
  const view = state.view;
  if (!view) return topbar("Output", "No run loaded.") + `<div class="content"><button class="btn" data-action="nav" data-tab="newrun">Start a run</button></div>`;
  const files = view.output || [];
  if (!state.outputSelected && files.length) state.outputSelected = files[0].file;
  const selectedFile = files.find((f) => f.file === state.outputSelected);
  if (selectedFile && state.outputContent?.path !== selectedFile.path) {
    state.outputContent = { path: selectedFile.path, text: null };
    LyreAPI.readFile(selectedFile.path).then((r) => { if (state.outputContent) { state.outputContent.text = r.content; rerender(); } }).catch(() => {});
  }
  return `
    ${topbar("Output", "Generated PreTeXt files, ready to download.")}
    <div class="content" style="display:grid;grid-template-columns:340px minmax(0,1fr);gap:22px;align-items:start">
      <div class="table-wrap">
        <div style="padding:12px 16px;border-bottom:1px solid var(--divider)">
          <div class="row" style="justify-content:space-between;gap:10px;margin-bottom:10px">
            <span class="mono" style="font-size:11.5px;color:var(--text-ghost)">OUTPUT/</span>
            ${files.length ? `<a class="btn btn-sm btn-primary" href="${LyreAPI.projectZipUrl(state.runId, state.projectRoot || "auto")}">${ICONS.download} Project .zip</a>` : ""}
          </div>
          <label class="row gap-2" style="font-size:11.5px;color:var(--text-ghost)" title="PreTeXt root element for the generated main.ptx. Auto picks book when the project has chapters, else article.">
            <span>main.ptx root</span>
            <select data-action="setProjectRoot" class="mono" style="font-size:11.5px;flex:1">
              ${["auto", "book", "article"].map((r) => `<option value="${r}" ${(state.projectRoot || "auto") === r ? "selected" : ""}>${r}</option>`).join("")}
            </select>
          </label>
        </div>
        ${files.length ? files.map((f) => `
          <div class="row" style="gap:10px;padding:11px 16px;border-top:1px solid var(--divider);cursor:pointer;${f.file === state.outputSelected ? "background:var(--accent-soft-bg);border-left:3px solid var(--accent)" : "border-left:3px solid transparent"}" data-action="selectOutput" data-file="${esc(f.file)}">
            ${ICONS.folder}<span class="mono" style="font-size:12.5px;flex:1;word-break:break-all">${esc(f.file)}</span><span style="font-size:11px;color:var(--text-ghost)">${fmtSize(f.size)}</span>
          </div>`).join("") : `<div class="empty-note" style="padding:14px 16px">No output files yet.</div>`}
      </div>
      <div class="table-wrap">
        <div class="row" style="justify-content:space-between;padding:12px 18px;border-bottom:1px solid var(--divider);gap:12px">
          <span class="row gap-3" style="min-width:0">
            <span class="mono" style="font-size:13px;word-break:break-all">${esc(state.outputSelected || "")}</span>
            ${state.outputContent?.text != null ? `<span class="code-meta mono">${codeStats(state.outputContent.text)}</span>` : ""}
          </span>
          <div class="row gap-2" style="flex:none">
            ${state.outputContent?.text != null ? `
              <button class="btn btn-icon btn-sm btn-ghost" data-action="toggleWrap" title="${state.wsWrap ? "Wrapping long lines — click for no wrap" : "Not wrapping — click to wrap long lines"}" aria-pressed="${state.wsWrap}">${ICONS.wrap}</button>
              <button class="btn btn-icon btn-sm btn-ghost" data-action="copyOutput" title="Copy all to clipboard">${ICONS.copy}</button>` : ""}
            ${selectedFile ? `<a class="btn btn-sm" href="${LyreAPI.downloadOutputUrl(state.runId, selectedFile.file)}">${ICONS.download} Download</a>` : ""}
          </div>
        </div>
        <div class="code-pane" style="padding:14px 0;height:520px;overflow:auto">
          ${state.outputContent
            ? (state.outputContent.text == null
                ? `<div class="empty-note">${ICONS.spinner} Loading…</div>`
                : codeView(state.outputContent.text, { xml: true, wrap: state.wsWrap, id: "outputPreview" }))
            : `<div class="empty-note">Select a file to preview its PreTeXt source.</div>`}
        </div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

const SETTINGS_NODES = ["structure_project", "read_chapter", "translate_chapter", "review_chapter"];

function renderSettings() {
  if (!state.settingsDraft) {
    setTimeout(loadSettings, 0);
    return topbar("Settings", "Defaults applied to new runs of this project.") + `<div class="content">${ICONS.spinner} Loading…</div>`;
  }
  const g = state.settingsDraft.global_options || {};
  const overrides = state.settingsDraft.node_overrides || {};
  const nodeHead = `<div class="settings-node-head"><span>Node</span><span>Override</span><span>Provider</span><span>Model name</span></div>`;
  return `
    ${topbar("Settings", "Defaults applied to new runs of this project.")}
    <div class="content" style="max-width:960px">
      ${settingsCard("Default model", "Applied to every pipeline node unless overridden below.",
        settingsRow("Model provider", "Default provider for all pipeline nodes.", `
          <select class="select" data-action="settingsField" data-field="global.provider">
            <option value="gemini" ${g.provider !== "anthropic" ? "selected" : ""}>Gemini (Google)</option>
            <option value="anthropic" ${g.provider === "anthropic" ? "selected" : ""}>Anthropic · Claude</option>
          </select>`) +
        settingsRow("Model name", `Leave blank to use the default from <span class="mono">GEMINI_MODEL / ANTHROPIC_MODEL</span> env vars.`, `
          <input class="input mono" placeholder="e.g. gemini-2.0-flash" value="${esc(g.llm_model || "")}" data-action="settingsField" data-field="global.llm_model" />`))}

      ${settingsCard("Pipeline &amp; output", "How this project's sources are read, and where translations are written.",
        settingsRow("Pipeline", "Source format this project's chapters are authored in.", `
          <select class="select" data-action="settingsField" data-field="global.pipeline">
            <option value="rmd" ${!g.pipeline || g.pipeline === "rmd" ? "selected" : ""}>R Markdown (.Rmd)</option>
            <option value="qmd" ${g.pipeline === "qmd" ? "selected" : ""}>Quarto (.qmd)</option>
            <option value="tex" ${g.pipeline === "tex" ? "selected" : ""}>LaTeX (.tex)</option>
          </select>`) +
        settingsRow("Apply mode", "Whether translations write files directly or run as a dry run.", `
          <select class="select" data-action="settingsField" data-field="global.apply_mode">
            <option value="auto_apply" ${g.apply_mode !== "dry_run" ? "selected" : ""}>Auto-apply</option>
            <option value="dry_run" ${g.apply_mode === "dry_run" ? "selected" : ""}>Dry run (no writes)</option>
          </select>`) +
        settingsRow("Backups on edit", "Keep a .bak copy whenever a file is overwritten.", `
          <label class="row gap-2" style="cursor:pointer"><input type="checkbox" class="checkbox" ${g.create_backup ? "checked" : ""} data-action="settingsField" data-field="global.create_backup" />Create backups</label>`))}

      ${settingsCard("Per-node overrides", "Override provider or model for individual pipeline nodes. Leave unchecked to inherit the global setting above.",
        nodeHead + SETTINGS_NODES.map((node) => nodeOverrideRow(node, overrides[node])).join(""))}

      <div class="row gap-2 mt-6">
        <button class="btn btn-primary" data-action="saveSettings">Save changes</button>
        <button class="btn" data-action="resetSettings">Reset to defaults</button>
      </div>
    </div>
  `;
}

function settingsCard(title, sub, body) {
  return `<div class="settings-card">
    <div class="settings-card-head">
      <div class="settings-card-title">${title}</div>
      <div class="settings-card-sub">${sub}</div>
    </div>
    ${body}
  </div>`;
}

function settingsRow(label, help, control) {
  return `<div class="settings-row">
    <div><div class="field-label">${label}</div><div class="field-help">${help}</div></div>
    <div class="settings-control">${control}</div>
  </div>`;
}

function nodeOverrideRow(node, ov) {
  const on = !!ov;
  return `<div class="settings-node-row">
    <span class="mono" style="font-size:13px">${esc(node)}</span>
    <label class="row gap-2" style="cursor:pointer;font-size:12.5px"><input type="checkbox" class="checkbox" ${on ? "checked" : ""} data-action="toggleNodeOverride" data-node="${node}" />Override</label>
    <select class="select" data-action="settingsField" data-field="node.${node}.provider" ${!on ? "disabled" : ""}>
      <option value="">— inherit —</option>
      <option value="gemini" ${ov?.provider === "gemini" ? "selected" : ""}>gemini</option>
      <option value="anthropic" ${ov?.provider === "anthropic" ? "selected" : ""}>anthropic</option>
    </select>
    <input class="input mono" placeholder="model name" value="${esc(ov?.llm_model || "")}" data-action="settingsField" data-field="node.${node}.llm_model" ${!on ? "disabled" : ""} />
  </div>`;
}

async function loadSettings() {
  try {
    const s = await LyreAPI.getSettings();
    state.settings = s;
    state.settingsDraft = JSON.parse(JSON.stringify(s));
  } catch (err) {
    state.settingsDraft = { global_options: {}, node_overrides: {} };
    toast(`Could not load settings: ${err.message}`, "error");
  }
  rerender();
}

// ---------------------------------------------------------------------------
// Action registry (delegated events)
// ---------------------------------------------------------------------------

const ACTIONS = {
  nav(el) {
    state.tab = el.dataset.tab;
    if (state.tab === "chapters" || state.tab === "workspace" || state.tab === "jobs" || state.tab === "output") {
      if (state.runId) refreshRun();
    }
    rerender();
  },

  browseFolder() {
    safeAction(async () => {
      const r = await LyreAPI.browseFolder(state.chosenSource);
      if (r.path) {
        state.chosenSource = r.path;
        state.chosenSourceLabel = r.path;
        state.uploadFiles = null;
      }
    });
  },
  useDemo() {
    safeAction(async () => {
      const r = await LyreAPI.demoPath();
      state.chosenSource = r.path;
      state.chosenSourceLabel = `${r.path} (demo project)`;
      state.uploadFiles = null;
    });
  },
  triggerUpload() { document.getElementById("uploadInput")?.click(); },
  onFolderSelected(el) {
    const files = el.files;
    if (!files || !files.length) return;
    state.uploadFiles = files;
    state.chosenSource = "";
    const root = (files[0].webkitRelativePath || files[0].name).split("/")[0];
    state.chosenSourceLabel = `${root} — ${files.length} files selected for upload`;
    rerender();
  },

  startRun() {
    safeAction(async () => {
      state.starting = true;
      rerender();
      // Generate the run_id client-side so output_dir/temp_dir can be scoped
      // to it from the very first request — every run used to share the
      // literal "output"/"temp" directories, so a chapter translated by an
      // earlier run left its .ptx sitting there and a brand-new run would
      // see it via a plain file-existence check and show it as already
      // done, with no chapter thread of its own ever having run.
      const runId = crypto.randomUUID();
      const outputDir = `output/${runId}`;
      const tempDir = `temp/${runId}`;
      let result;
      if (state.uploadFiles) {
        const fd = new FormData();
        for (const f of state.uploadFiles) {
          fd.append("files", f);
          fd.append("paths", f.webkitRelativePath || f.name);
        }
        fd.append("output_dir", outputDir);
        fd.append("temp_dir", tempDir);
        fd.append("run_id", runId);
        result = await LyreAPI.startRunUpload(fd);
      } else {
        result = await LyreAPI.startRun(state.chosenSource, outputDir, tempDir, {}, runId);
      }
      state.runId = result.run_id;
      resetRunScopedState();
      syncUrlToRun();
      await waitForFirstGate();
      state.starting = false;
      startPolling();
    });
  },

  resumeRun(el) {
    safeAction(async () => {
      state.runId = el.dataset.run;
      resetRunScopedState();
      syncUrlToRun();
      await refreshRun({ silent: true });
      state.tab = state.view?.gate?.read_pending ? "manifest" : "chapters";
      startPolling();
    });
  },

  // -- manifest gate -----------------------------------------------------
  toggleManifestSelect(el) {
    const id = el.dataset.id;
    if (state.manifestSelected.has(id)) state.manifestSelected.delete(id); else state.manifestSelected.add(id);
    rerender();
  },
  startManifestEdit() {
    state.manifestEditing = true;
    state.manifestEdit = (state.view.chapters || []).map((c) => ({ id: c.id, type: c.type, name: c.title, source_path: c.file, output_path: c.output_file }));
    rerender();
  },
  cancelManifestEdit() { state.manifestEditing = false; rerender(); },
  editManifestField(el) {
    const idx = Number(el.dataset.idx);
    state.manifestEdit[idx][el.dataset.field] = el.value;
    // Stable per-field ids (manifestEdit-{i}-{field}) let rerender() restore
    // focus + cursor position, so it's safe to rerender on every change —
    // needed so picking a new type visibly recolors the select immediately
    // instead of only catching up on the next unrelated render.
    rerender();
  },
  saveManifestEdit() {
    safeAction(async () => {
      const manifest = state.manifestEdit.map((r) => ({ type: r.type, name: r.name, source_path: r.source_path, output_path: r.output_path }));
      await LyreAPI.applyGateDecision(state.runId, "edit_manifest", { manifest });
      state.manifestEditing = false;
      await settleAfterGate();
    });
  },
  gateSelect() { safeAction(async () => { await LyreAPI.applyGateDecision(state.runId, "select_chapters", { chapter_ids: [...state.manifestSelected] }); await settleAfterGate(); }); },
  gateSkip() { safeAction(async () => { await LyreAPI.applyGateDecision(state.runId, "skip_chapters", { chapter_ids: [...state.manifestSelected] }); await settleAfterGate(); }); },
  gateAbort() {
    safeAction(async () => {
      await LyreAPI.applyGateDecision(state.runId, "abort_run");
      stopPolling();
      state.runId = null; state.view = null; state.tab = "newrun";
      state.runsListLoaded = false;
      resetRunScopedState();
      syncUrlToRun();
      toast("Run aborted");
    });
  },

  // -- chapters ------------------------------------------------------------
  onSearch(el) { state.search = el.value; rerender(); },
  setFilter(el) { state.filter = el.dataset.filter; rerender(); },
  setChaptersView(el) { state.chaptersView = el.dataset.view; rerender(); },
  toggleProjectFile(el) {
    const path = el.dataset.path;
    if (state.projectFileOpen === path) { state.projectFileOpen = null; rerender(); return; }
    state.projectFileOpen = path;
    rerender();
    if (state.projectFileContent[path] === undefined) {
      LyreAPI.readFile(path)
        .then((r) => { state.projectFileContent[path] = r.content; rerender(); })
        .catch(() => { state.projectFileContent[path] = "(could not read file)"; rerender(); });
    }
  },
  toggleChapterSelect(el) {
    const id = el.dataset.id;
    if (state.selectedChapters.has(id)) state.selectedChapters.delete(id); else state.selectedChapters.add(id);
    rerender();
  },
  clearSelection() { state.selectedChapters.clear(); rerender(); },
  runAll() {
    safeAction(async () => {
      const ids = (state.view.chapters || []).filter((c) => c.lifecycle === "translation_ready" && !c._executing).map((c) => c.id);
      if (!ids.length) return;
      await LyreAPI.chaptersCommandBatch(state.runId, ids, "translate");
      await refreshRun({ silent: true });
    });
  },
  runSelected() {
    safeAction(async () => {
      const ids = [...state.selectedChapters];
      if (!ids.length) return;
      await LyreAPI.chaptersCommandBatch(state.runId, ids, "translate");
      state.selectedChapters.clear();
      await refreshRun({ silent: true });
    });
  },
  chapterQuick(el) {
    safeAction(async () => {
      const instruction = el.dataset.instruction ? state.wsRefine : undefined;
      await LyreAPI.chapterCommand(state.runId, el.dataset.id, el.dataset.op, instruction);
      if (instruction) state.wsRefine = "";
      await refreshRun({ silent: true });
    });
  },
  toggleRemap(el) {
    state.remapOpenFor = state.remapOpenFor === el.dataset.id ? null : el.dataset.id;
    state.remapValue = "";
    rerender();
  },
  onRemapInput(el) { state.remapValue = el.value; },
  cancelRemap() { state.remapOpenFor = null; rerender(); },
  submitRemap(el) {
    safeAction(async () => {
      await LyreAPI.remapChapter(state.runId, el.dataset.id, state.remapValue);
      state.remapOpenFor = null;
      toast("Source path remapped");
      await refreshRun({ silent: true });
    });
  },
  openWorkspace(el) {
    state.wsChapterId = el.dataset.id;
    state.wsFindingsChapter = null; state.wsFixQueue.clear(); state.wsContentChapter = null;
    state.wsEditTarget = null;
    state.tab = "workspace";
    rerender();
  },

  // -- workspace --------------------------------------------------------
  wsPrev() {
    const list = state.view.chapters || []; const i = currentChapterIndex();
    if (i > 0) { state.wsChapterId = list[i - 1].id; resetWsPaneState(); rerender(); }
  },
  wsNext() {
    const list = state.view.chapters || []; const i = currentChapterIndex();
    if (i < list.length - 1) { state.wsChapterId = list[i + 1].id; resetWsPaneState(); rerender(); }
  },
  toggleWsFindings() { state.wsFindingsOpen = !state.wsFindingsOpen; rerender(); },
  setFindingsGrouped(el) {
    state.wsFindingsGrouped = el.dataset.grouped === "1";
    localStorage.setItem("lyretext.groupFindings", state.wsFindingsGrouped ? "1" : "0");
    rerender();
  },
  dismissIssue(el) {
    safeAction(async () => {
      const idx = parseInt(el.dataset.idx, 10);
      const chId = el.dataset.id;
      const findings = await LyreAPI.dismissIssue(state.runId, chId, idx);
      state.wsFindings = findings;
      // A finding the user just dismissed must not still go out with the batch.
      state.wsFixQueue.delete(idx);
      await refreshRun({ silent: true });
    });
  },
  dismissIssueGroup(el) {
    safeAction(async () => {
      const idxs = issueIndices(el);
      const chId = el.dataset.id;
      if (!idxs.length) return;
      const findings = await LyreAPI.dismissIssueGroup(state.runId, chId, idxs);
      state.wsFindings = findings;
      for (const i of idxs) state.wsFixQueue.delete(i);
      await refreshRun({ silent: true });
    });
  },
  fixIssue(el) {
    safeAction(async () => {
      const idx = parseInt(el.dataset.idx, 10);
      const chId = el.dataset.id;
      await LyreAPI.fixIssue(state.runId, chId, idx);
      // The chapter re-enters the pipeline (edit_chapter); refresh status.
      await refreshRun({ silent: true });
    });
  },
  toggleFixQueue(el) {
    const idxs = issueIndices(el);
    const queued = idxs.every((i) => state.wsFixQueue.has(i));
    for (const i of idxs) {
      if (queued) state.wsFixQueue.delete(i);
      else state.wsFixQueue.add(i);
    }
    rerender();
  },
  queueAllFixes() {
    const issues = state.wsFindings?.issues || [];
    const selectable = issues.map((f, i) => (f.ignored ? -1 : i)).filter((i) => i >= 0);
    if (selectable.every((i) => state.wsFixQueue.has(i))) state.wsFixQueue.clear();
    else for (const i of selectable) state.wsFixQueue.add(i);
    rerender();
  },
  clearFixQueue() { state.wsFixQueue.clear(); rerender(); },
  applyFixQueue(el) {
    safeAction(async () => {
      const idxs = [...state.wsFixQueue];
      const chId = el.dataset.id;
      if (!idxs.length) return;
      // One call, one edit_chapter run, one review cycle — however many
      // findings are in the queue.
      await LyreAPI.fixIssueGroup(state.runId, chId, idxs);
      state.wsFixQueue.clear();
      await refreshRun({ silent: true });
    });
  },
  restoreIssues(el) {
    safeAction(async () => {
      const idxs = issueIndices(el);
      const chId = el.dataset.id;
      if (!idxs.length) return;
      const findings = await LyreAPI.restoreIssues(state.runId, chId, idxs);
      state.wsFindings = findings;
      await refreshRun({ silent: true });
    });
  },
  fixIssueGroup(el) {
    safeAction(async () => {
      const idxs = issueIndices(el);
      const chId = el.dataset.id;
      if (!idxs.length) return;
      // One edit_chapter run for the whole group, not one per occurrence.
      await LyreAPI.fixIssueGroup(state.runId, chId, idxs);
      await refreshRun({ silent: true });
    });
  },
  setWsMode(el) { state.wsMode = el.dataset.mode; rerender(); },
  refreshRender() {
    // Clearing the key is all that's needed — ensureRenderLoaded refetches
    // for any chapter whose key doesn't match.
    state.wsRenderKey = null;
    state.wsRenderStatus = "idle";
    rerender();
  },
  onRefineInput(el) { state.wsRefine = el.value; },
  toggleWrap() {
    state.wsWrap = !state.wsWrap;
    localStorage.setItem("lyretext.wrap", state.wsWrap ? "1" : "0");
    rerender();
  },
  copyPane(el) {
    const text = el.dataset.target === "source" ? state.wsSourceContent : state.wsPtxContent;
    if (text == null) return;
    navigator.clipboard.writeText(text)
      .then(() => toast(`Copied ${lineCount(text).toLocaleString()} lines`, "success"))
      .catch(() => toast("Could not access the clipboard", "error"));
  },
  jumpToLine(el) {
    const line = parseInt(el.dataset.line, 10);
    if (!Number.isFinite(line)) return;
    // Findings are reported against the PreTeXt output, which is the right
    // pane in translation mode and the left one in render mode — either way
    // it is the pane whose target is "output", so no mode switch is needed.
    state.wsJumpTo = { target: "output", line };
    rerender();
  },
  startEdit(el) {
    const target = el.dataset.target;
    state.wsEditTarget = target;
    state.wsEditContent = target === "source" ? (state.wsSourceContent || "") : (state.wsPtxContent || "");
    state.wsEditLine = 1; state.wsEditCol = 1;
    // Findings are reported against the PreTeXt output only, so the source
    // editor has nothing to carry across.
    state.wsEditMarkers = target === "output" ? findingMarkers() : null;
    state.wsEditAnchors = target === "output" ? editMarkerAnchors(state.wsEditContent, state.wsEditMarkers) : null;
    rerender();
    document.getElementById("wsEditArea")?.focus();
  },
  cancelEdit() {
    state.wsEditTarget = null;
    state.wsEditMarkers = null; state.wsEditAnchors = null;
    rerender();
  },
  onWsEditInput(el) { state.wsEditContent = el.value; syncEditorGutter(); },
  saveEdit(el) { saveChapterEdit(el.dataset.id, el.dataset.target); },

  // -- jobs ---------------------------------------------------------------
  toggleJob(el) {
    const key = el.dataset.key;
    if (state.jobsOpen.has(key)) state.jobsOpen.delete(key); else state.jobsOpen.add(key);
    rerender();
  },

  // -- output ---------------------------------------------------------------
  selectOutput(el) { state.outputSelected = el.dataset.file; state.outputContent = null; rerender(); },
  setProjectRoot(el) { state.projectRoot = el.value; rerender(); },
  copyOutput() {
    const text = state.outputContent?.text;
    if (text == null) return;
    navigator.clipboard.writeText(text)
      .then(() => toast(`Copied ${lineCount(text).toLocaleString()} lines`, "success"))
      .catch(() => toast("Could not access the clipboard", "error"));
  },

  // -- settings -------------------------------------------------------------
  settingsField(el) {
    const path = el.dataset.field.split(".");
    const value = el.type === "checkbox" ? el.checked : el.value;
    if (path[0] === "global") {
      state.settingsDraft.global_options[path[1]] = value;
    } else if (path[0] === "node") {
      const node = path[1], field = path[2];
      state.settingsDraft.node_overrides[node] = state.settingsDraft.node_overrides[node] || {};
      state.settingsDraft.node_overrides[node][field] = value === "" ? null : value;
    }
  },
  toggleNodeOverride(el) {
    const node = el.dataset.node;
    if (el.checked) state.settingsDraft.node_overrides[node] = state.settingsDraft.node_overrides[node] || { provider: null, llm_model: null };
    else state.settingsDraft.node_overrides[node] = null;
    rerender();
  },
  saveSettings() {
    safeAction(async () => {
      await LyreAPI.updateSettings(state.settingsDraft);
      state.settings = JSON.parse(JSON.stringify(state.settingsDraft));
      toast("Settings saved — effective on next run", "success");
    });
  },
  resetSettings() {
    safeAction(async () => {
      const patch = {
        global_options: { provider: "gemini", llm_model: null, pipeline: "rmd", apply_mode: "auto_apply", create_backup: false },
        node_overrides: Object.fromEntries(SETTINGS_NODES.map((n) => [n, null])),
      };
      await LyreAPI.updateSettings(patch);
      state.settingsDraft = JSON.parse(JSON.stringify(patch));
      toast("Settings reset to defaults");
    });
  },
};

// Shared by the Save button and the Ctrl/Cmd+S shortcut.
//
// wsSaving is cleared in a finally, not after the await: it gates the Save
// button *and* the early return below, so a failed request that left it set
// wedged the editor permanently — the button stayed on its spinner and every
// later attempt to save returned immediately without doing anything.
function saveChapterEdit(chapterId, target) {
  if (state.wsSaving) return;
  safeAction(async () => {
    state.wsSaving = true; rerender();
    try {
      const result = await LyreAPI.writeChapterFile(state.runId, chapterId, target, state.wsEditContent);
      // The write is done by the time this returns; the recompile/revalidate
      // it triggered runs on the server in the background, and the poll below
      // picks the chapter up as running.
      state.wsEditTarget = null;
      state.wsContentChapter = null; // force refetch
      state.wsFindingsChapter = null; state.wsFixQueue.clear();
      toast(result.triggered ? `Saved — ${result.triggered} running…` : "Saved", "success");
      await refreshRun({ silent: true });
    } finally {
      state.wsSaving = false;
    }
  });
  // On failure safeAction toasts the message and wsEditTarget is left set, so
  // the editor stays open with the user's text rather than discarding it.
}

function resetWsPaneState() {
  state.wsFindingsChapter = null; state.wsFixQueue.clear(); state.wsFindings = undefined;
  state.wsContentChapter = null; state.wsSourceContent = null; state.wsPtxContent = null;
  state.wsEditTarget = null; state.wsRefine = ""; state.wsJumpTo = null;
  state.wsEditMarkers = null; state.wsEditAnchors = null;
  state.wsLastExecuting = false;
  state.wsRender = null; state.wsRenderKey = null; state.wsRenderStatus = "idle";
}

async function waitForFirstGate() {
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const view = await LyreAPI.getRun(state.runId);
      state.view = view;
      if (view.gate?.read_pending || (view.chapters || []).length) {
        state.tab = "manifest";
        return;
      }
    } catch { /* not ready yet */ }
  }
  toast("Still processing — check back in a moment", "info");
  state.tab = "manifest";
}

async function settleAfterGate() {
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 800));
    await refreshRun({ silent: true });
    if (!state.view?.gate?.read_pending) break;
  }
  if (!state.view?.gate?.read_pending) {
    state.tab = "chapters";
    state.manifestSelected = new Set();
  }
  rerender();
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const handler = ACTIONS[el.dataset.action];
  if (!handler) return;
  // Checkboxes/selects/text inputs report through the "change"/"input"
  // listeners below — handling them here too would double-fire (a checkbox
  // click always also emits "change").
  if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
  e.preventDefault();
  handler(el, e);
});

document.addEventListener("change", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const handler = ACTIONS[el.dataset.action];
  if (!handler) return;
  handler(el, e);
});

document.addEventListener("input", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const handler = ACTIONS[el.dataset.action];
  if (!handler) return;
  const live = ["onSearch", "onRemapInput", "onRefineInput", "onWsEditInput", "editManifestField"];
  if (live.includes(el.dataset.action)) handler(el, e);
});

// Editor plumbing. All three are delegated from `document` and re-find the
// textarea by id each time, because renderApp() replaces the whole DOM — a
// listener bound directly to the element would be discarded on the next poll.

// scroll does not bubble, so this listens in the capture phase.
document.addEventListener("scroll", (e) => {
  const el = e.target;
  if (el && el.id === "wsEditArea") {
    const gutter = document.getElementById("wsEditGutter");
    if (gutter) gutter.scrollTop = el.scrollTop;
  }
}, true);

// Caret moves that aren't keystrokes (clicks, arrows, selection) still need to
// update "Ln x, Col y" and the active gutter row.
document.addEventListener("selectionchange", () => {
  if (document.activeElement?.id === "wsEditArea") syncEditorGutter();
});

document.addEventListener("keydown", (e) => {
  const area = e.target;
  if (!area || area.id !== "wsEditArea") return;

  // Tab indents instead of leaving the editor — mandatory when hand-editing
  // nested PreTeXt. Shift+Tab still moves focus out, so keyboard-only users
  // are never trapped.
  if (e.key === "Tab" && !e.shiftKey) {
    e.preventDefault();
    const { selectionStart: s, selectionEnd: t, value } = area;
    area.value = value.slice(0, s) + "  " + value.slice(t);
    area.selectionStart = area.selectionEnd = s + 2;
    state.wsEditContent = area.value;
    syncEditorGutter();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (state.wsChapterId && state.wsEditTarget) saveChapterEdit(state.wsChapterId, state.wsEditTarget);
    return;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    ACTIONS.cancelEdit();
  }
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function loadRunFromUrl(runId) {
  state.runId = runId;
  resetRunScopedState();
  try {
    await refreshRun({ silent: true });
    if (state.view) {
      state.tab = state.view.gate?.read_pending ? "manifest" : "chapters";
      startPolling();
      return;
    }
  } catch { /* fall through */ }
  // The run in the URL no longer exists (old checkpoint, wrong server, …) —
  // drop it rather than get stuck on a dead run.
  state.runId = null;
  syncUrlToRun();
  toast(`Run ${runId} could not be loaded`, "error");
}

window.addEventListener("popstate", () => {
  const runId = new URLSearchParams(location.search).get("run");
  if (runId !== state.runId) {
    if (runId) loadRunFromUrl(runId).then(rerender);
    else { stopPolling(); state.runId = null; state.view = null; state.tab = "newrun"; rerender(); }
  }
});

(async function boot() {
  try { state.settings = await LyreAPI.getSettings(); } catch { /* settings load lazily on the tab too */ }
  const runId = new URLSearchParams(location.search).get("run");
  if (runId) await loadRunFromUrl(runId);
  renderApp();
})();
