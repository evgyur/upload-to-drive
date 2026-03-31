#!/usr/bin/env node

const [, , cdpBase, videoUrl] = process.argv;
if (!cdpBase || !videoUrl) {
  console.error('usage: browser_cdp_youtube.mjs <cdp-base> <video-url>');
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
    const payload = { id, method, params };
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.ws.send(JSON.stringify(payload));
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

const evalExpr = `(() => {
  const pr = globalThis.ytInitialPlayerResponse || null;
  const sd = pr && pr.streamingData || {};
  const norm = (xs) => (xs || []).map(x => ({
    itag: x.itag,
    mimeType: x.mimeType || '',
    qualityLabel: x.qualityLabel || '',
    bitrate: x.bitrate || 0,
    width: x.width || 0,
    height: x.height || 0,
    url: x.url || null,
  }));
  return {
    title: (document.title || '').replace(/\s*-\s*YouTube\s*$/, '').trim(),
    playability: pr && pr.playabilityStatus || null,
    formats: norm(sd.formats),
    adaptive: norm(sd.adaptiveFormats),
  };
})()`;

let tabId = null;
let conn = null;
try {
  const encoded = encodeURIComponent(videoUrl);
  const res = await fetch(`${cdpBase}/json/new?${encoded}`, { method: 'PUT' });
  if (!res.ok) throw new Error(`failed to open tab: HTTP ${res.status}`);
  const tab = await res.json();
  tabId = tab.id;
  conn = new CDPConn(tab.webSocketDebuggerUrl);
  await conn.send('Page.enable');
  await conn.send('Runtime.enable');

  let payload = null;
  for (let i = 0; i < 20; i++) {
    await sleep(1500);
    payload = await conn.eval(evalExpr);
    const usable = (payload?.formats || []).filter(x => x.url && (x.mimeType || '').includes('video/mp4'));
    if (usable.length) break;
  }

  process.stdout.write(JSON.stringify(payload || {}, null, 2));
} catch (err) {
  console.error(String(err && err.message || err));
  process.exitCode = 1;
} finally {
  if (conn) conn.close();
  if (tabId) {
    try { await fetch(`${cdpBase}/json/close/${tabId}`); } catch {}
  }
}
