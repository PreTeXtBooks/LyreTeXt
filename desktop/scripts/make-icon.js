#!/usr/bin/env node
// Regenerate desktop/assets/icon.ico and icon.png from frontend/assets/logomark.svg.
//
// The outputs are committed; run this only when the logo changes. It needs no
// npm packages and downloads nothing: a locally installed Edge or Chrome
// renders the SVG headless at each size, and the PNGs are packed into a
// Windows .ico here (32-bit BMP entries up to 128 px, PNG for 256 px).
//
//   node scripts/make-icon.js [--browser <path to msedge.exe / chrome.exe>]
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');
const { spawnSync } = require('child_process');

const DESKTOP = path.resolve(__dirname, '..');
const SVG = path.resolve(DESKTOP, '..', 'frontend', 'assets', 'logomark.svg');
const OUT = path.join(DESKTOP, 'assets');
const SIZES = [16, 24, 32, 48, 64, 128, 256];

function findBrowser() {
  const i = process.argv.indexOf('--browser');
  if (i !== -1) return process.argv[i + 1];
  const candidates = [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  ];
  const found = candidates.find((c) => fs.existsSync(c));
  if (!found) throw new Error('No Edge or Chrome found; pass --browser <path>');
  return found;
}

function render(browser, dir, size) {
  const html = path.join(dir, `icon${size}.html`);
  const png = path.join(dir, `icon${size}.png`);
  fs.writeFileSync(html, `<!doctype html><html><body style="margin:0">`
    + `<img src="logomark.svg" style="width:${size}px;height:${size}px;display:block"></body></html>`);
  const r = spawnSync(browser, [
    '--headless=new', '--disable-gpu', '--hide-scrollbars',
    `--window-size=${size},${size}`, `--screenshot=${png}`,
    `--user-data-dir=${path.join(dir, 'profile')}`,
    'file:///' + html.split(path.sep).join('/'),
  ], { timeout: 60000, windowsHide: true });
  if (!fs.existsSync(png)) throw new Error(`render at ${size}px failed: ${r.stderr}`);
  return fs.readFileSync(png);
}

// Minimal decoder for what a headless browser screenshot is: 8-bit RGB or RGBA,
// non-interlaced. Returns { width, height, rgba }.
function decodePng(buf) {
  let pos = 8;
  let width, height, colorType;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('latin1', pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      colorType = data[9];
      if (data[8] !== 8 || ![2, 6].includes(colorType) || data[12] !== 0) {
        throw new Error('unsupported PNG (need 8-bit RGB/RGBA, non-interlaced)');
      }
    } else if (type === 'IDAT') {
      idat.push(data);
    }
    pos += 12 + len;
  }
  const bpp = colorType === 6 ? 4 : 3;
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * bpp;
  const px = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? px[y * stride + x - bpp] : 0;
      const b = y > 0 ? px[(y - 1) * stride + x] : 0;
      const c = x >= bpp && y > 0 ? px[(y - 1) * stride + x - bpp] : 0;
      let v = line[x];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      px[y * stride + x] = v & 0xff;
    }
  }
  const rgba = Buffer.alloc(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    rgba[i * 4] = px[i * bpp];
    rgba[i * 4 + 1] = px[i * bpp + 1];
    rgba[i * 4 + 2] = px[i * bpp + 2];
    rgba[i * 4 + 3] = bpp === 4 ? px[i * bpp + 3] : 255;
  }
  return { width, height, rgba };
}

// A 32-bit BMP icon entry: BITMAPINFOHEADER, bottom-up BGRA, empty AND mask.
function bmpEntry({ width, height, rgba }) {
  const header = Buffer.alloc(40);
  header.writeUInt32LE(40, 0);
  header.writeInt32LE(width, 4);
  header.writeInt32LE(height * 2, 8);
  header.writeUInt16LE(1, 12);
  header.writeUInt16LE(32, 14);
  const pixels = Buffer.alloc(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const src = ((height - 1 - y) * width + x) * 4;
      const dst = (y * width + x) * 4;
      pixels[dst] = rgba[src + 2];
      pixels[dst + 1] = rgba[src + 1];
      pixels[dst + 2] = rgba[src];
      pixels[dst + 3] = rgba[src + 3];
    }
  }
  const mask = Buffer.alloc(Math.ceil(width / 32) * 4 * height);
  return Buffer.concat([header, pixels, mask]);
}

function packIco(entries) {
  const dir = Buffer.alloc(6 + 16 * entries.length);
  dir.writeUInt16LE(1, 2);
  dir.writeUInt16LE(entries.length, 4);
  let offset = dir.length;
  entries.forEach(({ size, data }, i) => {
    const e = 6 + 16 * i;
    dir[e] = size >= 256 ? 0 : size;
    dir[e + 1] = size >= 256 ? 0 : size;
    dir.writeUInt16LE(1, e + 4);
    dir.writeUInt16LE(32, e + 6);
    dir.writeUInt32LE(data.length, e + 8);
    dir.writeUInt32LE(offset, e + 12);
    offset += data.length;
  });
  return Buffer.concat([dir, ...entries.map((e) => e.data)]);
}

function main() {
  const browser = findBrowser();
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'lyretext-icon-'));
  try {
    fs.copyFileSync(SVG, path.join(work, 'logomark.svg'));
    const entries = SIZES.map((size) => {
      const png = render(browser, work, size);
      const img = decodePng(png);
      if (img.width !== size || img.height !== size) {
        throw new Error(`expected ${size}x${size}, got ${img.width}x${img.height}`);
      }
      if (size === 256) fs.writeFileSync(path.join(OUT, 'icon.png'), png);
      return { size, data: size === 256 ? png : bmpEntry(img) };
    });
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(path.join(OUT, 'icon.ico'), packIco(entries));
    console.log(`wrote ${path.join(OUT, 'icon.ico')} (${SIZES.join(', ')} px) and icon.png`);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

main();
