"""从 segments 目录拼接并配音的简化脚本"""
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.services.ffmpeg import concat_local_segments, build_voice_track_from_segments
from app.services import volcano_speech
from app.services.llm import infer_voice_for_drama_line

MERGED_DIR = BACKEND / "static" / "merged"
SEGMENT_DIR = MERGED_DIR / "segments" / "05a01ce70a1f47a1a5d0286c3e4227dc"

storyboard_path = "scripts/fixtures/storyboard_05a01ce7.json"
with open(storyboard_path, "r", encoding="utf-8") as f:
    storyboard = json.load(f)

print(f"加载分镜: {len(storyboard)} 镜")

# 收集视频片段
local_paths = []
for i in range(10):
    seg_path = SEGMENT_DIR / f"seg_{i:03d}.mp4"
    if seg_path.is_file():
        local_paths.append(str(seg_path))
        print(f"  片段 {i}: {seg_path.name}")
    else:
        break

print(f"\n找到 {len(local_paths)} 个视频片段")

# 拼接视频
print("\n拼接视频...")
merged_path = MERGED_DIR / "05a01ce70a1f47a1a5d0286c3e4227dc.mp4"
merged_url = concat_local_segments(local_paths, with_transitions=True, output_path=str(merged_path))
if merged_url:
    print(f"拼接完成: {merged_path.name}")
else:
    print("拼接失败!")
    sys.exit(1)

# 生成配音
print("\n生成配音...")
segments = []
voice_cache = {}

for i, shot in enumerate(storyboard):
    if i >= len(local_paths):
        break
    
    copy = shot.get("copy", "") or ""
    duration = shot.get("duration_sec", 5)
    
    if not copy.strip():
        segments.append((None, duration))
        continue
    
    # 获取角色名
    char_name = shot.get("character_name", "") or ""
    
    # 选择音色
    voice_id = infer_voice_for_drama_line(
        copy,
        character_name=char_name,
        voice_cache=voice_cache,
    )
    print(f"  镜 {i+1}: 角色={char_name or '?'} 台词={copy[:30]}... 音色={voice_id}")
    
    # 生成 TTS
    try:
        result = volcano_speech.text_to_speech(copy, voice_id=voice_id)
        if result and result[0]:
            tts_path = result[0]
            if os.path.isfile(tts_path):
                segments.append((tts_path, duration))
                continue
    except Exception as e:
        print(f"    TTS 失败: {e}")
    
    segments.append((None, duration))

# 生成配音音轨
print("\n生成配音音轨...")
voice_dest = MERGED_DIR / "05a01ce70a1f47a1a5d0286c3e4227dc_voice.mp3"
voice_path = build_voice_track_from_segments(segments, voice_dest, align_mode="pad_trim")

if voice_path:
    print(f"配音完成: {Path(voice_path).name}")
else:
    print("配音生成失败!")

print("\n完成!")
print(f"  视频: {merged_path.name}")
print(f"  配音: {Path(voice_path).name if voice_path else '失败'}")
