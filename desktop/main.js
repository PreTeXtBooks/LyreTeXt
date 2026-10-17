// LyreTeXt desktop shell — Electron main process (Phase 0 spike, #68).
//
// Starts the Python backend as a sidecar on a free 127.0.0.1 port, waits for
// it to answer, opens the existing web UI in a window, and makes sure the
// backend dies with the app.
//
// API keys (#67, decision #84): the user's keys are stored encrypted by
// keys.js and handed to the backend in its environment. The page manages them
// only through the preload bridge (set / remove / test, and set-or-unset
// status), never seeing a stored value. A random token generated each launch
// protects the backend's local API: the backend gets it as
// LYRETEXT_AUTH_TOKEN, the window carries it as an HttpOnly SameSite=Strict
// cookie, and this process sends it as a header.
'use strict';

const { app, BrowserWindow, dialog, ipcMain, safeStorage, session, shell } = require('electron');
const crypto = require('crypto');
const path = require('path');
const { Backend, postJson } = require('./backend-process');
const { KeyStore } = require('./keys');

const mode = process.argv.includes('--backend=frozen') ? 'frozen' : 'dev';

if (!app.requestSingleInstanceLock()) {
  // A second launch hands focus to the first (see 'second-instance') and exits
  // before starting a second backend.
  app.quit();
} else {
  let mainWindow = null;
  // The backend's writable data dir (--data-dir): config.yml, the runs
  // database, output/ and temp/. %APPDATA%\LyreTeXt\data on Windows.
  const dataDir = path.join(app.getPath('userData'), 'data');
  // Per-launch secret for the backend's local API; never written to disk by us.
  const authToken = crypto.randomBytes(32).toString('hex');
  const backend = new Backend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    mode,
    dataDir,
    logFile: path.join(app.getPath('userData'), 'backend.log'),
    authToken,
  });

  // Every exit path funnels into backend.stop(): normal quit, last window
  // closed, uncaught exception in this process, and the process 'exit' event
  // as a final net. A hard kill of Electron itself skips all of these; the
  // backend's stdin watchdog (backend/entry.py) covers that case.
  app.on('will-quit', () => backend.stop());
  process.on('exit', () => backend.stop());
  process.on('uncaughtException', (err) => {
    console.error(err);
    backend.stop();
    app.exit(1);
  });

  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.on('window-all-closed', () => app.quit());

  app.whenReady().then(async () => {
    // safeStorage is only usable once the app is ready.
    const keyStore = new KeyStore({ userDataDir: app.getPath('userData'), safeStorage });
    let baseUrl;
    try {
      baseUrl = await backend.start({ keys: keyStore.env() });
    } catch (err) {
      backend.stop();
      dialog.showErrorBox('LyreTeXt could not start',
        `The LyreTeXt backend failed to start:\n\n${err.message}\n\nLog: ${backend.logFile}`);
      app.exit(1);
      return;
    }

    backend.child.on('exit', (code) => {
      if (backend.stopping) return;
      dialog.showErrorBox('LyreTeXt stopped',
        `The LyreTeXt backend exited unexpectedly (code ${code}).\n\nLog: ${backend.logFile}`);
      app.quit();
    });

    // Only the local backend's origin loads in the app. Other http(s) links
    // open in the user's browser; any other scheme (file:, javascript:, ...)
    // is dropped. Compare whole origins: a prefix test would let port 5000
    // match http://127.0.0.1:50001.
    const isAppUrl = (url) => {
      try { return new URL(url).origin === baseUrl; } catch { return false; }
    };

    registerKeyHandlers({ keyStore, baseUrl, authToken, isAppUrl });

    // The window's credential for the local API. HttpOnly keeps it out of the
    // page's reach; SameSite=Strict keeps other sites from riding on it. No
    // expiry, so it is a session cookie that dies with the app.
    await session.defaultSession.cookies.set({
      url: baseUrl,
      name: 'lyretext_token',
      value: authToken,
      httpOnly: true,
      sameSite: 'strict',
      path: '/',
    });

    mainWindow = new BrowserWindow({
      width: 1400,
      height: 900,
      title: 'LyreTeXt',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: path.join(__dirname, 'preload.js'),
      },
    });
    const openExternalSafe = (url) => {
      let protocol;
      try { protocol = new URL(url).protocol; } catch { return; }
      if (protocol === 'http:' || protocol === 'https:') shell.openExternal(url);
    };
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (isAppUrl(url)) return { action: 'allow' };
      openExternalSafe(url);
      return { action: 'deny' };
    });
    mainWindow.webContents.on('will-navigate', (event, url) => {
      if (isAppUrl(url)) return;
      event.preventDefault();
      openExternalSafe(url);
    });
    mainWindow.on('closed', () => { mainWindow = null; });
    await mainWindow.loadURL(`${baseUrl}/ui/`);
  });
}

// IPC behind window.lyretextDesktop (preload.js). Each handler answers
// { ok: true, value } or { ok: false, error }, and only for the app's own page.
function registerKeyHandlers({ keyStore, baseUrl, authToken, isAppUrl }) {
  const handle = (channel, fn) => ipcMain.handle(channel, async (event, ...args) => {
    if (!isAppUrl(event.senderFrame && event.senderFrame.url)) {
      return { ok: false, error: 'Not allowed' };
    }
    try {
      return { ok: true, value: await fn(...args) };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // Apply a change to the running backend, so it takes effect without a
  // restart. The key is already stored, so on failure it still applies at the
  // next launch; say so rather than report the save as lost.
  const pushToBackend = async (keys) => {
    let res;
    try {
      res = await postJson(baseUrl, '/api/internal/keys', { keys }, authToken);
    } catch (err) {
      throw new Error(`Saved, but the running backend could not be updated (${err.message}). It will apply after a restart.`);
    }
    if (res.status !== 200) {
      throw new Error(`Saved, but the running backend could not be updated (HTTP ${res.status}). It will apply after a restart.`);
    }
  };

  handle('keys:list', () => keyStore.list());
  handle('keys:set', async (provider, key) => {
    const value = keyStore.set(provider, key);
    await pushToBackend({ [provider]: value });
    return keyStore.list();
  });
  handle('keys:delete', async (provider) => {
    keyStore.delete(provider);
    await pushToBackend({ [provider]: null });
    return keyStore.list();
  });
  handle('keys:test', async (provider) => {
    KeyStore.checkProvider(provider);
    const res = await postJson(baseUrl, '/api/keys/verify', { provider }, authToken, 60000);
    if (res.status !== 200 || !res.body || typeof res.body !== 'object') {
      throw new Error(`Key test failed (HTTP ${res.status})`);
    }
    return { ok: Boolean(res.body.ok), detail: String(res.body.detail || '') };
  });
}
