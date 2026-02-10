"""通过运行此文件启动后端服务（在 backend 目录下执行：python run.py）"""
import logging
import os
import sys

# 确保以 backend 为当前目录，便于找到 app 包
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# 让 app.main 里的 logger.info 能打出来（uvicorn 默认会看到 INFO）
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
