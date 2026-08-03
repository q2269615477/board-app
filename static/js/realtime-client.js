/**
 * RealtimeBus - 统一 WebSocket 实时数据总线
 * 提供全局单例连接，分发所有实时推送事件
 */
(function() {
  let ws = null;
  let reconnectTimer = null;
  let pingTimer = null;
  let retryDelay = 1000;
  const maxRetryDelay = 30000;
  const pingInterval = 18000; // 18秒心跳
  
  const status = {
    connected: false,
    connecting: false
  };

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      return;
    }
    
    status.connecting = true;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = protocol + '//' + window.location.host + '/ws/realtime';
    
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      console.error('[RealtimeBus] 创建连接失败', e);
      scheduleReconnect();
      return;
    }

    ws.onopen = function() {
      console.log('[RealtimeBus] WebSocket 已连接');
      status.connected = true;
      status.connecting = false;
      retryDelay = 1000;
      
      // 订阅默认通道
      send({
        action: 'subscribe',
        channels: ['indices', 'boards']
      });
      
      // 启动心跳
      startPing();

      // 广播连接状态：消费者（如指数栏）据此关闭 HTTP 降级轮询。
      // 此前无人派发状态事件，index-bar 的 on('status') 永远收不到，
      // 导致 WS 断开后指数价格永久停更。
      window.dispatchEvent(new CustomEvent('rt-status', {
        detail: { connected: true }
      }));
    };

    ws.onmessage = function(event) {
      try {
        const msg = JSON.parse(event.data);
        dispatchMessage(msg);
      } catch (e) {
        console.warn('[RealtimeBus] 解析消息失败', event.data, e);
      }
    };

    ws.onclose = function() {
      console.log('[RealtimeBus] WebSocket 已断开');
      status.connected = false;
      status.connecting = false;
      stopPing();
      window.dispatchEvent(new CustomEvent('rt-status', {
        detail: { connected: false }
      }));
      scheduleReconnect();
    };

    ws.onerror = function(error) {
      console.error('[RealtimeBus] 连接错误', error);
    };
  }

  function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  function scheduleReconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    // Add jitter: 0-500ms random delay to prevent thundering herd
    var jitter = Math.floor(Math.random() * 500);
    reconnectTimer = setTimeout(function() {
      console.log('[RealtimeBus] ' + (retryDelay/1000) + '秒后重连...');
      connect();
      // 指数退避
      retryDelay = Math.min(retryDelay * 2, maxRetryDelay);
    }, retryDelay + jitter);
  }

  function startPing() {
    stopPing();
    pingTimer = setInterval(function() {
      send({action: 'ping'});
    }, pingInterval);
  }

  function stopPing() {
    if (pingTimer) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  function close() {
    stopPing();
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    if (ws) {
      ws.close();
      ws = null;
    }
    status.connected = false;
    status.connecting = false;
  }

  function dispatchMessage(msg) {
    switch (msg.type) {
      case 'indices_update':
        window.dispatchEvent(new CustomEvent('rt-indices', {
          detail: msg,
          bubbles: true
        }));
        break;
      case 'board_changes_update':
        window.dispatchEvent(new CustomEvent('rt-board-changes', {
          detail: msg,
          bubbles: true
        }));
        break;
      case 'task_progress':
        window.dispatchEvent(new CustomEvent('rt-task', {
          detail: msg.data,
          bubbles: true
        }));
        break;
      case 'kline_ready':
        window.dispatchEvent(new CustomEvent('rt-kline-ready', {
          detail: msg.data,
          bubbles: true
        }));
        break;
      case 'pong':
        // 心跳响应，无需处理
        break;
      default:
        console.debug('[RealtimeBus] 未知消息类型', msg.type);
    }
  }

  // 事件监听帮助方法
  function on(eventName, handler) {
    window.addEventListener(eventName, handler);
  }

  function off(eventName, handler) {
    window.removeEventListener(eventName, handler);
  }

  // 页面关闭时清理
  window.addEventListener('beforeunload', function() {
    close();
  });

  // 自动连接
  if (document.readyState === 'complete') {
    connect();
  } else {
    window.addEventListener('load', connect);
  }

  // 导出全局单例
  window.RealtimeBus = {
    isConnected: function() { return status.connected; },
    status: status,
    connect: connect,
    close: close,
    on: on,
    off: off,
    send: send
  };

})();
