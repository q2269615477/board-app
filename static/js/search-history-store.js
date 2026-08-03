// Shared persistent history for all symbol/search inputs.
(function (root, factory) {
  var api = factory(root);
  if (root) root.BoardSearchHistory = api;
  // Some chart bundles expose a browser-side `module` shim whose exports
  // assignment can throw. Register the browser API first so that shim cannot
  // prevent all search inputs from sharing the persistent store.
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis, function (root) {
  var DEFAULT_KEY = 'board_app_search_history';
  var DEFAULT_MAX_ITEMS = 5;

  function safeStorage(storage) {
    if (storage) return storage;
    try { return root && root.localStorage ? root.localStorage : null; } catch (_) { return null; }
  }

  function text(value) {
    return value == null ? '' : String(value).trim();
  }

  function normalize(item) {
    if (typeof item === 'string') item = { value: item };
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null;

    var code = text(item.code);
    var value = text(item.value != null ? item.value : item.query);
    var name = text(item.name);
    if (!code && !value) return null;

    var normalized = {
      code: code,
      value: value,
      name: name,
      type: text(item.type),
      category: text(item.category),
      display_code: text(item.display_code || item.displayCode),
      initials: text(item.initials),
      time: Number.isFinite(Number(item.time)) ? Number(item.time) : Date.now()
    };

    Object.keys(normalized).forEach(function (key) {
      if (normalized[key] === '') delete normalized[key];
    });
    return normalized;
  }

  function sameHistoryEntry(a, b) {
    var ac = text(a && a.code).toLowerCase();
    var bc = text(b && b.code).toLowerCase();
    var av = text(a && a.value).toLowerCase();
    var bv = text(b && b.value).toLowerCase();
    return (!!ac && !!bc && ac === bc) || (!!av && !!bv && av === bv);
  }

  function createSearchHistoryStore(storage, options) {
    options = options || {};
    var target = safeStorage(storage);
    var key = text(options.key) || DEFAULT_KEY;
    var requestedMax = Math.floor(Number(options.maxItems) || DEFAULT_MAX_ITEMS);
    var maxItems = Math.min(DEFAULT_MAX_ITEMS, Math.max(1, requestedMax));

    function read() {
      if (!target || typeof target.getItem !== 'function') return [];
      try {
        var raw = target.getItem(key);
        if (!raw) return [];
        var parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        var result = [];
        parsed.forEach(function (item) {
          var normalized = normalize(item);
          if (!normalized || result.some(function (existing) { return sameHistoryEntry(existing, normalized); })) return;
          result.push(normalized);
        });
        var bounded = result.slice(0, maxItems);
        if (bounded.length !== parsed.length && target && typeof target.setItem === 'function') {
          try { target.setItem(key, JSON.stringify(bounded)); } catch (_) {}
        }
        return bounded;
      } catch (_) {
        return [];
      }
    }

    function write(items) {
      var list = Array.isArray(items) ? items.slice(0, maxItems) : [];
      if (!target || typeof target.setItem !== 'function') return list;
      try { target.setItem(key, JSON.stringify(list)); } catch (_) {}
      return list;
    }

    function compact(items) {
      var result = [];
      (Array.isArray(items) ? items : []).forEach(function (item) {
        var normalized = normalize(item);
        if (!normalized || result.some(function (existing) { return sameHistoryEntry(existing, normalized); })) return;
        result.push(normalized);
      });
      return result.slice(0, maxItems);
    }

    return {
      key: key,
      maxItems: maxItems,
      list: read,
      load: read,
      add: function (item) {
        var normalized = normalize(item);
        var current = read();
        if (!normalized) return current;
        var next = [normalized].concat(current.filter(function (existing) {
          return !sameHistoryEntry(existing, normalized);
        }));
        return write(next);
      },
      replace: function (items) { return write(compact(items)); },
      clear: function () { return write([]); }
    };
  }

  var defaultStore = createSearchHistoryStore();
  var api = {
    STORAGE_KEY: DEFAULT_KEY,
    MAX_ITEMS: DEFAULT_MAX_ITEMS,
    create: createSearchHistoryStore,
    list: function () { return defaultStore.list(); },
    load: function () { return defaultStore.load(); },
    add: function (item) { return defaultStore.add(item); },
    replace: function (items) { return defaultStore.replace(items); },
    clear: function () { return defaultStore.clear(); }
  };

  return api;
}));
