#!/usr/bin/env python3
"""
测试台词时长计算和镜头匹配功能
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import StoryboardItem
from app.agents.video_generation import _calculate_actual_dialogue_durations
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


def test_storyboard_duration_calculation():
    """测试分镜台词时长计算"""
    print("\n测试分镜台词时长计算...")
    # 创建测试分镜
    storyboard = [
        StoryboardItem(
            shot_desc="角色A在办公室说话",
            copy_text="你好，我是角色A，这是我的第一句台词。",
            shot_type="中景",
            camera_technique="固定"
        ),
        StoryboardItem(
            shot_desc="角色B在会议室回应",
            copy_text="你好，角色A，我是角色B，很高兴见到你。",
            shot_type="近景",
            camera_technique="固定"
        ),
        StoryboardItem(
            shot_desc="角色A和角色B一起走出办公室",
            copy_text="我们一起去吃午饭吧！",
            shot_type="全景",
            camera_technique="跟随"
        )
    ]
    
    # 计算时长
    durations = _calculate_actual_dialogue_durations(storyboard)
    print(f"分镜台词时长: {[round(d, 2) for d in durations]}")
    
    # 验证时长是否被正确设置
    for i, shot in enumerate(storyboard):
        if hasattr(shot, "duration_sec"):
            print(f"分镜 {i+1} 时长: {shot.duration_sec:.2f} 秒")
        else:
            print(f"分镜 {i+1} 未设置时长")


if __name__ == "__main__":
    print("开始测试台词时长计算和镜头匹配功能...")
    
    # 测试文本转语音时长
    test_text_to_speech_duration()
    
    # 测试分镜时长计算
    test_storyboard_duration_calculation()
    
    print("\n测试完成！")
