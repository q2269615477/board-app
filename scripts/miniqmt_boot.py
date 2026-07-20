#!/usr/bin/env python
import sys
sys.path.insert(0, r'D:\.workbuddy\2026-06-27-21-35-52\board-app')
from services.miniqmt_service import miniqmt_service

# 开机启动模式
miniqmt_service.set_mode('boot')
miniqmt_service.start()

# 保持运行
try:
    import time
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    miniqmt_service.stop()
