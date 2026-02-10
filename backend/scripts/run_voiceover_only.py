"""仅跑配音：对 static/merged 下已有成片按分镜添加 TTS 并混音，不重新生成视频。
用法（需先启动后端 python run.py）：
  cd backend && python scripts/run_voiceover_only.py
  python scripts/run_voiceover_only.py --merged fb4023abe5284c82835837f0e4fffab4.mp4
  python scripts/run_voiceover_only.py --merged xxx.mp4 --storyboard-json path/to/storyboard.json
"""
import argparse
import json
import os
import sys
from pathlib import Path

# backend 为当前目录
BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import httpx

MERGED_DIR = BACKEND / "static" / "merged"
BASE_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

# 默认短分镜（镜湖道院孟川绿竹简化版），用于测试；实际使用请传 --storyboard-json
DEFAULT_STORYBOARD = [
    {"index": 1, "shot_type": "全景", "shot_desc": "镜湖道院正门", "copy": "", "duration_sec": 5},
    {"index": 2, "shot_type": "中景", "shot_desc": "孟川与师弟师妹", "copy": "师弟师妹：孟师兄 孟师兄好 见过孟师兄", "duration_sec": 4},
    {"index": 3, "shot_type": "近景", "shot_desc": "孟川侧脸", "copy": "", "duration_sec": 4},
    {"index": 4, "shot_type": "中景", "shot_desc": "绿竹跑来", "copy": "绿竹：公子 公子", "duration_sec": 3},
    {"index": 5, "shot_type": "近景", "shot_desc": "孟川微笑", "copy": "孟川：绿竹，你怎么来了？", "duration_sec": 4},
    {"index": 6, "shot_type": "中景", "shot_desc": "绿竹说话", "copy": "绿竹：我家小姐想请公子同游东山，昨夜大雪，东山极美。", "duration_sec": 5},
    {"index": 7, "shot_type": "近景", "shot_desc": "孟川蹙眉", "copy": "孟川：东山太远，怕是要过夜，明日才回。", "duration_sec": 4},
    {"index": 8, "shot_type": "中景", "shot_desc": "绿竹比划", "copy": "绿竹：云家东山有别院，可宿。", "duration_sec": 4},
    {"index": 9, "shot_type": "特写", "shot_desc": "孟川按刀", "copy": "", "duration_sec": 3},
    {"index": 10, "shot_type": "近景", "shot_desc": "孟川坚定", "copy": "孟川：转告青萍，一月后玉阳宫斩妖盛会，我需潜修，无法同往。", "duration_sec": 5},
]
# 与 DEFAULT_STORYBOARD 一一对应：无台词镜 None，男角用男声 ID，女角用女声 ID，测试环境不依赖 LLM 推断
DEFAULT_SHOT_VOICE_IDS = [
    None, "male-qn-jingying", None, "female-yujie", "male-qn-jingying", "female-yujie",
    "male-qn-jingying", "female-yujie", None, "male-qn-jingying",
]


def find_merged_mp4(name: str | None) -> str | None:
    if name:
        p = MERGED_DIR / name
        if p.is_file():
            return name
        if (MERGED_DIR / f"{name}.mp4").is_file():
            return f"{name}.mp4"
        return None
    # 取最新一个未带 _vo 的 .mp4
    candidates = [f for f in os.listdir(MERGED_DIR) if f.endswith(".mp4") and "_vo" not in f and "_cap" not in f]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (MERGED_DIR / f).stat().st_mtime, reverse=True)
    return candidates[0]


def load_storyboard(path: str | None) -> list[dict]:
    if not path or not os.path.isfile(path):
        return DEFAULT_STORYBOARD
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "storyboard" in data:
        return data["storyboard"]
    return DEFAULT_STORYBOARD


def main():
    parser = argparse.ArgumentParser(description="仅对已有成片跑配音")
    parser.add_argument("--merged", default=None, help="成片文件名，如 xxx.mp4；不传则用 static/merged 下最新 .mp4")
    parser.add_argument("--storyboard-json", default=None, help="分镜 JSON 文件路径；不传则用内置默认")
    parser.add_argument("--script-summary", default="", help="剧本摘要，供情绪推断")
    parser.add_argument("--url", default=BASE_URL, help="后端 base URL")
    args = parser.parse_args()

    merged_name = find_merged_mp4(args.merged)
    if not merged_name:
        print("未找到成片。请指定 --merged xxx.mp4 或确保 static/merged 下有 .mp4 文件")
        sys.exit(1)

    storyboard = load_storyboard(args.storyboard_json)
    merged_url = f"/api/merged/{merged_name}"
    # 使用默认分镜时带上每镜音色，强制男/女声，避免测试环境 LLM 推断错
    use_default = not (args.storyboard_json and os.path.isfile(args.storyboard_json))
    shot_voice_ids = DEFAULT_SHOT_VOICE_IDS if (use_default and len(storyboard) == len(DEFAULT_SHOT_VOICE_IDS)) else None
    payload = {
        "merged_url": merged_url,
        "storyboard": storyboard,
        "script_summary": args.script_summary or "",
        "shot_voice_ids": shot_voice_ids,
    }
    print(f"成片: {merged_name}")
    print(f"分镜镜数: {len(storyboard)}")
    print(f"请求: POST {args.url}/api/video/voiceover-only ...")
    try:
        r = httpx.post(f"{args.url.rstrip('/')}/api/video/voiceover-only", json=payload, timeout=300.0)
        r.raise_for_status()
        out = r.json()
        print("完成.")
        print("  成片(含配音):", out.get("merged_download_url"))
        print("  配音音频:   ", out.get("voiceover_download_url"))
    except httpx.HTTPStatusError as e:
        print("请求失败:", e.response.status_code)
        body = e.response.text
        try:
            # 尝试解析 JSON 中的 detail（FastAPI 500 返回格式）
            j = e.response.json()
            detail = j.get("detail")
            if detail:
                print("详情:", detail if isinstance(detail, str) else detail)
            else:
                print(body[:2000] if body else "(无响应体)")
        except Exception:
            print(body[:2000] if body else "(无响应体)")
        sys.exit(1)
    except Exception as e:
        print("错误:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
