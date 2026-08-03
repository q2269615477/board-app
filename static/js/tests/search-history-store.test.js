const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const history = require('../search-history-store.js');

function makeStorage(initial) {
  const values = new Map();
  if (initial !== undefined) values.set('board_app_search_history', initial);
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
    raw() { return values.get('board_app_search_history'); },
  };
}

describe('BoardSearchHistory', () => {
  test('keeps at most five entries and puts the newest entry first', () => {
    const storage = makeStorage();
    const store = history.create(storage);

    for (let index = 0; index < 7; index += 1) {
      store.add({ code: `code-${index}`, value: `query-${index}`, name: `item-${index}` });
    }

    assert.equal(store.list().length, 5);
    assert.deepEqual(store.list().map((item) => item.code), [
      'code-6', 'code-5', 'code-4', 'code-3', 'code-2',
    ]);
    assert.deepEqual(JSON.parse(storage.raw()).map((item) => item.code), [
      'code-6', 'code-5', 'code-4', 'code-3', 'code-2',
    ]);
  });

  test('compacts legacy arrays during the first read', () => {
    const storage = makeStorage(JSON.stringify(
      Array.from({ length: 8 }, (_, index) => ({ code: `legacy-${index}`, value: `q-${index}` })),
    ));
    const store = history.create(storage, { maxItems: 20 });

    assert.equal(store.list().length, 5);
    assert.equal(JSON.parse(storage.raw()).length, 5);
  });

  test('deduplicates when either code or value matches and refreshes recency', () => {
    const store = history.create(makeStorage());
    store.add({ code: 'A', value: 'alpha', name: 'old A' });
    store.add({ code: 'B', value: 'beta', name: 'B' });
    store.add({ code: 'A', value: 'new-alpha', name: 'new A' });

    assert.deepEqual(store.list().map((item) => item.name), ['new A', 'B']);

    store.add({ code: 'C', value: 'beta', name: 'new beta' });
    assert.deepEqual(store.list().map((item) => item.name), ['new beta', 'new A']);
  });

  test('ignores malformed persisted data and malformed entries', () => {
    const storage = makeStorage('{not-json');
    const store = history.create(storage);
    assert.deepEqual(store.list(), []);

    storage.setItem('board_app_search_history', JSON.stringify([
      null,
      'plain text is not a record',
      {},
      { code: 'A', value: 'alpha', name: 'A' },
      { code: 'A', value: 'duplicate', name: 'duplicate A' },
      { value: 'beta', name: 'B' },
    ]));
    assert.equal(store.list().length, 3);
    assert.equal(store.list()[0].value, 'plain text is not a record');
    assert.deepEqual(store.list().slice(1).map((item) => item.name), ['A', 'B']);
  });

  test('degrades safely when localStorage operations throw', () => {
    const brokenStorage = {
      getItem() { throw new Error('storage unavailable'); },
      setItem() { throw new Error('storage unavailable'); },
    };
    const store = history.create(brokenStorage);

    assert.deepEqual(store.list(), []);
    assert.deepEqual(store.add({ code: 'A', value: 'alpha' }).map((item) => item.code), ['A']);
    assert.deepEqual(store.list(), []);
  });
});
