"""
午休数据缓存管理器
处理中午休息时间的临时数据存储和13:00切换逻辑
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from core.config import NOON_CACHE_DIR, NOON_CACHE_FILE_PATTERN

logger = logging.getLogger('noon_cache')


class NoonCacheManager:
    """午休数据缓存管理器"""
    
    def __init__(self):
        self._cache_dir = Path(NOON_CACHE_DIR)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = datetime.now().strftime('%Y%m%d')
    
    def _get_cache_file(self) -> Path:
        """获取今日缓存文件路径"""
        filename = NOON_CACHE_FILE_PATTERN.format(date=self._current_date)
        return self._cache_dir / filename
    
    def save_noon_data(self, data: Dict[str, Any]) -> bool:
        """
        保存午休数据到独立缓存文件
        
        Args:
            data: {code: {price, change_pct, volume, ...}}
        
        Returns:
            bool: 保存成功返回 True
        """
        try:
            cache_file = self._get_cache_file()
            cache_data = {
                'date': self._current_date,
                'timestamp': datetime.now().isoformat(),
                'data': data,
                'is_noon_data': True
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"[午休缓存] 已保存 {len(data)} 个标的到 {cache_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"[午休缓存] 保存失败: {e}")
            return False
    
    def load_noon_data(self) -> Dict[str, Any]:
        """
        加载午休缓存数据
        
        Returns:
            Dict: 缓存的数据，如果没有缓存返回空字典
        """
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return {}
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证日期匹配
            if cache_data.get('date') != self._current_date:
                logger.warning(f"[午休缓存] 日期不匹配，忽略缓存")
                return {}
            
            return cache_data.get('data', {})
            
        except Exception as e:
            logger.error(f"[午休缓存] 加载失败: {e}")
            return {}
    
    def clear_noon_cache(self) -> bool:
        """
        清空今日午休缓存
        
        Returns:
            bool: 清空成功返回 True
        """
        try:
            cache_file = self._get_cache_file()
            if cache_file.exists():
                cache_file.unlink()
                logger.info(f"[午休缓存] 已清空 {cache_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"[午休缓存] 清空失败: {e}")
            return False
    
    def is_noon_cache_valid(self) -> bool:
        """
        检查午休缓存是否有效（存在且日期匹配）
        
        Returns:
            bool: 缓存有效返回 True
        """
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return False
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            return cache_data.get('date') == self._current_date and 'data' in cache_data
            
        except Exception:
            return False
    
    def get_cache_metadata(self) -> Optional[Dict[str, Any]]:
        """
        获取缓存元数据（时间戳等）
        
        Returns:
            Dict: 包含 timestamp, date 等元数据
        """
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            return {
                'date': cache_data.get('date'),
                'timestamp': cache_data.get('timestamp'),
                'record_count': len(cache_data.get('data', {}))
            }
            
        except Exception:
            return None
