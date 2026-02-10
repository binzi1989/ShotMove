"""
可灵视频任务查询并下载。
- 列表：GET /v1/videos/omni-video?pageNum=1&pageSize=30
- 单个：GET /v1/videos/omni-video/{id}
请求头：Content-Type: application/json，Authorization: Bearer <JWT>
成功则下载 data.task_result.videos 中的视频到本地。
用法：
  python query_omni_download.py              # 按 TASK_IDS_RAW 逐个查 GET /v1/videos/omni-video/{id}
  python query_omni_download.py list         # 列表接口分页拉取并下载成功的
  python query_omni_download.py single <id>  # 仅调 GET /v1/videos/omni-video/{id} 并打印响应
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import jwt
from dotenv import load_dotenv

# 加载 backend 目录下的 .env（脚本在 backend/scripts/ 下）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 与 kling_video 一致：从环境变量读取
KLING_ACCESS_KEY = (os.getenv("KLING_ACCESS_KEY") or "").strip()
KLING_SECRET_KEY = (os.getenv("KLING_SECRET_KEY") or "").strip()
KLING_BASE = (os.getenv("KLING_BASE") or "https://api-beijing.klingai.com").rstrip("/")
OUTPUT_DIR = Path(os.getenv("OMNI_DOWNLOAD_DIR", "data/omni_videos")).resolve()

# 任务 ID 列表（18 位完整 ID）
TASK_IDS_RAW = """
847227806181773323
847227779610841179
847227752431902781
847225742345277477
847225734937980980
847225727748943942
847224896400146520
847224884354248790
847223065833902121
847223905256902713
847223917177106459
847222328475426817
847222318664945677
847222338625482818
847219678908272716
847218929436631050
847218941017100339
847218952723390477
847217512478949403
847216526029123680
847216544731521064
847216532878422018
847215379218329651
847215364240465973
847215348734140486
"""


def _bearer_token() -> str:
    if not KLING_ACCESS_KEY or not KLING_SECRET_KEY:
        raise ValueError("请设置环境变量 KLING_ACCESS_KEY 和 KLING_SECRET_KEY")
    now = int(time.time())
    return jwt.encode(
        {"iss": KLING_ACCESS_KEY, "exp": now + 1800, "nbf": now - 5},
        KLING_SECRET_KEY,
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT"},
    )


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_bearer_token()}",
        "Content-Type": "application/json",
    }


def parse_task_ids(text: str) -> list[str]:
    """从多行文本解析 task_id，去掉每行末尾非数字字符。"""
    ids = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # 只保留数字部分作为 task_id
        match = re.match(r"^(\d+)", line)
        if match:
            ids.append(match.group(1))
    return ids


def list_omni_tasks(page_num: int = 1, page_size: int = 30) -> dict:
    """
    GET /v1/videos/omni-video?pageNum=1&pageSize=30
    返回接口原始 JSON（或含 _error 的 dict）。
    """
    url = f"{KLING_BASE}/v1/videos/omni-video"
    params = {"pageNum": max(1, page_num), "pageSize": min(500, max(1, page_size))}
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, params=params, headers=_headers())
            data = r.json() if r.content else {}
            if r.status_code != 200:
                data["_error"] = data.get("message") or data.get("error") or r.text or f"HTTP {r.status_code}"
            return data
    except Exception as e:
        return {"_error": str(e)}


def query_single_omni(task_id: str) -> tuple[dict, int]:
    """
    仅请求 GET /v1/videos/omni-video/{id}，不回退到 tasks。
    返回 (响应 body 的 dict, HTTP status_code)。
    """
    url = f"{KLING_BASE}/v1/videos/omni-video/{task_id}"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=_headers())
            data = r.json() if r.content else {}
            return data, r.status_code
    except Exception as e:
        return {"_error": str(e)}, 0


def query_task(task_id: str) -> dict:
    """
    请求 GET /v1/videos/omni-video/{id}。
    返回接口原始 JSON（或含 _error、_last_status、_last_data 的 dict）。
    """
    url = f"{KLING_BASE}/v1/videos/omni-video/{task_id}"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=_headers())
            data = r.json() if r.content else {}
            if r.status_code != 200:
                data["_error"] = data.get("message") or data.get("error") or r.text or f"HTTP {r.status_code}"
                data["_last_status"] = r.status_code
                data["_last_data"] = data.copy()
            else:
                if data.get("code") is not None and data.get("code") != 0:
                    data["_error"] = data.get("message") or data.get("task_status_msg") or f"code={data.get('code')}"
            return data
    except Exception as e:
        return {"_error": str(e)}


def download_file(url: str, dest: Path) -> bool:
    """下载 URL 到 dest，返回是否成功。"""
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"  下载失败 {url[:60]}...: {e}", file=sys.stderr)
        return False


def _extract_videos_and_download(data: dict, task_id: str, output_dir: Path) -> None:
    """从单任务 data 中取 task_result.videos 并下载到 output_dir。"""
    tr = data.get("task_result") or data.get("result") or {}
    videos = (tr.get("videos") if isinstance(tr, dict) else []) or []
    for j, v in enumerate(videos):
        url = v.get("url") or v.get("video_url") or v.get("watermark_url")
        if not url:
            continue
        dest = output_dir / f"{task_id}_{j}.mp4"
        if dest.exists():
            print(f"  已存在: {dest.name}")
        else:
            ok = download_file(url, dest)
            print(f"  {'已下载' if ok else '下载失败'}: {dest.name}")


def run_list():
    """列表模式：GET /v1/videos/omni-video 分页拉取，下载状态为 succeed 的任务视频。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page_size = 100
    page_num = 1
    total_downloaded = 0
    print(f"列表模式：分页拉取 omni-video，结果保存到 {OUTPUT_DIR}\n")
    while True:
        print(f"  请求 pageNum={page_num}, pageSize={page_size} ...")
        resp = list_omni_tasks(page_num=page_num, page_size=page_size)
        if resp.get("_error"):
            print(f"  请求失败: {resp['_error']}")
            break
        if resp.get("code") is not None and resp.get("code") != 0:
            print(f"  业务失败: code={resp.get('code')}, message={resp.get('message', '')}")
            break
        data = resp.get("data") or resp
        # 兼容 data.list / data.tasks / data 为数组
        items = data.get("list") or data.get("tasks")
        if items is None and isinstance(data, list):
            items = data
        if not items:
            print(f"  当前页无数据，结束")
            break
        for i, item in enumerate(items):
            task_id = str(item.get("task_id") or item.get("id") or f"p{page_num}_{i}")
            status = (item.get("task_status") or item.get("status") or "").lower()
            if status != "succeed":
                continue
            print(f"[{task_id}] 状态 succeed，下载视频 ...")
            _extract_videos_and_download(item, task_id, OUTPUT_DIR)
            total_downloaded += 1
        if len(items) < page_size:
            print(f"  本页 {len(items)} 条 < pageSize，结束")
            break
        page_num += 1
        if page_num > 1000:
            print("  已达最大页码 1000，结束")
            break
    print(f"\n完成，共下载 {total_downloaded} 个成功任务。")


def run_single(task_id: str) -> None:
    """单个任务模式：仅调 GET /v1/videos/omni-video/{id}，打印完整响应，成功则下载。"""
    print(f"请求 GET /v1/videos/omni-video/{task_id}\n")
    data, status = query_single_omni(task_id)
    print(f"HTTP 状态: {status}")
    print("响应 body:")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if status != 200:
        return
    if data.get("code") is not None and data.get("code") != 0:
        print(f"\n业务失败 code={data.get('code')}, message={data.get('message', '')}")
        return
    payload = data.get("data") or data
    st = (payload.get("task_status") or payload.get("status") or "").lower()
    print(f"\n任务状态: {st}")
    if st != "succeed":
        return
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"下载到 {OUTPUT_DIR}\n")
    _extract_videos_and_download(payload, task_id, OUTPUT_DIR)
    print("\n完成。")


def run():
    argv = [a.strip() for a in sys.argv[1:] if a.strip()]
    if argv and argv[0].lower() == "list":
        run_list()
        return
    if argv and argv[0].lower() == "single":
        if len(argv) < 2:
            print("用法: python query_omni_download.py single <task_id>", file=sys.stderr)
            sys.exit(1)
        run_single(argv[1])
        return
    task_ids = parse_task_ids(TASK_IDS_RAW)
    if not task_ids:
        print("未解析到任何 task_id", file=sys.stderr)
        sys.exit(1)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"共 {len(task_ids)} 个任务，结果保存到 {OUTPUT_DIR}\n")

    for i, task_id in enumerate(task_ids, 1):
        print(f"[{i}/{len(task_ids)}] 查询 task_id={task_id} ...")
        resp = query_task(task_id)
        if resp.get("_error"):
            print(f"  请求失败: {resp['_error']}")
            if "_last_status" in resp or "_last_data" in resp:
                ld = resp.get("_last_data")
                status_code = resp.get("_last_status", "?")
                if isinstance(ld, dict):
                    msg = ld.get("message") or ld.get("error") or ""
                    path = ld.get("path") or ""
                    if msg or path:
                        print(f"    最后响应: HTTP {status_code}, message={msg!r}, path={path!r}")
                    else:
                        print(f"    最后响应: HTTP {status_code}, keys={list(ld.keys())}")
                elif isinstance(ld, list):
                    print(f"    最后响应: HTTP {status_code}, list(len={len(ld)})")
                else:
                    print(f"    最后响应: HTTP {status_code}, {type(ld).__name__}")
            continue
        if resp.get("code") is not None and resp.get("code") != 0:
            print(f"  业务失败: code={resp.get('code')}, message={resp.get('message', '')}")
            continue
        data = resp.get("data") or resp
        status = (data.get("task_status") or data.get("status") or "").lower()
        status_msg = data.get("task_status_msg") or ""
        print(f"  状态: {status} {status_msg}".strip())
        if status != "succeed":
            continue
        # 兼容 task_result.videos 与 result.videos 两种结构
        tr = data.get("task_result") or data.get("result") or {}
        videos = (tr.get("videos") if isinstance(tr, dict) else []) or []
        if not videos:
            print("  无视频 URL，跳过")
            continue
        _extract_videos_and_download(data, task_id, OUTPUT_DIR)
    print("\n完成。")


if __name__ == "__main__":
    run()
