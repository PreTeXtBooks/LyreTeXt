// Preload for the app window (#67, decision #84). Runs sandboxed with context
// isolation; exposes only a narrow key-management bridge to the page. The page
// can save, remove and test keys, and see which providers have one — it can
// never read a stored key back.
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// Main-process handlers answer { ok, value } or { ok: false, error }, so the
// page gets a clean message rather than Electron's "Error invoking remote
// method ..." wrapper.
async function call(channel, ...args) {
  const res = await ipcRenderer.invoke(channel, ...args);
  if (!res || !res.ok) throw new Error((res && res.error) || 'Request failed');
  return res.value;
}

contextBridge.exposeInMainWorld('lyretextDesktop', {
  isDesktop: true,
  listKeys: () => call('keys:list'),
  setKey: (provider, key) => call('keys:set', provider, key),
  deleteKey: (provider) => call('keys:delete', provider),
  testKey: (provider) => call('keys:test', provider),
});
