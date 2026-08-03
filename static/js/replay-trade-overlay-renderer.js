(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ReplayTradeOverlayRenderer = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  function element(adapter, tag, attrs, value, namespace) {
    if (!adapter || typeof adapter.create !== 'function') return null;
    var node = adapter.create(tag, namespace !== false);
    if (!node) return null;
    Object.keys(attrs || {}).forEach(function (name) {
      if (attrs[name] !== null && attrs[name] !== undefined && typeof adapter.attr === 'function') {
        adapter.attr(node, name, attrs[name]);
      }
    });
    if (value !== null && value !== undefined && typeof adapter.text === 'function') {
      adapter.text(node, value);
    }
    return node;
  }

  function append(adapter, parent, child) {
    if (child && adapter && typeof adapter.append === 'function') adapter.append(parent, child);
    return child;
  }

  function renderBracketLevel(adapter, svg, spec) {
    spec = spec || {};
    var group = element(adapter, 'g', {
      'class': 'replay-trade-bracket-draft replay-trade-bracket-' + spec.colorClass,
      'data-bracket-role': spec.role,
    });
    if (!group) return null;
    append(adapter, group, element(adapter, 'line', {
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
      stroke: 'transparent', 'stroke-width': 16,
    }));
    append(adapter, group, element(adapter, 'line', {
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
      'class': 'replay-trade-preset replay-trade-bracket-' + spec.colorClass,
    }));
    var label = append(adapter, group, element(adapter, 'text', {
      'class': 'replay-trade-bracket-label replay-trade-bracket-label-' + spec.colorClass,
      x: spec.labelX, y: spec.y - 8,
      'data-bracket-orders': spec.orderText,
    }, spec.labelText));
    append(adapter, svg, group);
    return { group: group, label: label };
  }

  function renderExecutionMarker(adapter, svg, spec) {
    spec = spec || {};
    var groupAttrs = {
      'class': 'replay-trade-marker replay-trade-' + spec.side + '-marker',
      'data-trade-side': spec.side,
    };
    if (spec.title) groupAttrs['aria-label'] = spec.title;
    var group = element(adapter, 'g', groupAttrs);
    if (!group) return null;
    var levelAttrs = {
      'class': 'replay-trade-execution-level replay-trade-execution-level-' + spec.side,
      'data-trade-label': spec.label,
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
    };
    if (spec.pairColor) levelAttrs.stroke = spec.pairColor;
    append(adapter, group, element(adapter, 'line', levelAttrs));
    if (spec.entryLabel) {
      var host = element(adapter, 'foreignObject', {
        x: spec.entryLabel.x, y: spec.entryLabel.y,
        width: spec.entryLabel.width, height: 22,
        'pointer-events': 'none',
      });
      var label = element(adapter, 'div', {
        'class': 'replay-trade-execution-label',
        'data-order-entry-label': spec.label || 'B',
        style: 'color:' + spec.entryLabel.color + ';font-weight:400;',
      }, spec.entryLabel.text, false);
      append(adapter, host, label);
      append(adapter, group, host);
    }
    var arrowAttrs = {
      'class': 'replay-trade-marker-arrow',
      points: (spec.arrowPoints || []).join(' '),
      'data-price-tip-y': spec.y,
    };
    if (spec.pairColor) arrowAttrs.fill = spec.pairColor;
    append(adapter, group, element(adapter, 'polygon', arrowAttrs));
    append(adapter, group, element(adapter, 'text', {
      'class': 'replay-trade-marker-label',
      x: spec.markerX,
      y: spec.y + (spec.side === 'buy' ? 16 : -16),
    }, spec.label || (spec.side === 'buy' ? 'B' : 'S')));
    append(adapter, svg, group);
    return { group: group };
  }

  function renderRiskZone(adapter, svg, spec) {
    spec = spec || {};
    return append(adapter, svg, element(adapter, 'rect', {
      'class': 'replay-trade-risk-zone replay-trade-risk-zone-' + spec.side,
      'data-order-number': spec.orderNumber,
      'data-zone-role': spec.side,
      x: spec.x, y: spec.y, width: spec.width, height: spec.height,
    }));
  }

  function renderPresetOrder(adapter, svg, spec) {
    spec = spec || {};
    var groupAttrs = {
      'class': 'replay-trade-preset-order replay-trade-preset-order-' + spec.side +
        ' replay-trade-preset-' + spec.side,
      'data-preset-role': spec.side,
      'aria-label': spec.ariaLabel,
      tabindex: 0,
    };
    if (spec.orderId) groupAttrs['data-preset-order-id'] = spec.orderId;
    var group = element(adapter, 'g', groupAttrs);
    if (!group) return null;
    append(adapter, group, element(adapter, 'line', {
      'class': 'replay-trade-preset-hit',
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
    }));
    append(adapter, group, element(adapter, 'line', {
      'class': 'replay-trade-preset replay-trade-preset-' + spec.side,
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
      'data-trade-side': spec.side,
    }));
    append(adapter, group, element(adapter, 'rect', {
      'class': 'replay-trade-preset-label-bg replay-trade-preset-' + spec.side,
      x: spec.labelX, y: spec.labelY, width: spec.labelWidth, height: 22, rx: 3,
    }));
    append(adapter, group, element(adapter, 'text', {
      'class': 'replay-trade-preset-label replay-trade-preset-label-' + spec.side,
      style: 'font-weight:400!important;',
      x: spec.labelX + 8, y: spec.labelY + 12,
    }, spec.labelText));
    var remove = append(adapter, group, element(adapter, 'text', {
      'class': 'replay-trade-preset-delete replay-trade-preset-label-' + spec.side,
      x: spec.labelX + spec.labelWidth - 15, y: spec.labelY + 12,
      'aria-label': spec.deleteAriaLabel,
    }, '×'));
    append(adapter, svg, group);
    return { group: group, remove: remove };
  }

  function renderPresetPreview(adapter, svg, spec) {
    spec = spec || {};
    var line = append(adapter, svg, element(adapter, 'line', {
      'class': 'replay-trade-preset-preview replay-trade-preset-preview-' + spec.side,
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
      'data-trade-side': spec.side,
    }));
    var label = append(adapter, svg, element(adapter, 'text', {
      'class': 'replay-trade-preset-label replay-trade-preset-label-' + spec.side,
      x: spec.labelX, y: spec.y - 7,
      'text-anchor': 'end',
      'aria-label': spec.ariaLabel,
    }, spec.labelText));
    return { line: line, label: label };
  }

  function renderHistoryGhost(adapter, svg, spec) {
    spec = spec || {};
    var group = element(adapter, 'g', {
      'class': 'replay-trade-history-ghost replay-trade-history-ghost-' + spec.side,
    });
    if (!group) return null;
    append(adapter, group, element(adapter, 'line', {
      x1: 0, x2: spec.width, y1: spec.y, y2: spec.y,
      'class': 'replay-trade-preset replay-trade-preset-' + spec.side,
    }));
    append(adapter, group, element(adapter, 'text', {
      x: spec.labelX, y: spec.y - 6,
      'class': 'replay-trade-preset-label replay-trade-preset-label-' + spec.side,
    }, spec.labelText));
    append(adapter, svg, group);
    return { group: group };
  }

  return {
    renderBracketLevel: renderBracketLevel,
    renderExecutionMarker: renderExecutionMarker,
    renderRiskZone: renderRiskZone,
    renderPresetOrder: renderPresetOrder,
    renderPresetPreview: renderPresetPreview,
    renderHistoryGhost: renderHistoryGhost,
  };
});
