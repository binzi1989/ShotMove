"""讯飞 TTS 集成测试：需配置 IFLYTEK_APP_ID、IFLYTEK_API_KEY、IFLYTEK_API_SECRET 后运行。"""
import os
import shutil
import sys

# 确保 backend 根目录在 path 中并加载 .env
_backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)
os.chdir(_backend_root)

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(_backend_root) / ".env")

from app.services.iflytek_speech import text_to_speech

# 试听文件保存目录（与当前测试文件同目录）
TESTS_DIR = Path(__file__).resolve().parent
SAVED_MP3 = TESTS_DIR / "iflytek_tts_test.mp3"


def test_iflytek_tts_simple():
    """调用讯飞 TTS 合成一句短文本，校验返回 mp3 文件存在且非空，并保存到 tests 目录供试听。"""
    if not os.getenv("IFLYTEK_APP_ID") or not os.getenv("IFLYTEK_API_KEY") or not os.getenv("IFLYTEK_API_SECRET"):
        print("跳过：未配置 IFLYTEK_APP_ID / IFLYTEK_API_KEY / IFLYTEK_API_SECRET")
        return
    text = "你好，这是一段讯飞语音合成测试。"
    path, err = text_to_speech(text, voice_id="x6_lingyuyan_pro")
    assert err is None, f"TTS 失败: {err}"
    assert path is not None, "未返回路径"
    assert os.path.isfile(path), f"文件不存在: {path}"
    size = os.path.getsize(path)
    assert size > 0, "音频文件为空"
    # 复制到 tests 目录供试听
    shutil.copy2(path, SAVED_MP3)
    print(f"通过：合成成功，大小={size} 字节，已保存到 {SAVED_MP3}")
    try:
        os.unlink(path)
    except Exception:
        pass


if __name__ == "__main__":
    test_iflytek_tts_simple()
