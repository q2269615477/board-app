"""
画线状态管理服务
管理图表画线的持久化、同步和查询
"""

import json
import os
import time
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger('overlay_service')


class OverlayService:
    """画线状态管理服务"""
    
    def __init__(self, storage_dir: str = None):
        """
        初始化画线服务
        
        Args:
            storage_dir: 画线数据存储目录
        """
        if storage_dir is None:
            # 默认存储在 logs/drawings 目录
            base_dir = os.path.dirname(os.path.dirname(__file__))
            storage_dir = os.path.join(base_dir, 'logs', 'drawings')
        
        self.storage_dir = storage_dir
        self._ensure_storage_dir()
        
        # 内存缓存：symbol -> overlays
        self._cache: Dict[str, List[Dict]] = {}
        
        # 加载所有已保存的画线
        self._load_all()
    
    def _ensure_storage_dir(self):
        """确保存储目录存在"""
        os.makedirs(self.storage_dir, exist_ok=True)
    
    def _get_file_path(self, symbol: str) -> str:
        """获取画线文件路径"""
        # 清理文件名中的非法字符
        safe_symbol = symbol.replace('/', '_').replace('\\', '_')
        return os.path.join(self.storage_dir, f"{safe_symbol}_overlays.json")
    
    def _load_all(self):
        """加载所有画线数据"""
        try:
            for filename in os.listdir(self.storage_dir):
                if filename.endswith('_overlays.json'):
                    symbol = filename.replace('_overlays.json', '')
                    filepath = os.path.join(self.storage_dir, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            self._cache[symbol] = json.load(f)
                    except Exception as e:
                        logger.error(f"加载画线失败 {symbol}: {e}")
                        self._cache[symbol] = []
            logger.info(f"[OverlayService] 已加载 {len(self._cache)} 个标的的画线数据")
        except Exception as e:
            logger.error(f"[OverlayService] 加载所有画线失败: {e}")
    
    def _save_symbol(self, symbol: str):
        """保存指定标的的画线"""
        try:
            filepath = self._get_file_path(symbol)
            overlays = self._cache.get(symbol, [])
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(overlays, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[OverlayService] 保存画线失败 {symbol}: {e}")
    
    # ============================================================
    # 公开API
    # ============================================================
    
    def get_overlays(self, symbol: str) -> List[Dict]:
        """
        获取指定标的的所有画线
        
        Args:
            symbol: 标的代码
            
        Returns:
            画线列表
        """
        return self._cache.get(symbol, []).copy()
    
    def get_overlay(self, symbol: str, overlay_id: str) -> Optional[Dict]:
        """
        获取指定画线
        
        Args:
            symbol: 标的代码
            overlay_id: 画线ID
            
        Returns:
            画线数据或None
        """
        overlays = self._cache.get(symbol, [])
        for overlay in overlays:
            if overlay.get('id') == overlay_id:
                return overlay.copy()
        return None
    
    def create_overlay(self, symbol: str, overlay_data: Dict) -> Dict:
        """
        创建画线
        
        Args:
            symbol: 标的代码
            overlay_data: 画线数据（需包含 type, points）
            
        Returns:
            创建的画线（包含生成的id）
        """
        # 生成唯一ID
        overlay_id = f"ov_{int(time.time() * 1000)}_{len(self._cache.get(symbol, []))}"
        
        overlay = {
            'id': overlay_id,
            'type': overlay_data.get('type', 'horizontalLine'),
            'points': overlay_data.get('points', []),
            'styles': overlay_data.get('styles', {}),
            'extendData': overlay_data.get('extendData', {}),
            'visible': overlay_data.get('visible', True),
            'locked': overlay_data.get('locked', False),
            'createdAt': int(time.time() * 1000),
            'updatedAt': int(time.time() * 1000)
        }
        
        # 添加到缓存
        if symbol not in self._cache:
            self._cache[symbol] = []
        self._cache[symbol].append(overlay)
        
        # 持久化
        self._save_symbol(symbol)
        
        logger.info(f"[OverlayService] 创建画线: {symbol}/{overlay_id} ({overlay['type']})")
        
        return overlay.copy()
    
    def update_overlay(self, symbol: str, overlay_id: str, updates: Dict) -> Optional[Dict]:
        """
        更新画线
        
        Args:
            symbol: 标的代码
            overlay_id: 画线ID
            updates: 更新内容
            
        Returns:
            更新后的画线或None
        """
        overlays = self._cache.get(symbol, [])
        for i, overlay in enumerate(overlays):
            if overlay.get('id') == overlay_id:
                # 更新字段
                allowed_fields = ['points', 'styles', 'extendData', 'visible', 'locked']
                for field in allowed_fields:
                    if field in updates:
                        overlay[field] = updates[field]
                
                overlay['updatedAt'] = int(time.time() * 1000)
                
                # 持久化
                self._save_symbol(symbol)
                
                logger.info(f"[OverlayService] 更新画线: {symbol}/{overlay_id}")
                
                return overlay.copy()
        
        return None
    
    def delete_overlay(self, symbol: str, overlay_id: str) -> bool:
        """
        删除画线
        
        Args:
            symbol: 标的代码
            overlay_id: 画线ID
            
        Returns:
            是否成功删除
        """
        overlays = self._cache.get(symbol, [])
        original_len = len(overlays)
        
        self._cache[symbol] = [
            o for o in overlays 
            if o.get('id') != overlay_id
        ]
        
        deleted = len(self._cache[symbol]) < original_len
        
        if deleted:
            self._save_symbol(symbol)
            logger.info(f"[OverlayService] 删除画线: {symbol}/{overlay_id}")
        
        return deleted
    
    def sync_overlays(self, symbol: str, overlays: List[Dict]) -> Dict:
        """
        同步画线状态（前端上报完整状态）
        
        Args:
            symbol: 标的代码
            overlays: 完整的画线列表
            
        Returns:
            同步结果
        """
        # 为没有id的画线生成id
        processed = []
        for overlay in overlays:
            if 'id' not in overlay or not overlay['id']:
                overlay['id'] = f"ov_{int(time.time() * 1000)}_{len(processed)}"
            if 'createdAt' not in overlay:
                overlay['createdAt'] = int(time.time() * 1000)
            overlay['updatedAt'] = int(time.time() * 1000)
            processed.append(overlay)
        
        # 更新缓存
        self._cache[symbol] = processed
        
        # 持久化
        self._save_symbol(symbol)
        
        logger.info(f"[OverlayService] 同步画线: {symbol}, {len(processed)}条")
        
        return {
            'success': True,
            'symbol': symbol,
            'count': len(processed),
            'overlays': processed
        }
    
    def clear_overlays(self, symbol: str) -> bool:
        """
        清空指定标的的所有画线
        
        Args:
            symbol: 标的代码
            
        Returns:
            是否成功
        """
        if symbol in self._cache:
            self._cache[symbol] = []
            self._save_symbol(symbol)
            logger.info(f"[OverlayService] 清空画线: {symbol}")
            return True
        return False
    
    def get_all_symbols(self) -> List[str]:
        """获取所有有画线的标的"""
        return list(self._cache.keys())
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        total_overlays = sum(len(v) for v in self._cache.values())
        return {
            'symbols': len(self._cache),
            'total_overlays': total_overlays,
            'storage_dir': self.storage_dir
        }


# 全局实例
_overlay_service: Optional[OverlayService] = None


def get_overlay_service() -> OverlayService:
    """获取画线服务实例（单例）"""
    global _overlay_service
    if _overlay_service is None:
        _overlay_service = OverlayService()
    return _overlay_service
