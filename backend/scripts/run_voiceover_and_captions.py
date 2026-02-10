#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对已有成片先加配音、再烧录字幕，出一版「配音+字幕」成片。

用法（需在 backend 目录执行，且后端 TTS 等环境已配置）：
  cd backend
  python scripts/run_voiceover_and_captions.py --merged 47595_mengchuan_by_copy.mp4 --storyboard scripts/fixtures/storyboard_47595_full.json

可选：
  --out <basename>  最终输出文件名（不含 .mp4），默认在原名基础上加 _vo_cap
  --url <base>      若通过 HTTP 调用后端配音，此处填 base URL（默认不通过 HTTP，直接调 app 函数）
"""
import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

MERGED_DIR = BACKEND / "static" / "merged"

# 孟川/众弟子=男声，绿竹=女声（与 7 镜顺序一致）
DEFAULT_SHOT_VOICE_IDS = [
    "male-qn-jingying",  # 众弟子
    "female-yujie",      # 绿竹
    "male-qn-jingying",  # 孟川
    "female-yujie",      # 绿竹
    "male-qn-jingying",  # 孟川
    "female-yujie",      # 绿竹
    "male-qn-jingying",  # 孟川
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
    parser = argparse.ArgumentParser(description="成片加配音并烧录字幕")
    parser.add_argument("--merged", required=True, help="成片文件名，如 47595_mengchuan_by_copy.mp4")
    parser.add_argument("--storyboard", required=True, help="分镜 JSON（每项含 copy 与 duration_sec）")
    parser.add_argument("--out", default=None, help="最终输出文件名（不含 .mp4）")
    parser.add_argument("--script-summary", default="", help="剧本摘要，供情绪推断")
    args = parser.parse_args()

    merged_name = args.merged.strip()
    if not merged_name.endswith(".mp4"):
        merged_name = f"{merged_name}.mp4"
    video_path = MERGED_DIR / merged_name
    if not video_path.is_file():
        print(f"错误：成片不存在 {video_path}")
        sys.exit(1)

    storyboard = load_storyboard(args.storyboard)
    if not storyboard:
        print("错误：分镜为空或格式不对")
        sys.exit(1)

    # 从分镜取每镜时长，与成片一致才能对齐
    segment_durations = []
    for s in storyboard:
        d = s.get("duration_sec")
        try:
            segment_durations.append(max(1.0, min(10.0, float(d or 5))))
        except (TypeError, ValueError):
            segment_durations.append(5.0)
    if len(segment_durations) != len(storyboard):
        segment_durations = segment_durations[: len(storyboard)]
    while len(segment_durations) < len(storyboard):
        segment_durations.append(5.0)

    merged_url = f"/api/merged/{merged_name}"
    shot_voice_ids = DEFAULT_SHOT_VOICE_IDS if len(DEFAULT_SHOT_VOICE_IDS) == len(storyboard) else None

    # 1) 配音
    from app.main import _add_bgm_and_voiceover, _postprocess_visuals

    print("正在生成配音并混入成片…")
    merged_url, voiceover_url, _ = _add_bgm_and_voiceover(
        merged_url,
        args.script_summary or "",
        with_bgm=False,
        with_voiceover=True,
        pipeline="script_drama",
        storyboard=storyboard,
        segment_durations=segment_durations,
        shot_voice_ids=shot_voice_ids,
        script_summary_for_emotion=args.script_summary or "",
        voice_align_mode="pad_trim",
    )
    if not merged_url or merged_url == f"/api/merged/{merged_name}":
        print("警告：配音可能未生成新文件，继续尝试烧录字幕")
    else:
        print(f"  配音成片: {merged_url}")

    # 2) 字幕（在配音成片上烧录，与 segment_durations 对齐）
    print("正在烧录字幕…")
    merged_url = _postprocess_visuals(
        merged_url,
        storyboard,
        "script_drama",
        with_captions=True,
        with_stickers=False,
        subtitle_style="clean",
        segment_durations=segment_durations,
    )
    if not merged_url:
        print("字幕烧录失败")
        sys.exit(1)

    # 输出路径：一般为 xxx_vo_cap.mp4
    name = merged_url.replace("/api/merged/", "").strip("/")
    out_path = MERGED_DIR / name
    print(f"\n完成。成片（配音+字幕）: {out_path}")

    if args.out:
        dest = MERGED_DIR / f"{args.out.strip()}.mp4"
        if dest.resolve() != out_path.resolve():
            import shutil
            shutil.copy2(out_path, dest)
            print(f"已另存为: {dest}")


if __name__ == "__main__":
    main()
