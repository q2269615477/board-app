// 最小DOM mock供测试用（零依赖，不依赖Flask后端）
global.window = global.window || {};
global.document = global.document || {
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
};
global.EventSource = function () { return { addEventListener: () => {}, close: () => {} }; };
global.WebSocket = function () { return { addEventListener: () => {}, close: () => {} }; };
global.localStorage = global.localStorage || {
  _s: {},
  getItem: function (k) { return this._s[k] || null; },
  setItem: function (k, v) { this._s[k] = String(v); },
  removeItem: function (k) { delete this._s[k]; },
};
module.exports = { window: global.window, document: global.document };