#!/usr/bin/env python3
"""
测试语音时长计算功能
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.volcano_speech import text_to_speech, get_audio_duration


def test_text_to_speech_duration():
    """测试文本转语音并计算时长"""
    print("测试文本转语音时长计算...")
    test_texts = [
        "你好，这是一段测试台词。",
        "今天天气真好，我们一起去公园吧！",
        "这是一段比较长的测试台词，用于验证时长计算的准确性。",
    ]
    
    for i, text in enumerate(test_texts):
        print(f"\n测试文本 {i+1}: {text}")
        audio_path, error, duration = text_to_speech(text)
        if error:
            print(f"错误: {error}")
        else:
            print(f"计算时长: {duration:.2f} 秒")
            # 验证音频文件存在并再次计算时长
            if audio_path and os.path.exists(audio_path):
                actual_duration = get_audio_duration(audio_path)
                print(f"实际音频时长: {actual_duration:.2f} 秒")
                # 清理临时文件
                try:
                    os.unlink(audio_path)
                except Exception as e:
                    print(f"清理文件失败: {e}")


if __name__ == "__main__":
    print("开始测试语音时长计算功能...")
    test_text_to_speech_duration()
    print("\n测试完成！")
