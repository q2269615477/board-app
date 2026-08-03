def normalize_number(value):
    if value in (None, ''):
        return None
    try:
        if hasattr(value, 'item') and callable(getattr(value, 'item')):
            value = value.item()
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def normalize_market_volume(value):
    """Repair QMT signed int32 overflow for very large market volumes."""
    volume = normalize_number(value)
    if volume is not None and -(2 ** 31) <= volume < 0:
        volume += 2 ** 32
    return volume


def normalize_int(value):
    number = normalize_number(value)
    if number is None:
        return None
    return int(number)


def normalize_market_time(value, period):
    if value is None:
        return None
    if hasattr(value, 'strftime'):
        if period in ('tick', '1m', '3m', '5m', '15m', '30m', '1h'):
            return value.strftime('%Y%m%d%H%M%S')
        return value.strftime('%Y%m%d')
    text = str(value)
    digits = ''.join([char for char in text if char.isdigit()])
    if period == '1mon' and len(digits) >= 6:
        return digits[:6] + '01'
    if len(digits) >= 14:
        return digits[:14]
    if len(digits) >= 8:
        return digits[:8]
    return text


def extract_market_rows(result, fields):
    """Single-symbol (or flat) row extract. Unchanged contract for /candles."""
    rows = []
    iterrows = getattr(result, 'iterrows', None)
    if callable(iterrows):
        try:
            for index, row in iterrows():
                row_dict = row.to_dict() if hasattr(row, 'to_dict') else {}
                rows.append((index, row_dict))
            if rows:
                return rows
        except Exception:
            pass
    if isinstance(result, dict):
        dict_values = []
        for field in fields:
            value = result.get(field)
            if isinstance(value, dict):
                dict_values.append(value)
        if dict_values:
            times = []
            for field_values in dict_values:
                for key in field_values.keys():
                    if key not in times:
                        times.append(key)
            for time_key in times:
                row_dict = {}
                for field in fields:
                    field_values = result.get(field)
                    if isinstance(field_values, dict):
                        row_dict[field] = field_values.get(time_key)
                rows.append((time_key, row_dict))
    return rows


def _symbol_key_variants(symbol):
    """Match QMT column labels that may omit/alter suffix."""
    if not symbol:
        return []
    text = str(symbol).strip()
    variants = [text, text.upper(), text.lower()]
    if '.' in text:
        code, mkt = text.split('.', 1)
        variants.extend([code, code.upper(), code.lower(),
                         '%s.%s' % (code, mkt.upper()),
                         '%s.%s' % (code, mkt.lower())])
    # de-dupe preserve order
    seen = set()
    out = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _pick_field_matrix(result, field):
    """Return field payload from get_market_data result (dict or attr)."""
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get(field)
    return getattr(result, field, None)


def _iter_time_value_pairs(series_like):
    """Yield (time_key, value) from Series/dict/list."""
    if series_like is None:
        return
    if isinstance(series_like, dict):
        for k, v in series_like.items():
            yield k, v
        return
    # pandas Series
    index = getattr(series_like, 'index', None)
    values = getattr(series_like, 'values', None)
    if index is not None and values is not None:
        try:
            for i, key in enumerate(list(index)):
                yield key, values[i]
            return
        except Exception:
            pass
    tolist = getattr(series_like, 'tolist', None)
    if callable(tolist):
        try:
            for i, v in enumerate(tolist()):
                yield i, v
        except Exception:
            pass


def _dataframe_column_map(df):
    """Map normalized column name -> original column label."""
    mapping = {}
    columns = getattr(df, 'columns', None)
    if columns is None:
        return mapping
    try:
        for col in list(columns):
            mapping[str(col)] = col
            mapping[str(col).upper()] = col
            mapping[str(col).lower()] = col
            text = str(col)
            if '.' in text:
                code = text.split('.', 1)[0]
                mapping[code] = col
                mapping[code.upper()] = col
                mapping[code.lower()] = col
    except Exception:
        pass
    return mapping


def extract_ohlc_bars_by_symbol(result, fields, symbols, period):
    """
    Multi-symbol extract for get_market_data(stock_code=[...], count=N).

    Returns: {symbol: [ {time, open, high, low, close, volume, amount}, ... ]}
    Bars sorted by time ascending. Empty list if symbol missing.
    """
    out = {sym: [] for sym in symbols}
    if result is None or not symbols:
        return out

    # Path 1: field -> DataFrame(index=time, columns=symbols)
    sample = _pick_field_matrix(result, fields[0] if fields else 'close')
    has_columns = sample is not None and getattr(sample, 'columns', None) is not None
    if has_columns:
        col_map = _dataframe_column_map(sample)
        index = getattr(sample, 'index', None)
        times = list(index) if index is not None else []
        for sym in symbols:
            col = None
            for key in _symbol_key_variants(sym):
                if key in col_map:
                    col = col_map[key]
                    break
            if col is None:
                continue
            bars = []
            for t in times:
                row_dict = {}
                ok = False
                for field in fields:
                    matrix = _pick_field_matrix(result, field)
                    if matrix is None:
                        row_dict[field] = None
                        continue
                    try:
                        # DataFrame .loc[t, col]
                        loc = getattr(matrix, 'loc', None)
                        if loc is not None:
                            val = matrix.loc[t, col]
                        else:
                            val = None
                    except Exception:
                        val = None
                    row_dict[field] = val
                    if val is not None:
                        ok = True
                if not ok:
                    continue
                bars.append({
                    'time': normalize_market_time(t, period),
                    'open': normalize_number(row_dict.get('open')),
                    'high': normalize_number(row_dict.get('high')),
                    'low': normalize_number(row_dict.get('low')),
                    'close': normalize_number(row_dict.get('close')),
                    'volume': normalize_market_volume(row_dict.get('volume')),
                    'amount': normalize_number(row_dict.get('amount')),
                })
            bars = [b for b in bars if b.get('time') is not None]
            out[sym] = bars
        if any(out[s] for s in symbols):
            return out

    # Path 2: MultiIndex DataFrame rows (symbol, time) or index contains symbol
    iterrows = getattr(result, 'iterrows', None)
    if callable(iterrows) and not isinstance(result, dict):
        try:
            bucket = {sym: {} for sym in symbols}  # sym -> time -> row_dict
            for index, row in iterrows():
                row_dict = row.to_dict() if hasattr(row, 'to_dict') else {}
                sym = None
                tkey = index
                if isinstance(index, (list, tuple)) and len(index) >= 2:
                    a, b = index[0], index[1]
                    # guess which is symbol
                    for cand in (a, b):
                        for sym0 in symbols:
                            if str(cand) in _symbol_key_variants(sym0) or str(cand) == sym0:
                                sym = sym0
                                tkey = b if cand is a else a
                                break
                        if sym:
                            break
                if sym is None:
                    # single-symbol style
                    continue
                bucket[sym][tkey] = row_dict
            for sym in symbols:
                bars = []
                for tkey, row_dict in bucket[sym].items():
                    bars.append({
                        'time': normalize_market_time(tkey, period),
                        'open': normalize_number(row_dict.get('open')),
                        'high': normalize_number(row_dict.get('high')),
                        'low': normalize_number(row_dict.get('low')),
                        'close': normalize_number(row_dict.get('close')),
                        'volume': normalize_market_volume(row_dict.get('volume')),
                        'amount': normalize_number(row_dict.get('amount')),
                    })
                bars = [b for b in bars if b.get('time') is not None]
                bars.sort(key=lambda x: str(x.get('time') or ''))
                out[sym] = bars
            if any(out[s] for s in symbols):
                return out
        except Exception:
            pass

    # Path 3: field -> {symbol: {time: value}} or field -> {symbol: Series}
    if isinstance(result, dict):
        for sym in symbols:
            # collect times across fields for this symbol
            time_set = []
            per_field = {}
            for field in fields:
                matrix = result.get(field)
                series = None
                if isinstance(matrix, dict):
                    for key in _symbol_key_variants(sym):
                        if key in matrix:
                            series = matrix[key]
                            break
                elif matrix is not None and getattr(matrix, 'columns', None) is not None:
                    col_map = _dataframe_column_map(matrix)
                    col = None
                    for key in _symbol_key_variants(sym):
                        if key in col_map:
                            col = col_map[key]
                            break
                    if col is not None:
                        try:
                            series = matrix[col]
                        except Exception:
                            series = None
                per_field[field] = {}
                if series is not None:
                    for t, v in _iter_time_value_pairs(series):
                        per_field[field][t] = v
                        if t not in time_set:
                            time_set.append(t)
            bars = []
            for t in time_set:
                bars.append({
                    'time': normalize_market_time(t, period),
                    'open': normalize_number(per_field.get('open', {}).get(t)),
                    'high': normalize_number(per_field.get('high', {}).get(t)),
                    'low': normalize_number(per_field.get('low', {}).get(t)),
                    'close': normalize_number(per_field.get('close', {}).get(t)),
                    'volume': normalize_market_volume(
                        per_field.get('volume', {}).get(t)
                    ),
                    'amount': normalize_number(per_field.get('amount', {}).get(t)),
                })
            bars = [b for b in bars if b.get('time') is not None and b.get('close') is not None]
            bars.sort(key=lambda x: str(x.get('time') or ''))
            out[sym] = bars
        if any(out[s] for s in symbols):
            return out

    # Path 4: single-symbol flat extract — only valid when len(symbols)==1
    if len(symbols) == 1:
        sym = symbols[0]
        bars = []
        for index, row_dict in extract_market_rows(result, fields):
            bars.append({
                'time': normalize_market_time(index, period),
                'open': normalize_number(row_dict.get('open')),
                'high': normalize_number(row_dict.get('high')),
                'low': normalize_number(row_dict.get('low')),
                'close': normalize_number(row_dict.get('close')),
                'volume': normalize_market_volume(row_dict.get('volume')),
                'amount': normalize_number(row_dict.get('amount')),
            })
        bars = [b for b in bars if b.get('time') is not None]
        out[sym] = bars
    return out


def _bar_to_ohlc_item(symbol, bars):
    """Last bar + pre_close/change_pct from previous bar if present."""
    if not bars:
        return None
    last = bars[-1]
    pre_close = None
    if len(bars) >= 2 and bars[-2].get('close') is not None:
        pre_close = bars[-2].get('close')
    close = last.get('close')
    change_pct = None
    change = None
    if close is not None and pre_close not in (None, 0):
        change = close - pre_close
        change_pct = (change / pre_close) * 100.0
    return {
        'symbol': symbol,
        'time': last.get('time'),
        'open': last.get('open'),
        'high': last.get('high'),
        'low': last.get('low'),
        'close': last.get('close'),
        'volume': last.get('volume'),
        'amount': last.get('amount'),
        'pre_close': pre_close,
        'change': change,
        'change_pct': change_pct,
    }


def build_ohlc_batch_payload(
    runtime,
    symbols,
    period,
    dividend_type,
    record_error,
    count=2,
):
    """
    Scheme B: native multi-symbol get_market_data(..., count=1|2) for intraday OHLC snapshot.
    count default 2 so pre_close/change_pct can be derived.
    Falls back to per-symbol loop (scheme A) if native multi yields nothing.
    """
    import time as _time_mod
    t0 = _time_mod.time()
    symbols = [s for s in symbols if s]
    empty = {
        'channel': 'qmt_native_batch',
        'period': period,
        'count': 1,
        'dividend_type': dividend_type,
        'requested_count': len(symbols),
        'success_count': 0,
        'error_count': 0,
        'items': {},
        'errors': [],
        'elapsed_ms': 0,
        'mode': 'empty',
    }
    if not symbols:
        empty['error'] = 'symbols_required'
        return empty

    context = runtime.context_ref
    if context is None:
        empty['error'] = 'context_unavailable'
        empty['error_count'] = len(symbols)
        empty['errors'] = [{'symbol': s, 'error': 'context_unavailable'} for s in symbols]
        empty['elapsed_ms'] = int((_time_mod.time() - t0) * 1000)
        return empty

    get_market_data = getattr(context, 'get_market_data', None)
    if not callable(get_market_data):
        empty['error'] = 'get_market_data_unavailable'
        empty['error_count'] = len(symbols)
        empty['errors'] = [{'symbol': s, 'error': 'get_market_data_unavailable'} for s in symbols]
        empty['elapsed_ms'] = int((_time_mod.time() - t0) * 1000)
        return empty

    fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
    pull_count = count if count and count > 0 else 2
    if pull_count < 2:
        pull_count = 2  # need prev bar for change_pct

    items = {}
    errors = []
    mode = 'native'

    try:
        result = get_market_data(
            fields,
            stock_code=list(symbols),
            period=period,
            dividend_type=dividend_type,
            count=pull_count,
        )
        by_sym = extract_ohlc_bars_by_symbol(result, fields, symbols, period)
        for sym in symbols:
            bars = by_sym.get(sym) or []
            item = _bar_to_ohlc_item(sym, bars)
            if item and item.get('close') is not None:
                items[sym] = item
            else:
                errors.append({'symbol': sym, 'error': 'no_bars'})
    except Exception:
        record_error('build_ohlc_batch_payload_native')
        mode = 'fallback_loop'
        items = {}
        errors = []

    # Fallback scheme A: loop single-symbol if native incomplete
    missing = [s for s in symbols if s not in items]
    if missing:
        mode = 'native+fallback' if items else 'fallback_loop'
        for sym in missing:
            try:
                result = get_market_data(
                    fields,
                    stock_code=[sym],
                    period=period,
                    dividend_type=dividend_type,
                    count=pull_count,
                )
                by_sym = extract_ohlc_bars_by_symbol(result, fields, [sym], period)
                item = _bar_to_ohlc_item(sym, by_sym.get(sym) or [])
                if item and item.get('close') is not None:
                    items[sym] = item
                    # drop prior no_bars error for this sym
                    errors = [e for e in errors if e.get('symbol') != sym]
                else:
                    if not any(e.get('symbol') == sym for e in errors):
                        errors.append({'symbol': sym, 'error': 'no_bars'})
            except Exception:
                record_error('build_ohlc_batch_payload_loop')
                if not any(e.get('symbol') == sym for e in errors):
                    errors.append({'symbol': sym, 'error': 'get_market_data_failed'})

    elapsed_ms = int((_time_mod.time() - t0) * 1000)
    return {
        'channel': 'qmt_native_batch' if mode == 'native' else ('qmt_loop' if mode == 'fallback_loop' else 'qmt_native_batch+loop'),
        'period': period,
        'count': 1,
        'dividend_type': dividend_type,
        'requested_count': len(symbols),
        'success_count': len(items),
        'error_count': len(errors),
        'items': items,
        'errors': errors,
        'elapsed_ms': elapsed_ms,
        'mode': mode,
    }


def build_candles_payload(
    runtime,
    symbol,
    period,
    count,
    start,
    end,
    dividend_type,
    record_error,
):
    context = runtime.context_ref
    if context is None:
        return {
            'symbol': symbol,
            'period': period,
            'count': count,
            'start': start,
            'end': end,
            'dividend_type': dividend_type,
            'bars': [],
            'error': 'context_unavailable',
        }
    get_market_data = getattr(context, 'get_market_data', None)
    if not callable(get_market_data):
        return {
            'symbol': symbol,
            'period': period,
            'count': count,
            'start': start,
            'end': end,
            'dividend_type': dividend_type,
            'bars': [],
            'error': 'get_market_data_unavailable',
        }
    fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
    kwargs = {
        'stock_code': [symbol],
        'period': period,
        'dividend_type': dividend_type,
        'count': count,
    }
    if start:
        kwargs['start_time'] = start
    if end:
        kwargs['end_time'] = end
    try:
        result = get_market_data(fields, **kwargs)
        bars = []
        for index, row_dict in extract_market_rows(result, fields):
            bars.append({
                'time': normalize_market_time(index, period),
                'open': normalize_number(row_dict.get('open')),
                'high': normalize_number(row_dict.get('high')),
                'low': normalize_number(row_dict.get('low')),
                'close': normalize_number(row_dict.get('close')),
                'volume': normalize_market_volume(row_dict.get('volume')),
                'amount': normalize_number(row_dict.get('amount')),
            })
        bars = [bar for bar in bars if bar['time'] is not None]
        return {
            'symbol': symbol,
            'period': period,
            'count': count,
            'start': start,
            'end': end,
            'dividend_type': dividend_type,
            'bars': bars,
        }
    except Exception:
        record_error('_build_candles_payload')
        return {
            'symbol': symbol,
            'period': period,
            'count': count,
            'start': start,
            'end': end,
            'dividend_type': dividend_type,
            'bars': [],
            'error': 'get_market_data_failed',
        }


def _dataframe_to_records(df):
    """Convert a pandas DataFrame (or similar) to a list of dicts."""
    if df is None:
        return []
    iterrows = getattr(df, 'iterrows', None)
    if callable(iterrows):
        try:
            result = []
            for _, row in iterrows():
                record = row.to_dict() if hasattr(row, 'to_dict') else {}
                cleaned = {}
                for k, v in record.items():
                    cleaned[k] = normalize_number(v) if isinstance(v, (int, float)) or (hasattr(v, 'item') and callable(getattr(v, 'item'))) else v
                result.append(cleaned)
            return result
        except Exception:
            pass
    to_dict = getattr(df, 'to_dict', None)
    if callable(to_dict):
        try:
            return to_dict('records')
        except Exception:
            pass
    if isinstance(df, (list, tuple)):
        return list(df)
    return []


def build_longhubang_payload(runtime, symbol, start_time, end_time, record_error):
    context = runtime.context_ref
    if context is None:
        return {'symbol': symbol, 'records': [], 'error': 'context_unavailable'}
    get_longhubang = getattr(context, 'get_longhubang', None)
    if not callable(get_longhubang):
        return {'symbol': symbol, 'records': [], 'error': 'get_longhubang_unavailable'}
    try:
        result = get_longhubang([symbol], start_time, end_time)
        records = []
        rows = _dataframe_to_records(result)
        for row in rows:
            buy_booth = row.get('buyTraderBooth')
            sell_booth = row.get('sellTraderBooth')
            records.append({
                'reason': row.get('reason'),
                'close': normalize_number(row.get('close')),
                'spreadRate': normalize_number(row.get('spreadRate')),
                'turnoverVolume': normalize_number(row.get('TurnoverVolune')),
                'turnoverAmount': normalize_number(row.get('Turnover_Amount')),
                'buyTraderBooth': _dataframe_to_records(buy_booth),
                'sellTraderBooth': _dataframe_to_records(sell_booth),
            })
        return {'symbol': symbol, 'start': start_time, 'end': end_time, 'count': len(records), 'records': records}
    except Exception:
        record_error('build_longhubang_payload')
        return {'symbol': symbol, 'records': [], 'error': 'get_longhubang_failed'}


def build_signals_payload(runtime, symbol, normalize_quote_symbol):
    normalized_symbol = normalize_quote_symbol(symbol)
    points = []
    lowest_buy_price = None
    highest_buy_price = None
    for record in sorted(runtime.deal_index.values(), key=lambda item: str(item.get('time') or '')):
        if normalized_symbol is not None and record.get('symbol') != normalized_symbol:
            continue
        price = normalize_number(record.get('price'))
        side = record.get('side') or 'UNKNOWN'
        point = {
            'symbol': record.get('symbol'),
            'time': record.get('time'),
            'price': price,
            'volume': normalize_int(record.get('volume')),
            'side': side,
            'label': '买入' if side == 'BUY' else ('卖出' if side == 'SELL' else '成交'),
            'trade_id': record.get('trade_id'),
            'order_sys_id': record.get('order_sys_id'),
        }
        points.append(point)
        if side == 'BUY' and price is not None:
            if lowest_buy_price is None or price < lowest_buy_price:
                lowest_buy_price = price
            if highest_buy_price is None or price > highest_buy_price:
                highest_buy_price = price
    return {
        'symbol': normalized_symbol,
        'point_count': len(points),
        'lowest_buy_price': lowest_buy_price,
        'highest_buy_price': highest_buy_price,
        'points': points,
    }
