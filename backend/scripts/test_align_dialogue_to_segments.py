#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试脚本：先计算台词可能的时间长度 → 按该时长设置镜头 → 剪辑后与配音完全匹配。

流程：
  1. 计算每镜台词时长（估算或 TTS 实际）
  2. 目标镜头时长 = 台词时长 + tail_pad，且 >= min_shot
  3. 对已下载的 seg_*.mp4 按目标时长裁切/补齐（retime）
  4. 拼接成片，成片总长 = 各镜台词总长，后续加配音即可完全对齐

用法（使用已下载的素材目录）：
  cd backend
  python scripts/test_align_dialogue_to_segments.py --segment-dir static/merged/segments/640c66a722f54176b42c11e21b10fd31

可选：
  --storyboard <json>  分镜 JSON（每项含 copy 或 copy_text），不传则用内置 7 镜示例
  --use-tts            用真实 TTS 计算台词时长（否则仅用字数估算）
  --tail-pad 0.25      每镜台词后预留秒数
  --min-shot 1.0       每镜最小时长（秒）
  --out <basename>     输出文件名（不含 .mp4），会保存到 static/merged/<basename>.mp4
"""
import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

# 项目根目录
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# 仅脚本内使用的估算函数，避免依赖 main 的完整环境
def _estimate_dialogue_duration_sec(text: str) -> float:
    """估算台词朗读所需时长（秒）。中文约 4.5 字/秒，英文约 2.8 词/秒，标点加停顿。"""
    if not text or not str(text).strip():
        return 0.0
    t = " ".join(str(text).split()).strip()
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", t))
    en_words = len(re.findall(r"[A-Za-z0-9]+", t))
    punct = len(re.findall(r"[，,。.!！？?；;：:、】【「」""\"'…—-]", t))
    zh_rate = float(os.getenv("DRAMA_SPEECH_ZH_CHARS_PER_SEC", "4.5") or 4.5)
    en_rate = float(os.getenv("DRAMA_SPEECH_EN_WORDS_PER_SEC", "2.8") or 2.8)
    pause = float(os.getenv("DRAMA_SPEECH_PUNCT_PAUSE_SEC", "0.10") or 0.10)
    base = (zh_chars / max(1e-3, zh_rate)) + (en_words / max(1e-3, en_rate))
    return max(0.8, base + punct * pause)


def _ffprobe_duration_sec(path: str | Path) -> float | None:
    """返回视频/音频时长（秒）。"""
    import subprocess
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0:
            return None
        s = (r.stdout or "").strip()
        return float(s) if s else None
    except Exception:
        return None


def _default_storyboard(num_shots: int) -> list[dict]:
    """内置示例分镜（与常见 7 镜素材对齐）。"""
    lines = [
        "你好，欢迎来到直播间。",
        "今天给大家推荐这款产品，性价比很高。",
        "大家看，这个设计非常实用。",
        "有需要的朋友可以点击下方链接。",
        "我们还有优惠券可以领取。",
        "感谢大家的支持，我们下期再见。",
        "记得关注不迷路。",
    ]
    return [{"copy": lines[i % len(lines)]} for i in range(num_shots)]


def _load_storyboard(path: str | Path) -> list[dict]:
    """从 JSON 加载分镜，每项需有 copy 或 copy_text。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        data = [data]
    return data


def _copy_from_shot(shot: dict) -> str:
    return (shot.get("copy_text") or shot.get("copy") or "").strip()


def main() -> None:
    # Windows 控制台 UTF-8 输出
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="按台词时长对齐镜头并剪辑（测试脚本）")
    parser.add_argument("--segment-dir", required=True, help="已下载的分段视频目录，如 static/merged/segments/640c66a722f54176b42c11e21b10fd31")
    parser.add_argument("--storyboard", default=None, help="分镜 JSON 文件路径（每项含 copy 或 copy_text）")
    parser.add_argument("--use-tts", action="store_true", help="使用真实 TTS 计算台词时长（否则仅估算）")
    parser.add_argument("--tail-pad", type=float, default=0.25, help="每镜台词后预留秒数")
    parser.add_argument("--min-shot", type=float, default=1.0, help="每镜最小时长（秒）")
    parser.add_argument("--out", default=None, help="输出成片文件名前缀（不含 .mp4）")
    args = parser.parse_args()

    segment_dir = Path(args.segment_dir)
    if not segment_dir.is_absolute():
        segment_dir = (BACKEND_ROOT / segment_dir).resolve()
    if not segment_dir.is_dir():
        print(f"错误：目录不存在: {segment_dir}", file=sys.stderr)
        sys.exit(1)

    # 已下载的素材：seg_000.mp4, seg_001.mp4, ...
    segment_files = sorted(segment_dir.glob("seg_*.mp4"), key=lambda p: p.name)
    if not segment_files:
        print(f"错误：目录下没有 seg_*.mp4: {segment_dir}", file=sys.stderr)
        sys.exit(1)

    num_segments = len(segment_files)
    if args.storyboard:
        storyboard = _load_storyboard(args.storyboard)[:num_segments]
        while len(storyboard) < num_segments:
            storyboard.append({"copy": "（无台词）"})
    else:
        storyboard = _default_storyboard(num_segments)

    # Step 1: 计算每镜台词时长
    if args.use_tts:
        from app.schemas import StoryboardItem
        from app.agents.video_generation import _calculate_actual_dialogue_durations
        # 构造 StoryboardItem 列表（仅需 copy）
        sb_items = []
        for i, s in enumerate(storyboard):
            copy = _copy_from_shot(s)
            sb_items.append(StoryboardItem(index=i, shot_desc="", copy_text=copy))
        dialogue_durations = _calculate_actual_dialogue_durations(sb_items)
        print("使用 TTS 实际时长计算台词长度")
    else:
        dialogue_durations = [_estimate_dialogue_duration_sec(_copy_from_shot(s)) for s in storyboard]
        print("使用字数估算台词长度")

    # 目标镜头时长 = 台词时长 + 尾缓冲，且不小于 min_shot
    tail_pad = max(0.0, args.tail_pad)
    min_shot = max(0.5, args.min_shot)
    target_durations = [max(min_shot, d + tail_pad) for d in dialogue_durations]

    # 原始每段时长（用于报告）
    original_durations = [_ffprobe_duration_sec(p) or 0.0 for p in segment_files]

    print("\n--- 台词时长 → 目标镜头时长 ---")
    print(f"{'镜号':<4} {'台词(前30字)':<32} {'估算(秒)':<10} {'目标(秒)':<10} {'原段(秒)':<10}")
    print("-" * 72)
    for i in range(num_segments):
        copy = _copy_from_shot(storyboard[i])
        short = (copy[:30] + "…") if len(copy) > 30 else copy
        print(f"{i:<4} {short:<32} {dialogue_durations[i]:<10.2f} {target_durations[i]:<10.2f} {original_durations[i]:<10.2f}")
    print("-" * 72)
    print(f"合计: 台词总长(估算)={sum(dialogue_durations):.2f}s, 目标总长={sum(target_durations):.2f}s, 原素材总长={sum(original_durations):.2f}s\n")

    # Step 2 & 3: 按目标时长裁切/补齐镜头，再拼接
    from app.services.video_concat import (
        retime_local_segments_to_durations,
        concat_local_segments,
        MERGED_DIR,
    )

    retimed_paths = retime_local_segments_to_durations(segment_files, target_durations)
    retimed_durations = [_ffprobe_duration_sec(p) or 0.0 for p in retimed_paths]

    # 输出到 merged 目录；若指定 --out 则写固定名便于对比
    if args.out:
        out_name = f"{args.out.strip()}.mp4"
        out_path = MERGED_DIR / out_name
        # concat_local_segments 返回的是 API 路径且用 uuid 命名，我们这里先按标准流程得到 merged，再复制/重命名；或直接自己写 concat list 到 out_path
        # 为简单起见：先照常 concat 得到一条，再复制为 out_name；但 concat_local_segments 内部用 uuid，无法指定输出名。
        # 改为：先 concat 得到 merged_url，然后读取该文件并复制到 out_path。需要拿到实际路径。
        merged_url = concat_local_segments(retimed_paths)
        if merged_url:
            # merged_url 形如 /api/merged/xxx.mp4，实际文件在 MERGED_DIR/xxx.mp4
            actual_name = merged_url.replace("/api/merged/", "").strip("/")
            actual_path = MERGED_DIR / actual_name
            if actual_path.exists():
                import shutil
                shutil.copy2(actual_path, MERGED_DIR / out_name)
                print(f"已另存为: {MERGED_DIR / out_name}")
            print(f"成片(内部): {actual_path}")
        else:
            print("拼接失败", file=sys.stderr)
            sys.exit(1)
    else:
        merged_url = concat_local_segments(retimed_paths)
        if not merged_url:
            print("拼接失败", file=sys.stderr)
            sys.exit(1)
        # 实际路径
        actual_name = merged_url.replace("/api/merged/", "").strip("/")
        actual_path = MERGED_DIR / actual_name
        print(f"成片: {actual_path}")
        print(f"API 路径: {merged_url}")

    print("\n--- 调整后每段时长 ---")
    for i, d in enumerate(retimed_durations):
        print(f"  镜 {i}: {d:.2f}s (目标 {target_durations[i]:.2f}s)")
    total_retimed = sum(retimed_durations)
    print(f"  总时长: {total_retimed:.2f}s（与目标一致则剪辑完全匹配配音）\n")


if __name__ == "__main__":
    main()
