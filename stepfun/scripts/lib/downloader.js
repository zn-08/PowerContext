/**
 * scripts/lib/downloader.js — 极简 HTTP(S) 下载器（支持 HTTP 代理）
 *
 * 为什么要自己写：
 *   Node 24 的全局 fetch(undici) 默认不读取 HTTP_PROXY / HTTPS_PROXY 环境变量，
 *   而评审机/开发机常常只有代理才能访问 huggingface.co。
 *   这里用内置 http/https 模块实现 CONNECT 隧道，无第三方依赖，跨平台。
 *
 * 若环境无代理（离线直连），会自动走直连分支。
 */
import http from 'node:http';
import https from 'node:https';
import { URL } from 'node:url';

const UA = 'stepfun-seekdb-kb/1.0 (+local model fetcher)';

function proxyFromEnv() {
  const raw = process.env.HTTPS_PROXY || process.env.https_proxy
    || process.env.HTTP_PROXY || process.env.http_proxy || '';
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return {
      host: u.hostname,
      port: Number(u.port) || (u.protocol === 'https:' ? 443 : 80),
    };
  } catch {
    return null;
  }
}

function directGet(u, depth = 0) {
  return new Promise((resolve, reject) => {
    const mod = u.protocol === 'https:' ? https : http;
    const req = mod.get(
      { protocol: u.protocol, hostname: u.hostname, port: u.port || (u.protocol === 'https:' ? 443 : 80), path: u.pathname + u.search, headers: { 'User-Agent': UA } },
      (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && depth < 5) {
          res.resume();
          return resolve(fetchBuffer(new URL(res.headers.location, u).toString(), depth + 1));
        }
        if (res.statusCode !== 200) {
          res.resume();
          return reject(new Error(`HTTP ${res.statusCode} ${u.toString()}`));
        }
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve(Buffer.concat(chunks)));
        res.on('error', reject);
      }
    );
    req.on('error', reject);
    req.setTimeout(180000, () => req.destroy(new Error(`timeout ${u.toString()}`)));
  });
}

function proxyGet(u, proxy, depth = 0) {
  return new Promise((resolve, reject) => {
    const targetPort = u.port || (u.protocol === 'https:' ? 443 : 80);
    const req = http.request({
      host: proxy.host,
      port: proxy.port,
      method: 'CONNECT',
      path: `${u.hostname}:${targetPort}`,
      headers: { Host: `${u.hostname}:${targetPort}` },
    });
    req.on('connect', (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy();
        return reject(new Error(`proxy CONNECT ${res.statusCode} ${proxy.host}:${proxy.port}`));
      }
      const mod = u.protocol === 'https:' ? https : http;
      const req2 = mod.get(
        {
          protocol: u.protocol,
          hostname: u.hostname,
          port: targetPort,
          path: u.pathname + u.search,
          socket,
          agent: false,
          headers: { 'User-Agent': UA, Host: u.host },
        },
        (res2) => {
          if (res2.statusCode >= 300 && res2.statusCode < 400 && res2.headers.location && depth < 5) {
            res2.resume();
            socket.destroy();
            return resolve(fetchBuffer(new URL(res2.headers.location, u).toString(), depth + 1));
          }
          if (res2.statusCode !== 200) {
            res2.resume();
            socket.destroy();
            return reject(new Error(`HTTP ${res2.statusCode} ${u.toString()}`));
          }
          const chunks = [];
          res2.on('data', (c) => chunks.push(c));
          res2.on('end', () => { socket.destroy(); resolve(Buffer.concat(chunks)); });
          res2.on('error', (e) => { socket.destroy(); reject(e); });
        }
      );
      req2.on('error', (e) => { socket.destroy(); reject(e); });
      req2.setTimeout(180000, () => req2.destroy(new Error('timeout')));
    });
    req.on('error', reject);
    req.setTimeout(180000, () => req.destroy(new Error('proxy timeout')));
    req.end();
  });
}

/** 下载 URL 对应的二进制内容（自动选择直连 / 代理） */
export async function fetchBuffer(urlStr, depth = 0) {
  const u = new URL(urlStr);
  const proxy = proxyFromEnv();
  if (proxy) {
    try {
      return await proxyGet(u, proxy, depth);
    } catch (e) {
      console.warn(`[downloader] 代理下载失败(${e.message})，尝试直连…`);
      return await directGet(u, depth);
    }
  }
  return directGet(u, depth);
}
