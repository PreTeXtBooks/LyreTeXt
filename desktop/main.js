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

  // Set once a quit is under way (including the user closing the splash during
  // startup), so a startup still in flight neither reports the backend it just
  // lost as a failure nor opens a window.
  let quitting = false;
  app.on('before-quit', () => { quitting = true; });

  // "Starting LyreTeXt…" while the backend comes up (#71): a fresh install's
  // first launch can take ~30 s while Defender scans the new exe.
  let splash = null;
  const closeSplash = () => {
    if (splash && !splash.isDestroyed()) splash.destroy();
    splash = null;
  };

  app.on('second-instance', () => {
    const win = mainWindow || splash;
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  // Closing the splash during startup lands here too: it quits, and will-quit
  // stops the half-started backend.
  app.on('window-all-closed', () => app.quit());

  app.whenReady().then(async () => {
    splash = createSplash();
    // safeStorage is only usable once the app is ready.
    const keyStore = new KeyStore({ userDataDir: app.getPath('userData'), safeStorage });
    let baseUrl;
    try {
      baseUrl = await backend.start({ keys: keyStore.env() });
    } catch (err) {
      backend.stop();
      if (quitting) return;
      closeSplash();
      dialog.showErrorBox('LyreTeXt could not start',
        `The LyreTeXt backend failed to start:\n\n${err.message}\n\nLog: ${backend.logFile}`);
      app.exit(1);
      return;
    }
    if (quitting) return;

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
    if (quitting) return;

    // Created hidden and shown once the UI has loaded; the splash stays up
    // until then. The splash is closed only after this window exists, so
    // there is never a moment with no window (which would quit the app).
    mainWindow = new BrowserWindow({
      width: 1400,
      height: 900,
      title: 'LyreTeXt',
      show: false,
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
    try {
      await mainWindow.loadURL(`${baseUrl}/ui/`);
    } finally {
      // Shown even if the load failed, so the user sees the error page rather
      // than a splash that never goes away.
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.show();
      closeSplash();
    }
  });
}

// A small frameless window over nothing but the local splash.html: no preload,
// sandboxed, and no navigation or pop-ups.
function createSplash() {
  const win = new BrowserWindow({
    width: 380,
    height: 260,
    frame: false,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    center: true,
    show: false,
    title: 'Starting LyreTeXt',
    backgroundColor: '#fcfcfc',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  win.once('ready-to-show', () => { if (!win.isDestroyed()) win.show(); });
  win.loadFile(path.join(__dirname, 'splash.html')).catch(() => { /* cosmetic only */ });
  return win;
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
