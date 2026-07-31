#!/usr/bin/env python3
"""
Board-App MCP Server (Python实现)
作为Node.js版本的临时替代，使用Python标准库实现
"""

import sys
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MCP] %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger('mcp_server')

BOARD_APP_URL = 'http://127.0.0.1:5000'

# MCP协议常量
JSONRPC_VERSION = "2.0"
MCP_VERSION = "2024-11-05"


def send_request(path: str, data: Optional[Dict] = None) -> Dict:
    """发送HTTP请求到board-app"""
    url = f"{BOARD_APP_URL}{path}"
    try:
        if data:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
        else:
            req = urllib.request.Request(url)
        
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return {'success': False, 'error': str(e)}


def handle_initialize(params: Dict) -> Dict:
    """处理initialize请求"""
    return {
        "protocolVersion": MCP_VERSION,
        "capabilities": {
            "tools": {},
            "logging": {}
        },
        "serverInfo": {
            "name": "board-app-mcp",
            "version": "1.0.0"
        }
    }


def handle_tools_list(params: Dict) -> Dict:
    """处理tools/list请求"""
    result = send_request('/api/mcp/tools')
    tools = []
    
    if result.get('success'):
        for name, tool in result.get('tools', {}).items():
            tools.append({
                "name": name,
                "description": tool.get('description', ''),
                "inputSchema": {
                    "type": "object",
                    "properties": tool.get('parameters', {}),
                    "required": tool.get('required', [])
                }
            })
    
    return {"tools": tools}


def handle_tools_call(params: Dict) -> Dict:
    """处理tools/call请求"""
    tool_name = params.get('name', '')
    arguments = params.get('arguments', {})
    
    result = send_request('/api/mcp/call', {
        'tool': tool_name,
        'params': arguments
    })
    
    # 转换为MCP内容格式
    content = []
    if result.get('success'):
        content.append({
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False, indent=2)
        })
    else:
        content.append({
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False, indent=2)
        })
    
    return {
        "content": content,
        "isError": not result.get('success', False)
    }


# 请求处理器映射
HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def process_request(request: Dict) -> Optional[Dict]:
    """处理单个JSON-RPC请求"""
    method = request.get('method', '')
    params = request.get('params', {})
    req_id = request.get('id')
    
    handler = HANDLERS.get(method)
    if not handler:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }
    
    try:
        result = handler(params)
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "result": result
        }
    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }


def main():
    """主循环 - 从stdin读取，写入stdout"""
    logger.info("Board-App MCP Server (Python) v1.0.0")
    logger.info(f"Backend: {BOARD_APP_URL}")
    logger.info("Starting...")
    
    while True:
        try:
            # 读取一行
            line = sys.stdin.readline()
            if not line:
                break
            
            line = line.strip()
            if not line:
                continue
            
            # 解析请求
            request = json.loads(line)
            
            # 处理请求
            response = process_request(request)
            
            # 发送响应（如果有id）
            if response and request.get('id') is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error: {e}")
    
    logger.info("Server stopped")


if __name__ == '__main__':
    main()
