"""可灵 Kling 视频生成：Omni-Video 图/主体参考生视频，用于商品短视频（物品一致性更好）。"""
import logging
import os
import ssl
import time
from typing import Optional

import httpx
import jwt

logger = logging.getLogger(__name__)

KLING_ACCESS_KEY = (os.getenv("KLING_ACCESS_KEY") or "").strip()
KLING_SECRET_KEY = (os.getenv("KLING_SECRET_KEY") or "").strip()
KLING_BASE = (os.getenv("KLING_BASE") or "https://api-beijing.klingai.com").rstrip("/")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-video-o1")
# 单段时长：可灵枚举 3,4,5,6,7,8,9,10；文生视频/首帧图生视频仅支持 5 和 10s，此处默认 5
KLING_DURATION = os.getenv("KLING_DURATION", "5")
KLING_T2V_OMNI_DURATIONS = ("5", "10")
KLING_ASPECT_RATIO = os.getenv("KLING_ASPECT_RATIO", "16:9")

# 查询可灵时的 GET 请求：遇 SSL/连接错误自动重试，避免 WRONG_VERSION_NUMBER、UNEXPECTED_EOF 等瞬时错误
KLING_QUERY_RETRIES = int(os.getenv("KLING_QUERY_RETRIES", "4"))
KLING_QUERY_RETRY_DELAY_SEC = float(os.getenv("KLING_QUERY_RETRY_DELAY_SEC", "2"))


def _get_with_retry(url: str, headers: dict) -> tuple[Optional[httpx.Response], Optional[str]]:
    """对可灵 GET 请求做有限次重试，遇 SSL/连接错误时等待后重试。返回 (response, error)，成功时 error 为 None。"""
    last_err: Optional[str] = None
    retries = max(1, KLING_QUERY_RETRIES)
    for attempt in range(retries):
        try:
            # 使用 HTTP/1.1、关闭 http2，减少部分环境下的 SSL 兼容问题（WRONG_VERSION_NUMBER / UNEXPECTED_EOF）
            with httpx.Client(timeout=20.0, http2=False) as client:
                r = client.get(url, headers=headers)
                return r, None
        except (ssl.SSLError, OSError, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout) as e:
            last_err = str(e)
            if attempt < retries - 1:
                delay = KLING_QUERY_RETRY_DELAY_SEC * (attempt + 1)
                logger.warning("可灵查询请求 SSL/连接错误，%.0fs 后重试（%d/%d）: %s", delay, attempt + 1, retries, last_err[:120])
                time.sleep(delay)
            else:
                logger.warning("可灵查询请求多次重试后仍失败: %s", last_err[:200])
        except Exception as e:
            return None, str(e)
    return None, last_err


def has_kling() -> bool:
    return bool(KLING_ACCESS_KEY and KLING_SECRET_KEY)


def _bearer_token() -> str:
    """用 AK/SK 生成 JWT，与可灵官方文档一致：Header(alg,typ) + Payload(iss, exp, nbf)。"""
    if not KLING_ACCESS_KEY or not KLING_SECRET_KEY:
        raise ValueError("KLING_ACCESS_KEY and KLING_SECRET_KEY must be set")
    now = int(time.time())
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": KLING_ACCESS_KEY,
        "exp": now + 1800,   # 有效时间 30 分钟
        "nbf": now - 5,      # 开始生效时间，当前时间 -5 秒
    }
    return jwt.encode(
        payload,
        KLING_SECRET_KEY,
        algorithm="HS256",
        headers=headers,
    )


def _headers() -> dict:
    token = _bearer_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _t2v_omni_duration(duration: str) -> str:
    """文生视频/首帧图生视频仅支持 5 和 10s，非则回退为 5。"""
    return duration if duration in KLING_T2V_OMNI_DURATIONS else "5"


def create_t2v_task(
    prompt: str,
    duration: str = KLING_DURATION,
    aspect_ratio: str = KLING_ASPECT_RATIO,
    mode: str = "pro",
    model_name: str = KLING_MODEL,
) -> tuple[Optional[str], Optional[str]]:
    """
    创建可灵文生视频任务（无参考图）。可灵规则：文生视频时长仅支持 5、10s。
    返回 (task_id, error_msg)，成功时 error_msg 为 None。
    """
    duration = _t2v_omni_duration(str(duration).strip())
    url = f"{KLING_BASE}/v1/videos/text2video"
    payload = {
        "model_name": model_name,
        "prompt": prompt[:1700],
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, json=payload, headers=_headers())
            data = r.json() if r.content else {}
            if r.status_code != 200:
                err = data.get("message") or data.get("error") or data.get("error_msg") or r.text or f"HTTP {r.status_code}"
                return None, err
            task_id = data.get("data", {}).get("task_id") or data.get("task_id")
            if task_id:
                return str(task_id), None
            return None, data.get("message") or "未返回 task_id"
    except ValueError as e:
        return None, "KLING_ACCESS_KEY 未配置或无效" if "KLING_ACCESS_KEY" in str(e) else str(e)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            err = body.get("message") or body.get("error") or body.get("error_msg") or e.response.text
        except Exception:
            err = e.response.text or str(e)
        return None, err or str(e)
    except Exception as e:
        return None, str(e)


def create_omni_video_task(
    prompt: str,
    image_url_list: list[str],
    duration: str = KLING_DURATION,
    aspect_ratio: str = KLING_ASPECT_RATIO,
    mode: str = "pro",
    model_name: str = KLING_MODEL,
) -> tuple[Optional[str], Optional[str]]:
    """
    创建可灵 Omni-Video 任务（图/主体参考）。可灵规则：文生/首帧图生视频时长仅支持 5、10s。
    prompt 中可用 <<<image_1>>> 引用第一张图；image_list 为可公网访问的图片 URL 列表。
    返回 (task_id, error_msg)，成功时 error_msg 为 None。
    """
    duration = _t2v_omni_duration(str(duration).strip())
    url = f"{KLING_BASE}/v1/videos/omni-video"
    payload = {
        "model_name": model_name,
        "prompt": prompt[:1700],
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
    }
    # 官方示例：image_list 为 [{"image_url": "xxx"}, ...]，prompt 用 <<<image_1>>>
    # 但 O1 也支持“纯文生”直接走 omni-video（不传 image_list）。
    if image_url_list and image_url_list[0].strip():
        image_list = [{"image_url": u.strip()} for u in image_url_list[:7] if u and str(u).strip()]
        if image_list:
            payload["image_list"] = image_list
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, json=payload, headers=_headers())
            data = r.json() if r.content else {}
            if r.status_code != 200:
                err = data.get("message") or data.get("error") or data.get("error_msg") or r.text or f"HTTP {r.status_code}"
                return None, err
            task_id = data.get("data", {}).get("task_id") or data.get("task_id")
            if task_id:
                return str(task_id), None
            return None, data.get("message") or "未返回 task_id"
    except ValueError as e:
        return None, "KLING_ACCESS_KEY 未配置或无效" if "KLING_ACCESS_KEY" in str(e) else str(e)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            err = body.get("message") or body.get("error") or body.get("error_msg") or e.response.text
        except Exception:
            err = e.response.text or str(e)
        return None, err or str(e)
    except Exception as e:
        return None, str(e)


def _extract_video_url(d: dict) -> Optional[str]:
    """
    从可灵任务结果中提取视频下载 URL。
    官方响应体：data.task_result.videos[0].url（或 watermark_url），task_status 为 succeed 时存在。
    """
    if not d:
        return None
    tr = d.get("task_result")
    if isinstance(tr, dict):
        videos = tr.get("videos")
        if isinstance(videos, list) and videos:
            first = videos[0] or {}
            # 官方：url 为生成视频 URL，watermark_url 为含水印版；优先用 url，无则用 watermark_url
            u = first.get("url") or first.get("video_url") or first.get("watermark_url")
            if u:
                return str(u).strip()
        u = tr.get("video_url") or tr.get("url")
        if u:
            return str(u).strip()
    if isinstance(tr, list) and tr:
        u = (tr[0] or {}).get("url") or (tr[0] or {}).get("video_url")
        if u:
            return str(u).strip()
    u = d.get("video_url") or d.get("url")
    if u:
        return str(u).strip()
    res = d.get("result")
    if isinstance(res, dict):
        vlist = res.get("videos")
        if isinstance(vlist, list) and vlist:
            first = vlist[0] or {}
            u = first.get("url") or first.get("video_url") or first.get("watermark_url")
            if u:
                return str(u).strip()
        u = res.get("video_url") or res.get("url")
        if u:
            return str(u).strip()
    # 可灵部分接口（如 omni-video）可能放在 output 等字段
    out = d.get("output")
    if isinstance(out, dict):
        vlist = out.get("videos")
        if isinstance(vlist, list) and vlist:
            first = vlist[0] or {}
            u = first.get("url") or first.get("video_url") or first.get("watermark_url")
            if u:
                return str(u).strip()
        u = out.get("video_url") or out.get("url")
        if u:
            return str(u).strip()
    return None


def query_kling_omni_task(task_id: str) -> dict:
    """
    查询可灵 Omni-Video 任务状态。必须用 GET /v1/videos/omni-video/{id}，不能用 /tasks/{id}。
    返回 { status, video_url?, error? }，与 query_kling_task 同结构。遇 SSL/连接错误会自动重试。
    """
    url = f"{KLING_BASE}/v1/videos/omni-video/{task_id}"
    try:
        r, req_err = _get_with_retry(url, _headers())
        if req_err or r is None:
            return {"status": "Fail", "error": req_err or "可灵查询无响应"}
        data = r.json() if r.content else {}
        if r.status_code != 200:
            return {"status": "Fail", "error": data.get("message") or data.get("error") or (r.text or f"HTTP {r.status_code}")}
        if data.get("code") is not None and data.get("code") != 0:
            return {"status": "Fail", "error": data.get("message") or data.get("task_status_msg") or f"code={data.get('code')}"}
        d = data.get("data") or data
        status = (d.get("task_status") or d.get("status") or "").lower()
        # 可灵部分接口成功时返回 completed 而非 succeed，需一并视为完成
        is_done = status in ("succeed", "success", "completed")
        video_url = _extract_video_url(d) if is_done else None
        if is_done and not video_url:
            video_url = _extract_video_url(data)
        if is_done and not video_url:
            logger.warning("可灵 Omni 任务已成功但未解析到 video_url，data keys=%s", list(d.keys()) if isinstance(d, dict) else type(d))
        err = d.get("task_status_msg") or data.get("message")
        # 已完成时统一返回 Success，便于前端显示「全部完成」并允许点击剪辑（无 url 时剪辑接口会再查一次并报错）
        return {
            "status": "Success" if is_done else (status or "Processing"),
            "video_url": video_url,
            "error": err if status in ("fail", "failed") else None,
        }
    except Exception as e:
        return {"status": "Fail", "error": str(e)}


def query_kling_task(task_id: str) -> dict:
    """
    查询可灵文生视频(T2V)任务状态。用于 /v1/videos/text2video 创建的任务。
    返回 { status, video_url?, error? }。遇 SSL/连接错误会自动重试。
    """
    url = f"{KLING_BASE}/v1/videos/tasks/{task_id}"
    try:
        r, req_err = _get_with_retry(url, _headers())
        if req_err or r is None:
            return {"status": "Fail", "error": req_err or "可灵查询无响应"}
        data = r.json() if r.content else {}
        if r.status_code != 200:
            return {"status": "Fail", "error": data.get("message") or data.get("error") or r.text or f"HTTP {r.status_code}"}
        if data.get("code") is not None and data.get("code") != 0:
            return {"status": "Fail", "error": data.get("message") or data.get("task_status_msg") or f"code={data.get('code')}"}
        d = data.get("data") or data
        status = (d.get("task_status") or d.get("status") or "").lower()
        is_done = status in ("succeed", "success", "completed")
        video_url = _extract_video_url(d) if is_done else None
        if is_done and not video_url:
            video_url = _extract_video_url(data)
        if is_done and not video_url:
            logger.warning("可灵任务已成功但未解析到 video_url，data keys=%s", list(d.keys()) if isinstance(d, dict) else type(d))
        err = (d.get("task_status_msg") or data.get("message")) if status in ("fail", "failed") else None
        return {"status": "Success" if is_done else (status or "Processing"), "video_url": video_url, "error": err}
    except Exception as e:
        return {"status": "Fail", "error": str(e)}


def get_kling_task_status_batch(
    task_ids: list[str],
    use_omni: bool = True,
) -> list[dict]:
    """
    批量查询可灵任务状态，供前端按镜头展示（绿 succeed / 蓝 processing / 红 failed）。
    返回 list，每项为 { "task_id", "status": "succeed"|"processing"|"failed", "url"?, "task_status_msg"? }。
    可灵原始状态：submitted/processing -> processing，succeed -> succeed，fail/failed -> failed。
    """
    if not task_ids:
        return []
    query_fn = query_kling_omni_task if use_omni else query_kling_task
    result: list[dict] = []
    for tid in task_ids:
        if not tid or not str(tid).strip():
            result.append({"task_id": tid or "", "status": "failed", "task_status_msg": "无效 task_id"})
            continue
        tid = str(tid).strip()
        try:
            raw = query_fn(tid)
            status = (raw.get("status") or "").strip()
            status_lower = status.lower()
            if status_lower in ("success", "succeed", "completed"):
                normalized = "succeed"
                url = raw.get("video_url")
            elif status_lower in ("fail", "failed"):
                normalized = "failed"
                url = None
            else:
                normalized = "processing"
                url = None
            result.append({
                "task_id": tid,
                "status": normalized,
                "url": url,
                "task_status_msg": raw.get("error") or raw.get("task_status_msg"),
            })
        except Exception as e:
            result.append({
                "task_id": tid,
                "status": "failed",
                "task_status_msg": str(e)[:200],
            })
    return result


def get_kling_download_url(task_id: str, use_omni: bool = False) -> Optional[str]:
    """
    轮询可灵任务直至成功或失败，不限时（不设总时长上限），避免浪费资源包。
    use_omni=True 时用 GET /v1/videos/omni-video/{id}（Omni-Video 任务必须用此接口，否则拿不到结果）。
    成功则返回视频下载 URL；失败则返回 None。
    """
    interval = int(os.getenv("KLING_TASK_POLL_INTERVAL_SEC", "8"))
    query_fn = query_kling_omni_task if use_omni else query_kling_task
    while True:
        result = query_fn(task_id)
        status = (result.get("status") or "").lower()
        if status in ("success", "succeed"):
            url = result.get("video_url")
            if url:
                return url
            # 已成功但未解析到 URL 时再等一轮
        if status in ("fail", "failed"):
            err = result.get("error") or ""
            if err:
                logger.warning("可灵任务 %s 失败: %s", task_id, err[:200])
            break
        time.sleep(interval)
    return None
