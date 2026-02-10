"""使用 segments 目录重新剪辑（拼接 + 字幕 + 配音）。
用法：
  python scripts/remix_from_segments.py --job-id 05a01ce70a1f47a1a5d0286c3e4227dc
  python scripts/remix_from_segments.py --job-id 05a01ce70a1f47a1a5d0286c3e4227dc --with-voiceover
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

import httpx

BASE_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
MERGED_DIR = BACKEND / "static" / "merged"

def build_segment_urls(job_id: str, base_url: str) -> list[str]:
    """构建 seg_xxx.mp4 文件的 URL 列表"""
    base_url = base_url.rstrip("/")
    urls = []
    for i in range(20):  # 最多 20 个片段
        local_path = MERGED_DIR / "segments" / job_id / f"seg_{i:03d}.mp4"
        if local_path.is_file():
            url = f"{base_url}/api/merged/segments/{job_id}/seg_{i:03d}.mp4"
            urls.append(url)
        else:
            break
    return urls

def main():
    parser = argparse.ArgumentParser(description="使用 segments 目录重新剪辑（拼接+字幕+配音）")
    parser.add_argument("--job-id", required=True, help="segments 目录名，如 05a01ce70a1f47a1a5d0286c3e4227dc")
    parser.add_argument("--storyboard-json", default=None, help="分镜 JSON 文件路径，默认用 fixtures/storyboard_{job_id[:8]}.json")
    parser.add_argument("--with-transitions", action="store_true", default=True, help="添加转场")
    parser.add_argument("--with-captions", action="store_true", default=True, help="添加字幕")
    parser.add_argument("--with-voiceover", action="store_true", help="添加配音")
    parser.add_argument("--url", default=BASE_URL, help="后端 base URL")
    args = parser.parse_args()
    
    # 构建 segment URLs
    segment_urls = build_segment_urls(args.job_id, args.url)
    if not segment_urls:
        print("错误: 找不到 seg_xxx.mp4 文件")
        sys.exit(1)
    print(f"找到 {len(segment_urls)} 个视频片段:")
    for i, url in enumerate(segment_urls):
        print(f"  {i+1}. {url}")
    
    # 加载分镜
    storyboard_path = args.storyboard_json
    if not storyboard_path:
        storyboard_path = f"scripts/fixtures/storyboard_{args.job_id[:8]}.json"
    
    if storyboard_path and os.path.isfile(storyboard_path):
        with open(storyboard_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            storyboard = data
        elif isinstance(data, dict) and "storyboard" in data:
            storyboard = data["storyboard"]
        else:
            storyboard = data
        print(f"\n加载分镜: {storyboard_path} ({len(storyboard)} 镜)")
    else:
        print(f"\n警告: 未找到分镜文件 {storyboard_path}")
        print("将自动生成仅含时长的分镜")
        # 根据片段数量生成简单分镜
        storyboard = [{"index": i+1, "duration_sec": 5} for i in range(len(segment_urls))]
    
    # 构建请求
    payload = {
        "segment_urls": segment_urls,
        "storyboard": storyboard,
        "with_transitions": args.with_transitions,
        "with_captions": args.with_captions,
        "with_voiceover": args.with_voiceover,
    }
    
    print(f"\n请求: POST {args.url}/api/video/concat-from-segments ...")
    print(f"  with_transitions: {args.with_transitions}")
    print(f"  with_captions: {args.with_captions}")
    print(f"  with_voiceover: {args.with_voiceover}")
    
    try:
        r = httpx.post(f"{args.url.rstrip('/')}/api/video/concat-from-segments", json=payload, timeout=600.0)
        print(f"响应状态: {r.status_code}")
        print(f"响应内容: {r.text[:2000]}")
        r.raise_for_status()
        out = r.json()
        print("\n完成!")
        print("  成片:", out.get("merged_url") or out.get("merged_download_url"))
        print("  配音:", out.get("voiceover_url") or out.get("voiceover_download_url"))
    except httpx.HTTPStatusError as e:
        print(f"\n请求失败: {e.response.status_code}")
        try:
            j = e.response.json()
            detail = j.get("detail")
            if detail:
                print("详情:", detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:500])
        except Exception:
            print(e.response.text[:1000])
        sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
