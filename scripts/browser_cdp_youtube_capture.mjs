#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';

const [, , cdpBase, videoUrl, outputPath] = process.argv;
if (!cdpBase || !videoUrl || !outputPath) {
  console.error('usage: browser_cdp_youtube_capture.mjs <cdp-base> <video-url> <output-path>');
  process.exit(2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class CDPConn {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', reject, { once: true });
    });
    this.ws.addEventListener('message', (event) => {
      const data = JSON.parse(event.data.toString());
      if (data.id && this.pending.has(data.id)) {
        const { resolve, reject } = this.pending.get(data.id);
        this.pending.delete(data.id);
        if (data.error) reject(new Error(JSON.stringify(data.error)));
        else resolve(data.result || {});
      }
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.ws.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    return res?.result?.value ?? null;
  }

  close() {
    try { this.ws.close(); } catch {}
  }
}

const recordExpr = `
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const chooseQuality = () => {
    const p = document.getElementById('movie_player');
    if (!p) return;
    for (const q of ['highres', 'hd1080', 'hd720', 'large']) {
      try { p.setPlaybackQualityRange?.(q); } catch {}
      try { p.setPlaybackQuality?.(q); } catch {}
    }
  };
  const waitForPlayableVideo = async () => {
    for (let i = 0; i < 60; i++) {
      const player = document.getElementById('movie_player');
      const video = document.querySelector('video');
      if (document.querySelector('.ytp-ad-skip-button, .ytp-ad-skip-button-modern')) {
        document.querySelector('.ytp-ad-skip-button, .ytp-ad-skip-button-modern')?.click();
      }
      if (player && player.classList.contains('ad-showing')) {
        await sleep(1000);
        continue;
      }
      if (video && Number.isFinite(video.duration) && video.duration > 0) return { player, video };
      await sleep(1000);
    }
    return { player: document.getElementById('movie_player'), video: document.querySelector('video') };
  };

  const { player, video } = await waitForPlayableVideo();
  if (!video) return { error: 'no-video-element' };
  chooseQuality();
  try { video.currentTime = 0; } catch {}
  try { await video.play(); } catch (e) { return { error: 'play-failed', detail: String(e) }; }

  const mimeTypes = [
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm'
  ];
  const mimeType = mimeTypes.find(t => MediaRecorder.isTypeSupported(t)) || '';
  const stream = video.captureStream();
  const chunks = [];
  let stopResolve;
  const stopped = new Promise(r => { stopResolve = r; });
  const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  recorder.onstop = () => stopResolve();
  recorder.start(1000);

  const maxMs = Math.max(Math.ceil((video.duration || 20) * 1000) + 4000, 15000);
  await Promise.race([
    new Promise(r => video.addEventListener('ended', r, { once: true })),
    sleep(maxMs),
  ]);

  try { if (recorder.state !== 'inactive') recorder.stop(); } catch {}
  await stopped;

  globalThis.__relayCaptureBlob = new Blob(chunks, { type: recorder.mimeType || 'video/webm' });
  globalThis.__relayCaptureName = 'relay-youtube-capture.webm';
  return {
    ok: true,
    size: globalThis.__relayCaptureBlob.size,
    mimeType: globalThis.__relayCaptureBlob.type,
    duration: video.duration,
    currentTime: video.currentTime,
    title: document.title,
  };
})()
`;

function chunkExpr(offset, chunkSize) {
  return `
(async () => {
  const blob = globalThis.__relayCaptureBlob;
  if (!blob) return { error: 'missing-capture-blob' };
  const start = ${offset};
  const end = Math.min(blob.size, start + ${chunkSize});
  const buf = await blob.slice(start, end).arrayBuffer();
  const bytes = new Uint8Array(buf);
  let binary = '';
  const batch = 0x8000;
  for (let i = 0; i < bytes.length; i += batch) {
    binary += String.fromCharCode(...bytes.subarray(i, i + batch));
  }
  return {
    start,
    end,
    size: blob.size,
    done: end >= blob.size,
    name: globalThis.__relayCaptureName || 'relay-youtube-capture.webm',
    mimeType: blob.type || 'video/webm',
    b64: btoa(binary),
  };
})()
`;
}

(async () => {
  let tabId = null;
  let conn = null;
  try {
    const tabRes = await fetch(`${cdpBase}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' });
    if (!tabRes.ok) throw new Error(`failed to open tab: HTTP ${tabRes.status}`);
    const tab = await tabRes.json();
    tabId = tab.id;
    conn = new CDPConn(tab.webSocketDebuggerUrl);
    await conn.send('Page.enable');
    await conn.send('Runtime.enable');
    await conn.send('Page.navigate', { url: videoUrl });
    await sleep(7000);

    const meta = await conn.eval(recordExpr);
    if (!meta || meta.error) {
      throw new Error(meta?.error ? `${meta.error}: ${meta.detail || ''}` : 'capture failed');
    }

    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    const out = fs.createWriteStream(outputPath);
    const chunkSize = 256 * 1024;
    let offset = 0;
    let final = null;
    while (true) {
      const part = await conn.eval(chunkExpr(offset, chunkSize));
      if (!part || part.error) throw new Error(part?.error || 'chunk read failed');
      out.write(Buffer.from(part.b64, 'base64'));
      offset = part.end;
      final = part;
      if (part.done) break;
    }
    await new Promise((resolve) => out.end(resolve));
    await conn.eval(`(() => { try { delete globalThis.__relayCaptureBlob; delete globalThis.__relayCaptureName; } catch {} return true; })()`);

    process.stdout.write(JSON.stringify({
      outputPath,
      filename: final?.name || path.basename(outputPath),
      mimeType: final?.mimeType || meta.mimeType || 'video/webm',
      size: meta.size,
      duration: meta.duration,
      title: meta.title,
    }, null, 2));
  } catch (err) {
    console.error(String(err && err.message || err));
    process.exitCode = 1;
  } finally {
    if (conn) conn.close();
    if (tabId) {
      try { await fetch(`${cdpBase}/json/close/${tabId}`); } catch {}
    }
  }
})();
