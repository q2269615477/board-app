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

  function renderOrderRailRow(adapter, svg, spec) {
    spec = spec || {};
    var row = element(adapter, 'g', {
      'class': 'replay-trade-order-rail-row',
      'data-order-number': spec.orderNumber,
      'data-pnl-amount': spec.pnlAmount,
      'data-pnl-percent': spec.pnlPercent,
      'data-buy-price': spec.buyPrice,
      'data-order-closed': spec.closed ? 'true' : 'false',
      'aria-label': spec.ariaLabel,
    });
    if (!row) return null;

    var stripBackground = append(adapter, row, element(adapter, 'rect', {
      'class': 'replay-trade-order-strip-bg',
      x: spec.x, y: spec.y, width: spec.rowWidth, height: spec.rowHeight, rx: 7,
    }));
    var buyBackground = append(adapter, row, element(adapter, 'rect', {
      'class': 'replay-trade-order-rail-bg replay-trade-order-rail-buy-bg',
      x: spec.x, y: spec.y, width: spec.buttonWidth - 5, height: spec.rowHeight, rx: 6,
    }));
    append(adapter, row, element(adapter, 'path', {
      'class': 'replay-trade-order-rail-pointer replay-trade-order-rail-buy-bg',
      d: 'M ' + (spec.x + spec.buttonWidth - 6) + ' ' + (spec.y + 7) +
        ' L ' + (spec.x + spec.buttonWidth) + ' ' + (spec.y + spec.rowHeight / 2) +
        ' L ' + (spec.x + spec.buttonWidth - 6) + ' ' + (spec.y + spec.rowHeight - 7) + ' Z',
    }));

    var sellX = spec.x + spec.buttonWidth + spec.pnlWidth;
    var sellBackground = append(adapter, row, element(adapter, 'rect', {
      'class': 'replay-trade-order-rail-bg replay-trade-order-rail-sell-bg',
      x: sellX + 5, y: spec.y, width: spec.buttonWidth - 5, height: spec.rowHeight, rx: 6,
    }));
    append(adapter, row, element(adapter, 'path', {
      'class': 'replay-trade-order-rail-pointer replay-trade-order-rail-sell-bg',
      d: 'M ' + (sellX + 6) + ' ' + (spec.y + 7) +
        ' L ' + sellX + ' ' + (spec.y + spec.rowHeight / 2) +
        ' L ' + (sellX + 6) + ' ' + (spec.y + spec.rowHeight - 7) + ' Z',
    }));

    var buy = append(adapter, row, element(adapter, 'text', {
      'class': 'replay-trade-order-rail-buy',
      x: spec.x + spec.buttonWidth / 2 - 2,
      y: spec.y + 11,
    }, spec.buyLabel));
    append(adapter, row, element(adapter, 'text', {
      'class': 'replay-trade-order-rail-price',
      x: spec.x + spec.buttonWidth / 2 - 2,
      y: spec.y + 24,
    }, spec.buyPriceText));
    append(adapter, row, element(adapter, 'text', {
      'class': 'replay-trade-order-rail-pnl replay-trade-order-rail-pnl-' + spec.pnlClass,
      x: spec.x + spec.buttonWidth + spec.pnlWidth / 2,
      y: spec.y + 10,
    }, spec.pnlPercentText));
    append(adapter, row, element(adapter, 'text', {
      'class': 'replay-trade-order-rail-pnl replay-trade-order-rail-pnl-' + spec.pnlClass,
      x: spec.x + spec.buttonWidth + spec.pnlWidth / 2,
      y: spec.y + 24,
    }, spec.pnlMoneyText));
    var sell = append(adapter, row, element(adapter, 'text', {
      'class': 'replay-trade-order-rail-sell',
      x: sellX + spec.buttonWidth / 2 + 2,
      y: spec.y + 11,
      'data-sell-order-number': spec.orderNumber,
      'data-order-state': spec.closed ? 'closed' : 'open',
    }, spec.sellLabel));

    append(adapter, svg, row);
    return {
      row: row,
      stripBackground: stripBackground,
      buyBackground: buyBackground,
      buy: buy,
      sellBackground: sellBackground,
      sell: sell,
    };
  }

  function renderSummaryText(adapter, parent, value, x, y, className, attrs) {
    return append(adapter, parent, element(adapter, 'text', Object.assign({
      'class': className || 'replay-trade-summary-line',
      x: x,
      y: y,
    }, attrs || {}), value));
  }

  function renderPositionSummary(adapter, svg, spec) {
    spec = spec || {};
    var group = element(adapter, 'g', {
      'class': 'replay-trade-summary-group',
      'pointer-events': 'all',
    });
    if (!group) return null;
    append(adapter, svg, group);

    var box = append(adapter, group, element(adapter, 'rect', {
      'class': 'replay-trade-summary-box',
      x: spec.x, y: spec.y, width: spec.width, height: spec.height, rx: 4,
    }));
    var headings = [];
    (spec.columns || []).forEach(function (item, index) {
      var columnX = spec.x + index * spec.orderWidth;
      append(adapter, group, element(adapter, 'rect', {
        'class': item.columnClass,
        x: columnX + 1,
        y: spec.y + 1,
        width: Math.max(0, spec.orderWidth - 2),
        height: spec.height - 2,
      }));
      if (index > 0) {
        append(adapter, group, element(adapter, 'line', {
          'class': 'replay-trade-summary-separator',
          x1: columnX, x2: columnX,
          y1: spec.y + 8, y2: spec.y + spec.height - 8,
        }));
      }
      var heading = renderSummaryText(adapter, group, item.heading, columnX + 9, spec.y + 18,
        'replay-trade-summary-title replay-trade-summary-order' + item.valueClass, {
          'data-order-number': item.orderNumber,
        });
      headings.push({ node: heading, orderNumber: item.orderNumber, closed: !!item.closed });
      renderSummaryText(adapter, group, item.statusLine, columnX + 9, spec.y + 36,
        'replay-trade-summary-line' + item.valueClass);
      renderSummaryText(adapter, group, item.amountLine, columnX + 9, spec.y + 53,
        'replay-trade-summary-detail' + item.valueClass);
      renderSummaryText(adapter, group, item.finalLine, columnX + 9, spec.y + 70,
        'replay-trade-summary-detail' + item.valueClass);
    });

    var totalX = spec.x + (spec.columns || []).length * spec.orderWidth;
    if ((spec.columns || []).length) {
      append(adapter, group, element(adapter, 'line', {
        'class': 'replay-trade-summary-separator',
        x1: totalX, x2: totalX,
        y1: spec.y + 8, y2: spec.y + spec.height - 8,
      }));
    }
    var total = spec.total || {};
    renderSummaryText(adapter, group, total.heading || '总计', totalX + 10, spec.y + 18,
      'replay-trade-summary-title');
    renderSummaryText(adapter, group, total.label, totalX + 10, spec.y + 36,
      'replay-trade-summary-line' + (total.valueClass || ''));
    renderSummaryText(adapter, group, total.value, totalX + 10, spec.y + 54,
      'replay-trade-summary-line' + (total.valueClass || ''));
    renderSummaryText(adapter, group, total.countText, totalX + 10, spec.y + 71,
      'replay-trade-summary-detail');

    return { group: group, box: box, headings: headings };
  }

  return {
    renderBracketLevel: renderBracketLevel,
    renderExecutionMarker: renderExecutionMarker,
    renderRiskZone: renderRiskZone,
    renderPresetOrder: renderPresetOrder,
    renderPresetPreview: renderPresetPreview,
    renderHistoryGhost: renderHistoryGhost,
    renderOrderRailRow: renderOrderRailRow,
    renderPositionSummary: renderPositionSummary,
  };
});
