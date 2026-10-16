// Backend sidecar lifecycle: pick a port, spawn the LyreTeXt backend bound to
// 127.0.0.1, wait for it to answer, and kill its whole process tree on exit.
'use strict';

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const HOST = '127.0.0.1';
const HEALTH_PATH = '/api/settings';

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

class Backend {
  constructor({ isPackaged, resourcesPath, mode, workDir, logFile }) {
    this.opts = { isPackaged, resourcesPath, mode };
    this.workDir = workDir;
    this.logFile = logFile;
    this.child = null;
    this.port = null;
    this.stopping = false;
  }

  get baseUrl() {
    return `http://${HOST}:${this.port}`;
  }

  async start() {
    this.port = await getFreePort();
    const { cmd, args } = resolveBackendCommand(this.opts);
    fs.mkdirSync(this.workDir, { recursive: true });
    const log = fs.openSync(this.logFile, 'a');
    fs.writeSync(log, `\n--- ${new Date().toISOString()} starting ${cmd} ${args.join(' ')} on port ${this.port}\n`);

    this.child = spawn(cmd, [...args, '--host', HOST, '--port', String(this.port)], {
      // The backend writes lyretext_runs.db, output/ and temp/ relative to its
      // cwd, so give it a writable per-user directory rather than the install dir.
      cwd: this.workDir,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
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

module.exports = { Backend };
