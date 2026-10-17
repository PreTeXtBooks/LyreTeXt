// API-key storage for the desktop app (#67, decision #84).
//
// Each provider's key is encrypted with Electron's safeStorage (DPAPI on
// Windows, tied to the user's login) and kept as a base64 blob in
// userData/keys.json. Plaintext keys exist only in this process's memory while
// being stored or handed to the backend, and in the backend's environment.
// If safeStorage can't encrypt, keys are refused rather than written in clear.
'use strict';

const fs = require('fs');
const path = require('path');

// Provider -> the env var the backend (inferenceswitch) reads its key from.
const PROVIDERS = {
  gemini: 'GEMINI_API_KEY',
  anthropic: 'ANTHROPIC_API_KEY',
};

class KeyStore {
  constructor({ userDataDir, safeStorage }) {
    this.file = path.join(userDataDir, 'keys.json');
    this.safeStorage = safeStorage;
  }

  // { provider: base64 ciphertext }. A missing or unreadable file is empty: the
  // user re-enters keys rather than the app failing to start.
  _read() {
    try {
      const data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      return data && typeof data.keys === 'object' && data.keys ? data.keys : {};
    } catch {
      return {};
    }
  }

  // Write-then-rename, so a crash mid-write never leaves a truncated file.
  _write(keys) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ version: 1, keys }, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, this.file);
  }

  static checkProvider(provider) {
    if (!Object.prototype.hasOwnProperty.call(PROVIDERS, provider)) {
      throw new Error(`Unknown provider: ${String(provider)}`);
    }
  }

  // Provider names with set/unset status only — never a value.
  list() {
    const keys = this._read();
    return Object.keys(PROVIDERS).map((provider) => ({ provider, set: Boolean(keys[provider]) }));
  }

  set(provider, key) {
    KeyStore.checkProvider(provider);
    const value = typeof key === 'string' ? key.trim() : '';
    if (!value) throw new Error('Enter a key to save.');
    if (!this.safeStorage.isEncryptionAvailable()) {
      throw new Error('Secure storage is not available on this system, so the key was not saved.');
    }
    const keys = this._read();
    keys[provider] = this.safeStorage.encryptString(value).toString('base64');
    this._write(keys);
    return value;
  }

  delete(provider) {
    KeyStore.checkProvider(provider);
    const keys = this._read();
    delete keys[provider];
    this._write(keys);
  }

  // { GEMINI_API_KEY: plaintext, ... } for the backend's spawn environment.
  // A blob that no longer decrypts (e.g. copied from another Windows account)
  // is skipped; the user sees that provider as set and Test reports no key.
  env() {
    const out = {};
    if (!this.safeStorage.isEncryptionAvailable()) return out;
    const keys = this._read();
    for (const [provider, envVar] of Object.entries(PROVIDERS)) {
      if (!keys[provider]) continue;
      try {
        out[envVar] = this.safeStorage.decryptString(Buffer.from(keys[provider], 'base64'));
      } catch {
        console.error(`Stored ${provider} key could not be decrypted; ignoring it.`);
      }
    }
    return out;
  }
}

module.exports = { KeyStore, PROVIDERS };
