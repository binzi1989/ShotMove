#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
先按「真实 TTS 时长」裁镜头再拼接，再复用同一批 TTS 做配音并烧字幕，保证每句读完整再切。

流程：
  1. 按分镜生成 TTS，得到每镜真实时长 -> target_durations
  2. 用 target_durations 裁切/补齐素材段，拼接成片
  3. 用预生成的 TTS + target_durations 混入配音（不重算、不截断）
  4. 烧录字幕（与 target_durations 对齐）

用法：
  cd backend
  python scripts/regenerate_with_tts_first.py --segment-dir static/merged/segments/47595aeecb744e7db3c4deab40c1666c --storyboard scripts/fixtures/storyboard_47595_full.json --out 47595_mengchuan_vo_cap
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

MERGED_DIR = BACKEND / "static" / "merged"
SEGMENTS_BACKUP_DIR = MERGED_DIR / "segments"

# 孟川/众弟子=男声，绿竹=女声（与 7 镜顺序一致）
DEFAULT_SHOT_VOICE_IDS = [
    "male-qn-jingying",
    "female-yujie",
    "male-qn-jingying",
    "female-yujie",
    "male-qn-jingying",
    "female-yujie",
    "male-qn-jingying",
]


def load_storyboard(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "storyboard" in data:
        return data["storyboard"]
    return []


def main():
    parser = argparse.ArgumentParser(description="先 TTS 定时长再裁片，配音+字幕，句读完再切")
    parser.add_argument("--segment-dir", required=True, help="已下载分段目录，如 static/merged/segments/47595aeecb744e7db3c4deab40c1666c")
    parser.add_argument("--storyboard", required=True, help="分镜 JSON（每项含 copy）")
    parser.add_argument("--out", default=None, help="最终成片文件名（不含 .mp4）")
    parser.add_argument("--script-summary", default="", help="剧本摘要")
    parser.add_argument("--base-name", default="tts_aligned", help="中间成片文件名前缀（不含 .mp4）")
    args = parser.parse_args()

    segment_dir = Path(args.segment_dir)
    if not segment_dir.is_absolute():
        segment_dir = (BACKEND / segment_dir).resolve()
    if not segment_dir.is_dir():
        print(f"错误：目录不存在 {segment_dir}")
        sys.exit(1)

    segment_files = sorted(segment_dir.glob("seg_*.mp4"), key=lambda p: p.name)
    if not segment_files:
        print(f"错误：目录下没有 seg_*.mp4")
        sys.exit(1)

    storyboard = load_storyboard(args.storyboard)
    if not storyboard:
        print("错误：分镜为空或格式不对")
        sys.exit(1)
    # 只保留与素材段数一致的分镜
    storyboard = storyboard[: len(segment_files)]
    while len(storyboard) < len(segment_files):
        storyboard.append({"copy": "", "duration_sec": 3})

    shot_voice_ids = DEFAULT_SHOT_VOICE_IDS if len(DEFAULT_SHOT_VOICE_IDS) == len(storyboard) else None

    from app.main import (
        _build_drama_tts_and_target_durations,
        _add_bgm_and_voiceover,
        _postprocess_visuals,
    )
    from app.services.video_concat import (
        retime_local_segments_to_durations,
        concat_local_segments,
        MERGED_DIR,
    )

    # 1) 先生成 TTS，得到真实时长
    print("1/4 按分镜生成 TTS，计算每镜真实时长…")
    prebuilt_tts_paths, target_durations, _ = _build_drama_tts_and_target_durations(
        storyboard,
        character_references=None,
        voice_id=None,
        shot_voice_ids=shot_voice_ids,
        script_summary=args.script_summary or "",
    )
    if not target_durations or len(target_durations) != len(segment_files):
        print("错误：TTS/时长生成失败或镜数不一致")
        sys.exit(1)
    print(f"    目标时长(秒): {[round(t, 2) for t in target_durations]}, 总长 {sum(target_durations):.2f}s")

    # 2) 按目标时长裁切镜头并拼接
    print("2/4 按 TTS 时长裁切镜头并拼接…")
    retimed = retime_local_segments_to_durations(segment_files, target_durations)
    merged_url = concat_local_segments(retimed, with_transitions=False)
    if not merged_url:
        print("错误：拼接失败")
        sys.exit(1)
    base_name = (args.base_name or "tts_aligned").strip()
    merged_name = f"{base_name}.mp4"
    actual_path = MERGED_DIR / merged_url.replace("/api/merged/", "").strip("/")
    if actual_path.is_file():
        shutil.copy2(actual_path, MERGED_DIR / merged_name)
    merged_url = f"/api/merged/{merged_name}"
    print(f"    成片(无配音): {MERGED_DIR / merged_name}")

    # 3) 复用同一批 TTS 混入配音（不重算、不截断）
    print("3/4 复用 TTS 混入配音…")
    merged_url, voiceover_url, _ = _add_bgm_and_voiceover(
        merged_url,
        args.script_summary or "",
        with_bgm=False,
        with_voiceover=True,
        pipeline="script_drama",
        storyboard=storyboard,
        segment_durations=target_durations,
        shot_voice_ids=shot_voice_ids,
        prebuilt_tts_paths=prebuilt_tts_paths,
        script_summary_for_emotion=args.script_summary or "",
        voice_align_mode="pad_trim",
    )
    print(f"    配音成片: {merged_url}")

    # 4) 烧录字幕
    print("4/4 烧录字幕…")
    merged_url = _postprocess_visuals(
        merged_url,
        storyboard,
        "script_drama",
        with_captions=True,
        with_stickers=False,
        subtitle_style="clean",
        segment_durations=target_durations,
    )
    if not merged_url:
        print("字幕烧录失败")
        sys.exit(1)

    final_name = merged_url.replace("/api/merged/", "").strip("/")
    out_path = MERGED_DIR / final_name
    print(f"\n完成。成片（配音+字幕，句读完再切）: {out_path}")

    if args.out:
        dest = MERGED_DIR / f"{args.out.strip()}.mp4"
        if dest.resolve() != out_path.resolve():
            shutil.copy2(out_path, dest)
            print(f"已另存为: {dest}")


if __name__ == "__main__":
    main()
