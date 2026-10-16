// LyreTeXt desktop shell — Electron main process (Phase 0 spike, #68).
//
// Starts the Python backend as a sidecar on a free 127.0.0.1 port, waits for
// it to answer, opens the existing web UI in a window, and makes sure the
// backend dies with the app.
'use strict';

const { app, BrowserWindow, dialog, shell } = require('electron');
const path = require('path');
const { Backend } = require('./backend-process');

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
  const backend = new Backend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    mode,
    dataDir,
    logFile: path.join(app.getPath('userData'), 'backend.log'),
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
    let baseUrl;
    try {
      baseUrl = await backend.start();
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

    mainWindow = new BrowserWindow({
      width: 1400,
      height: 900,
      title: 'LyreTeXt',
      webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
    });
    // Only the local backend's origin loads in the app. Other http(s) links
    // open in the user's browser; any other scheme (file:, javascript:, ...)
    // is dropped. Compare whole origins: a prefix test would let port 5000
    // match http://127.0.0.1:50001.
    const isAppUrl = (url) => {
      try { return new URL(url).origin === baseUrl; } catch { return false; }
    };
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
