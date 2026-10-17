#!/usr/bin/env node
// Stage the external toolchain the desktop app ships (#70): pandoc and Oscar
// Levin's pretext.lua writer, with their licences, into dist/toolchain, which
// electron-builder copies to resources/toolchain (package.json extraResources).
//
// Sources are configuration, so CI (#71) can hand in a pandoc it downloaded:
//
//   --pandoc <pandoc.exe>    or LYRETEXT_TOOLCHAIN_PANDOC      default: pandoc on PATH
//   --writer-dir <dir>       or LYRETEXT_TOOLCHAIN_WRITER_DIR  default: <pandoc user data dir>/custom
//   --out <dir>                                                default: dist/toolchain
//
// The writer dir must hold pretext.lua and pretext-environments.lua (which
// pretext.lua loads from its own folder); pretext-latex-reader.lua is copied
// when present. A LICENSE file in the writer dir (a checkout of the upstream
// repo has one) is shipped as the writer's licence; otherwise the vendored
// GPL-2.0 text in toolchain/licenses/ is. Likewise pandoc's COPYING.rtf /
// COPYRIGHT.txt are copied from beside pandoc.exe when present (the official
// Windows zip and installer both have them).
//
// Output layout:
//   toolchain.json               what was staged: versions, hashes, sources
//   NOTICE.txt                   third-party notice for the whole folder
//   pandoc/pandoc.exe, COPYRIGHT.txt, COPYING.rtf | GPL-2.0.txt, SOURCE.txt
//   pretext-writer/pretext.lua, pretext-environments.lua, [pretext-latex-reader.lua], LICENSE, SOURCE.txt
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const DESKTOP = path.resolve(__dirname, '..');
const PINS = JSON.parse(fs.readFileSync(path.join(DESKTOP, 'toolchain', 'toolchain.json'), 'utf8'));
const GPL2 = path.join(DESKTOP, 'toolchain', 'licenses', 'GPL-2.0.txt');
const IS_WIN = process.platform === 'win32';

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const m = /^--(pandoc|writer-dir|out)(?:=(.*))?$/.exec(argv[i]);
    if (!m) throw new Error(`Unknown argument ${argv[i]}`);
    out[m[1]] = m[2] !== undefined ? m[2] : argv[++i];
    if (!out[m[1]]) throw new Error(`--${m[1]} needs a value`);
  }
  return out;
}

function which(cmd) {
  const exts = IS_WIN ? (process.env.PATHEXT || '.EXE').split(';') : [''];
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, cmd + ext);
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    }
  }
  return null;
}

function fail(message) {
  console.error(`stage-toolchain: ${message}`);
  process.exit(1);
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

// git's blob id, so a staged writer file can be matched against upstream
// history (`git hash-object`).
function gitBlob(file) {
  const data = fs.readFileSync(file);
  return crypto.createHash('sha1').update(`blob ${data.length}\0`).update(data).digest('hex');
}

function versionAtLeast(version, min) {
  const a = version.split('.').map(Number);
  const b = min.split('.').map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] || 0) - (b[i] || 0);
    if (d) return d > 0;
  }
  return true;
}

function copy(src, destDir, name = path.basename(src)) {
  fs.copyFileSync(src, path.join(destDir, name));
  return path.join(destDir, name);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const out = path.resolve(DESKTOP, args.out || path.join('dist', 'toolchain'));

  // -- pandoc ---------------------------------------------------------------
  const pandocSrc = args.pandoc || process.env.LYRETEXT_TOOLCHAIN_PANDOC || which('pandoc');
  if (!pandocSrc || !fs.existsSync(pandocSrc)) {
    fail('pandoc not found. Pass --pandoc <path to pandoc.exe> or set LYRETEXT_TOOLCHAIN_PANDOC.');
  }
  const probe = spawnSync(pandocSrc, ['--version'], { encoding: 'utf8', timeout: 30000, windowsHide: true });
  if (probe.status !== 0) fail(`${pandocSrc} --version failed: ${probe.stderr || probe.error}`);
  const version = (/^pandoc(?:\.exe)?\s+(\d+(?:\.\d+)*)/im.exec(probe.stdout) || [])[1];
  if (!version) fail(`could not read a version from ${pandocSrc} --version`);
  if (!versionAtLeast(version, PINS.pandoc.minVersion)) {
    fail(`pandoc ${version} is older than the required ${PINS.pandoc.minVersion}`);
  }
  if (version !== PINS.pandoc.verifiedVersion) {
    console.warn(`stage-toolchain: WARNING pandoc ${version} differs from the verified ${PINS.pandoc.verifiedVersion}`);
  }
  const userDataDir = (/^User data directory:\s*(.+?)\s*$/m.exec(probe.stdout) || [])[1];

  // -- writer ---------------------------------------------------------------
  const writerDir = args['writer-dir'] || process.env.LYRETEXT_TOOLCHAIN_WRITER_DIR
    || (userDataDir && path.join(userDataDir, 'custom'));
  if (!writerDir || !fs.existsSync(writerDir)) {
    fail('pretext.lua writer dir not found. Pass --writer-dir <dir> or set LYRETEXT_TOOLCHAIN_WRITER_DIR.');
  }
  const pp = PINS.pandocPretext;
  for (const name of pp.required) {
    if (!fs.existsSync(path.join(writerDir, name))) fail(`${name} not found in ${writerDir}`);
  }

  // -- stage ----------------------------------------------------------------
  fs.rmSync(out, { recursive: true, force: true });
  const pandocOut = path.join(out, 'pandoc');
  const writerOut = path.join(out, 'pretext-writer');
  fs.mkdirSync(pandocOut, { recursive: true });
  fs.mkdirSync(writerOut, { recursive: true });

  const pandocExe = copy(pandocSrc, pandocOut, IS_WIN ? 'pandoc.exe' : 'pandoc');
  if (!IS_WIN) fs.chmodSync(pandocExe, 0o755);
  const pandocLicences = [];
  for (const name of ['COPYRIGHT.txt', 'COPYING.rtf', 'COPYRIGHT', 'COPYING.md']) {
    const src = path.join(path.dirname(pandocSrc), name);
    if (fs.existsSync(src)) { copy(src, pandocOut); pandocLicences.push(name); }
  }
  if (!pandocLicences.length) {
    copy(GPL2, pandocOut);
    pandocLicences.push('GPL-2.0.txt');
  }
  fs.writeFileSync(path.join(pandocOut, 'SOURCE.txt'), [
    `pandoc ${version}`,
    `Copyright (C) 2006-present John MacFarlane and contributors.`,
    `Licence: ${PINS.pandoc.license} (see ${pandocLicences.join(', ')}).`,
    `Homepage: ${PINS.pandoc.homepage}`,
    `Corresponding source for this version: ${PINS.pandoc.source}/tree/${version}`,
    `Release (source archives and binaries): ${PINS.pandoc.source}/releases/tag/${version}`,
    '',
  ].join('\n'));

  const writerFiles = {};
  let matchesPin = true;
  for (const name of [...pp.required, ...pp.optional]) {
    const src = path.join(writerDir, name);
    if (!fs.existsSync(src)) continue;
    const dest = copy(src, writerOut);
    const blob = gitBlob(dest);
    writerFiles[name] = { gitBlob: blob, sha256: sha256(dest) };
    if (pp.gitBlobs[name] && pp.gitBlobs[name] !== blob) {
      matchesPin = false;
      console.warn(`stage-toolchain: WARNING ${name} differs from ${pp.source} at ${pp.commit.slice(0, 12)}`);
    }
  }
  const upstreamLicence = path.join(writerDir, 'LICENSE');
  copy(fs.existsSync(upstreamLicence) ? upstreamLicence : GPL2, writerOut, 'LICENSE');
  fs.writeFileSync(path.join(writerOut, 'SOURCE.txt'), [
    `pandoc-pretext: ${pp.description}.`,
    `Author: Oscar Levin. Licence: ${pp.license} (see LICENSE).`,
    `Source: ${pp.source}`,
    matchesPin
      ? `These files are unmodified copies of ${pp.source}/tree/${pp.commit}`
      : `Verified against ${pp.source}/tree/${pp.commit}; these copies differ (see toolchain.json).`,
    '',
  ].join('\n'));

  const manifest = {
    stagedAt: new Date().toISOString(),
    pandoc: {
      version,
      file: path.relative(out, pandocExe).split(path.sep).join('/'),
      sha256: sha256(pandocExe),
      license: PINS.pandoc.license,
      source: `${PINS.pandoc.source}/tree/${version}`,
    },
    pretextWriter: {
      file: 'pretext-writer/pretext.lua',
      license: pp.license,
      source: pp.source,
      verifiedCommit: pp.commit,
      matchesVerifiedCommit: matchesPin,
      files: writerFiles,
    },
  };
  fs.writeFileSync(path.join(out, 'toolchain.json'), JSON.stringify(manifest, null, 2) + '\n');
  fs.writeFileSync(path.join(out, 'NOTICE.txt'), [
    'Third-party software distributed with LyreTeXt',
    '',
    'LyreTeXt runs these as separate programs; they are not part of LyreTeXt',
    'and keep their own licences.',
    '',
    `pandoc/          pandoc ${version} by John MacFarlane, ${PINS.pandoc.license}.`,
    `                 Source: ${PINS.pandoc.source}/tree/${version}`,
    `pretext-writer/  pandoc-pretext by Oscar Levin, ${pp.license}.`,
    `                 Source: ${pp.source}/tree/${pp.commit}`,
    '',
  ].join('\n'));

  let bytes = 0;
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p); else bytes += fs.statSync(p).size;
  });
  walk(out);
  console.log(`staged pandoc ${version} from ${pandocSrc}`);
  console.log(`staged pretext.lua writer from ${writerDir}${matchesPin ? ` (matches ${pp.commit.slice(0, 12)})` : ' (DIFFERS from the verified commit)'}`);
  console.log(`-> ${out} (${(bytes / 1048576).toFixed(1)} MiB)`);
}

try {
  main();
} catch (err) {
  fail(err.message);
}
