#!/usr/bin/env node
/**
 * mcp_board_bridge.js — board-app MCP 桥接器
 * 将 board-app 的 HTTP REST API 包装为标准 MCP 协议（stdio）
 * 
 * WorkBuddy 通过 stdin/stdout JSON-RPC 2.0 通信
 * 桥接器内部通过 HTTP 调用 board-app 的 /mcp/tools 和 /mcp/call
 */
const http = require('http');

const BOARD_APP = 'http://127.0.0.1:5000';

function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ jsonrpc: '2.0', method, params, id: Date.now().toString() });
    const req = http.request(`${BOARD_APP}/mcp/call`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout: 10000
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error(data)); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function listTools() {
  return new Promise((resolve, reject) => {
    http.get(`${BOARD_APP}/mcp/tools`, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try {
          const j = JSON.parse(data);
          resolve(j.tools || []);
        } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

const handlers = {
  async 'tools/list'() {
    const tools = await listTools();
    return { tools };
  },
  async 'tools/call'(params) {
    const { name, arguments: args } = params;
    // 直接 POST 到 Flask，不做 JSON-RPC 封装
    const body = JSON.stringify({ tool: name, arguments: args || {} });
    const result = await new Promise((resolve, reject) => {
      const req = http.request(`${BOARD_APP}/mcp/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        timeout: 30000
      }, res => {
        let data = '';
        res.on('data', c => data += c);
        res.on('end', () => {
          try { resolve(JSON.parse(data)); }
          catch (e) { resolve({ error: data }); }
        });
      });
      req.on('error', reject);
      req.write(body);
      req.end();
    });
    // 按 MCP 协议封装返回值
    return {
      content: [{ type: 'text', text: JSON.stringify(result) }]
    };
  },
  async 'initialize'(params) {
    return {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'board-app', version: '3.0.0' }
    };
  }
};

process.stdin.setEncoding('utf8');
let buf = '';

process.stdin.on('data', async chunk => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    
    try {
      const msg = JSON.parse(line);
      const { method, params, id } = msg;
      
      if (id === undefined || id === null) continue;
      
      const handler = handlers[method];
      if (!handler) {
        const err = { jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${method}` } };
        process.stdout.write(JSON.stringify(err) + '\n');
        continue;
      }
      
      try {
        const result = await handler(params || {});
        const resp = { jsonrpc: '2.0', id, result };
        process.stdout.write(JSON.stringify(resp) + '\n');
      } catch (e) {
        const err = { jsonrpc: '2.0', id, error: { code: -32603, message: e.message } };
        process.stdout.write(JSON.stringify(err) + '\n');
      }
    } catch (e) {
      const err = { jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } };
      process.stdout.write(JSON.stringify(err) + '\n');
    }
  }
});

process.stdin.resume();
