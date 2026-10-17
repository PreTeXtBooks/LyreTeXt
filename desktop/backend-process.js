// Backend sidecar lifecycle: pick a port, spawn the LyreTeXt backend bound to
// 127.0.0.1, wait for it to answer, and kill its whole process tree on exit.
'use strict';

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const HOST = '127.0.0.1';
// Answers 200 as soon as the server is up; unlike /api/settings it never
// parses config.yml, so a broken config can't masquerade as a failed start.
const HEALTH_PATH = '/api/health';

// Ask the OS for a free port by binding to port 0. There is a small window
// between closing this probe and the backend binding the port; acceptable for
// a single local app, and the health check fails loudly if we lose the race.
function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, HOST, () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// How to launch the backend. Three modes:
//   packaged  — the frozen exe shipped in resources/backend (electron-builder)
//   frozen    — desktop/dist/lyretext-backend from a local PyInstaller build
//   dev       — the project venv's python running backend/entry.py
function resolveBackendCommand({ isPackaged, resourcesPath, mode }) {
  const exeName = process.platform === 'win32' ? 'lyretext-backend.exe' : 'lyretext-backend';
  if (isPackaged) {
    return { cmd: path.join(resourcesPath, 'backend', exeName), args: [] };
  }
  if (mode === 'frozen') {
    return { cmd: path.join(__dirname, 'dist', 'lyretext-backend', exeName), args: [] };
  }
  const repoRoot = path.resolve(__dirname, '..');
  return {
    cmd: process.env.LYRETEXT_PYTHON || findVenvPython(repoRoot),
    args: [path.join(__dirname, 'backend', 'entry.py')],
  };
}

// Walk up from the repo root looking for a .venv (a git worktree under
// .claude/worktrees/ shares the main checkout's venv).
function findVenvPython(start) {
  const rel = process.platform === 'win32'
    ? path.join('.venv', 'Scripts', 'python.exe')
    : path.join('.venv', 'bin', 'python');
  let dir = start;
  for (;;) {
    const candidate = path.join(dir, rel);
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) return process.platform === 'win32' ? 'python.exe' : 'python3';
    dir = parent;
  }
}

function httpGetStatus(url, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode);
    });
    req.setTimeout(timeoutMs, () => req.destroy());
    req.on('error', () => resolve(null));
  });
}

async function waitForHealthy(baseUrl, child, { timeoutMs = 60000, intervalMs = 250 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`backend exited during startup (code ${child.exitCode})`);
    }
    if ((await httpGetStatus(baseUrl + HEALTH_PATH, 2000)) === 200) return;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`backend did not become healthy within ${timeoutMs / 1000}s`);
}

// The backend's environment (decision #84): the parent's, minus every
// *_API_KEY variable, plus the app's stored keys and the per-launch auth token.
// Only keys the user entered in the app reach the backend — never one that
// happens to be set in the shell or system environment Electron started from.
function buildBackendEnv(parentEnv, { keys = {}, authToken }) {
  const env = { ...parentEnv };
  for (const name of Object.keys(env)) {
    if (/_API_KEY$/i.test(name)) delete env[name];
  }
  return {
    ...env,
    ...keys,
    LYRETEXT_AUTH_TOKEN: authToken,
    PYTHONUNBUFFERED: '1',
    PYTHONIOENCODING: 'utf-8',
  };
}

class Backend {
  constructor({ isPackaged, resourcesPath, mode, dataDir, logFile, authToken }) {
    this.opts = { isPackaged, resourcesPath, mode };
    this.dataDir = dataDir;
    this.logFile = logFile;
    this.authToken = authToken;
    this.child = null;
    this.port = null;
    this.stopping = false;
  }

  get baseUrl() {
    return `http://${HOST}:${this.port}`;
  }

  // keys: { GEMINI_API_KEY: ..., ... } from the key store, read at launch.
  async start({ keys = {} } = {}) {
    this.port = await getFreePort();
    const { cmd, args } = resolveBackendCommand(this.opts);
    fs.mkdirSync(this.dataDir, { recursive: true });
    const log = fs.openSync(this.logFile, 'a');
    fs.writeSync(log, `\n--- ${new Date().toISOString()} starting ${cmd} ${args.join(' ')} on port ${this.port}, data dir ${this.dataDir}\n`);

    const backendArgs = [
      ...args,
      '--host', HOST,
      '--port', String(this.port),
      // Everything the backend writes (config.yml, lyretext_runs.db, output/,
      // temp/) goes here, never into the read-only, replaced-on-upgrade
      // install dir. A missing config.yml is seeded from the bundled default.
      '--data-dir', this.dataDir,
    ];
    this.child = spawn(cmd, backendArgs, {
      // The backend itself no longer depends on its cwd, but the tools it runs
      // inherit it (knitr writes figures relative to R's working directory),
      // so start it somewhere writable rather than wherever Electron was
      // launched from.
      cwd: this.dataDir,
      env: buildBackendEnv(process.env, { keys, authToken: this.authToken }),
      // stdin is a pipe the backend's watchdog reads: if this process dies
      // without running its exit handlers, the pipe closes and the backend exits.
      stdio: ['pipe', log, log],
      windowsHide: true,
    });
    fs.closeSync(log);
    this.child.on('error', (err) => { this.spawnError = err; });

    await waitForHealthy(this.baseUrl, this.child);
    return this.baseUrl;
  }

  // Synchronous on purpose: it runs from will-quit and process 'exit', where
  // async work would never complete.
  stop() {
    if (!this.child || this.child.exitCode !== null || this.stopping) return;
    this.stopping = true;
    const pid = this.child.pid;
    if (process.platform === 'win32') {
      // /T kills the whole tree (pandoc, Rscript, ...), /F because a console
      // app with no window ignores a polite WM_CLOSE.
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true });
    } else {
      try { process.kill(pid, 'SIGTERM'); } catch { /* already gone */ }
    }
  }
}

// POST JSON to the backend with the auth token header. Resolves
// { status, body }; used by the main process for the internal key endpoint and
// key tests, so key material never passes through the page.
function postJson(baseUrl, urlPath, payload, authToken, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(payload), 'utf8');
    const req = http.request(baseUrl + urlPath, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': data.length,
        'X-LyreTeXt-Token': authToken,
      },
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let body = null;
        try { body = JSON.parse(text); } catch { body = text; }
        resolve({ status: res.statusCode, body });
      });
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error('backend request timed out')));
    req.on('error', reject);
    req.end(data);
  });
}

module.exports = { Backend, buildBackendEnv, postJson };
