#!/usr/bin/env node
// Installed-app backend smoke test (#71), run by CI after a silent install.
//
// Runs under the installed app's own Electron as plain Node, and drives the
// backend through the app's own backend-process.js loaded from the installed
// app.asar, so it exercises exactly what the app does at launch: the packaged
// backend exe, the bundled toolchain's environment, the auth token, and the
// process-tree kill on stop.
//
//   set ELECTRON_RUN_AS_NODE=1
//   "<install dir>\LyreTeXt.exe" smoke-backend.js --resources "<install dir>\resources" --data <scratch dir>
//
// Checks, in order: the backend becomes healthy in mock mode; /api/environment
// reports pandoc and the pretext.lua writer as bundled and working; a mock run
// reaches the manifest (read) gate; stop() leaves no backend process behind.
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawnSync } = require('child_process');

function arg(name) {
  const i = process.argv.indexOf(name);
  if (i === -1 || !process.argv[i + 1]) throw new Error(`missing ${name}`);
  return process.argv[i + 1];
}

const resources = path.resolve(arg('--resources'));
const dataRoot = path.resolve(arg('--data'));
const { Backend } = require(path.join(resources, 'app.asar', 'backend-process.js'));

const authToken = crypto.randomBytes(32).toString('hex');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function request(baseUrl, method, urlPath, payload) {
  return new Promise((resolve, reject) => {
    const data = payload === undefined ? null : Buffer.from(JSON.stringify(payload), 'utf8');
    const headers = { 'X-LyreTeXt-Token': authToken };
    if (data) Object.assign(headers, { 'Content-Type': 'application/json', 'Content-Length': data.length });
    const req = http.request(baseUrl + urlPath, { method, headers }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let body = text;
        try { body = JSON.parse(text); } catch { /* not JSON */ }
        resolve({ status: res.statusCode, body });
      });
    });
    req.setTimeout(60000, () => req.destroy(new Error(`${method} ${urlPath} timed out`)));
    req.on('error', reject);
    req.end(data || undefined);
  });
}

function check(cond, message) {
  if (!cond) throw new Error(`FAIL: ${message}`);
  console.log(`ok   ${message}`);
}

function backendProcesses() {
  const r = spawnSync('tasklist', ['/FI', 'IMAGENAME eq lyretext-backend.exe', '/FO', 'CSV', '/NH'],
    { encoding: 'utf8', windowsHide: true });
  return (r.stdout || '').split(/\r?\n/).filter((l) => l.toLowerCase().includes('lyretext-backend.exe'));
}

async function main() {
  process.env.LYRETEXT_MOCK = '1';
  fs.mkdirSync(dataRoot, { recursive: true });
  const dataDir = path.join(dataRoot, 'data');
  const backend = new Backend({
    isPackaged: true,
    resourcesPath: resources,
    mode: 'packaged',
    dataDir,
    logFile: path.join(dataRoot, 'backend.log'),
    authToken,
  });

  let failed = null;
  try {
    const t0 = Date.now();
    const baseUrl = await backend.start({ keys: {} });
    console.log(`backend healthy at ${baseUrl} after ${((Date.now() - t0) / 1000).toFixed(1)} s`);

    const health = await request(baseUrl, 'GET', '/api/health');
    check(health.status === 200 && health.body.status === 'ok', `GET /api/health -> 200 (version ${health.body.version})`);

    const env = await request(baseUrl, 'GET', '/api/environment');
    check(env.status === 200, 'GET /api/environment -> 200 with the token');
    const { pandoc, writer } = env.body;
    console.log(`     pandoc: ${JSON.stringify(pandoc)}`);
    console.log(`     writer: ${JSON.stringify(writer)}`);
    check(pandoc && pandoc.ok && pandoc.source === 'bundled', `pandoc is bundled and working (${pandoc && pandoc.version})`);
    check(writer && writer.ok && writer.source === 'bundled', 'pretext.lua writer is bundled and converts');
    check(env.body.desktop === true, 'backend reports desktop (token) mode');

    const source = path.join(dataDir, 'smoke-source');
    fs.mkdirSync(source, { recursive: true });
    fs.writeFileSync(path.join(source, 'index.Rmd'), '# Smoke\n\nMock runs ignore this.\n');
    const created = await request(baseUrl, 'POST', '/api/runs', { source, output_dir: 'output', temp_dir: 'temp' });
    check(created.status === 202 && created.body.run_id, `POST /api/runs -> 202 (run ${created.body.run_id})`);

    const runId = created.body.run_id;
    let summary = null;
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const runs = await request(baseUrl, 'GET', '/api/runs');
      summary = (runs.body.runs || []).find((r) => r.run_id === runId) || null;
      if (summary && summary.status === 'read_gate') break;
      await sleep(1000);
    }
    console.log(`     run summary: ${JSON.stringify(summary)}`);
    check(summary && summary.status === 'read_gate', 'mock run reached the manifest gate');
    check(summary.chapters_total === 7, `manifest lists the fixture's ${summary.chapters_total} chapters`);
  } catch (err) {
    failed = err;
  } finally {
    const pid = backend.child && backend.child.pid;
    backend.stop();
    if (backend.child && backend.child.exitCode === null) {
      await Promise.race([new Promise((r) => backend.child.once('exit', r)), sleep(15000)]);
    }
    await sleep(1000);
    const left = backendProcesses();
    if (left.length) {
      console.error(left.join('\n'));
      failed = failed || new Error(`FAIL: backend processes remain after stop() (pid was ${pid})`);
    } else {
      console.log(`ok   stop() left no lyretext-backend.exe running (pid was ${pid})`);
    }
  }

  if (failed) {
    console.error(failed.stack || String(failed));
    const log = path.join(dataRoot, 'backend.log');
    if (fs.existsSync(log)) console.error(`--- backend.log ---\n${fs.readFileSync(log, 'utf8').slice(-8000)}`);
    process.exit(1);
  }
  console.log('backend smoke test passed');
}

main().catch((err) => { console.error(err); process.exit(1); });
