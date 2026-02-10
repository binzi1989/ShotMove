"""讯飞超拟人语音合成 WebSocket TTS。与 volcano_speech 同接口：返回 (本地 mp3 路径, error_msg)。"""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import threading
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

try:
    import websocket
except ImportError:
    websocket = None

IFLYTEK_APP_ID = (os.getenv("IFLYTEK_APP_ID") or "").strip()
IFLYTEK_API_KEY = (os.getenv("IFLYTEK_API_KEY") or "").strip()
IFLYTEK_API_SECRET = (os.getenv("IFLYTEK_API_SECRET") or "").strip()

# 超拟人合成 API（控制台提供的私有地址）
IFLYTEK_TTS_HOST = (os.getenv("IFLYTEK_TTS_HOST") or "cbm01.cn-huabei-1.xf-yun.com").strip()
IFLYTEK_TTS_PATH = (os.getenv("IFLYTEK_TTS_PATH") or "/v1/private/mcd9m97e6").strip()
TTS_WSS = f"wss://{IFLYTEK_TTS_HOST}{IFLYTEK_TTS_PATH}"

# 单次请求文本上限（超拟人接口建议单次不超过约 1MB base64 对应原文，这里保守按 8000 字节分段）
MAX_TEXT_BYTES = 8000

# 默认发音人：聆玉言（普通话女声）
DEFAULT_VCN = "x6_lingyuyan_pro"

# 可选配音音色（讯飞超拟人 vcn）：供前端下拉选择；传 voice_id 为下列 id 或中文名均可
VOICE_OPTIONS = [
    ("x6_lingfeiyi_pro", "聆飞逸", "普通话", "男声"),
    ("x6_lingxiaoxuan_pro", "聆小璇", "普通话", "女声"),
    ("x5_lingyuzhao_flow", "聆玉昭", "普通话", "女声"),
    ("x6_lingxiaoyue_pro", "聆小玥", "普通话", "女声"),
    ("x6_lingyuyan_pro", "聆玉言", "普通话", "女声"),
    ("x6_wennuancixingnansheng_mini", "温暖磁性男声", "普通话", "男声"),
    ("x6_pangbainan1_pro", "旁白男声", "普通话", "男声"),
]


def _voice_id_to_vcn(voice_id: Optional[str]) -> str:
    """将业务 voice_id（vcn id 或中文名）映射到讯飞超拟人 vcn。"""
    s = (voice_id or "").strip()
    if not s:
        return DEFAULT_VCN
    v = s.lower().replace(" ", "")
    for vcn, name, *_ in VOICE_OPTIONS:
        if vcn.lower() == v or name.replace(" ", "") in v or v in name.replace(" ", ""):
            return vcn
    if "lingyuyan" in v or "聆玉言" in v:
        return "x6_lingyuyan_pro"
    if "lingfeiyi" in v or "聆飞逸" in v:
        return "x6_lingfeiyi_pro"
    if "lingxiaoxuan" in v or "聆小璇" in v:
        return "x6_lingxiaoxuan_pro"
    if "lingyuzhao" in v or "聆玉昭" in v:
        return "x5_lingyuzhao_flow"
    if "lingxiaoyue" in v or "聆小玥" in v:
        return "x6_lingxiaoyue_pro"
    if "wennuan" in v or "温暖磁性" in v:
        return "x6_wennuancixingnansheng_mini"
    if "pangbai" in v or "旁白" in v:
        return "x6_pangbainan1_pro"
    # 先判女声再判男声，否则 "female-yujie" 里的 "male" 会被误判成男声
    if "female" in v or "女" in v or "yujie" in v or "shaonv" in v or "tianmei" in v or "chengshu" in v:
        return "x6_lingxiaoxuan_pro"
    if "male" in v or "男" in v:
        return "x6_lingfeiyi_pro"
    return DEFAULT_VCN


def _build_auth_url() -> str:
    """生成带鉴权参数的 wss URL（HMAC-SHA256，与 WebSocket 通用鉴权一致）。"""
    if not IFLYTEK_API_KEY or not IFLYTEK_API_SECRET:
        raise ValueError("IFLYTEK_API_KEY / IFLYTEK_API_SECRET 未配置")
    now = datetime.now(timezone.utc)
    date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    request_line = f"GET {IFLYTEK_TTS_PATH} HTTP/1.1"
    signature_origin = f"host: {IFLYTEK_TTS_HOST}\ndate: {date}\n{request_line}"
    signature_sha = hmac.new(
        IFLYTEK_API_SECRET.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode("utf-8")
    authorization_origin = (
        f'api_key="{IFLYTEK_API_KEY}", '
        f'algorithm="hmac-sha256", '
        f'headers="host date request-line", '
        f'signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
    params = {
        "authorization": authorization,
        "date": date,
        "host": IFLYTEK_TTS_HOST,
    }
    return f"{TTS_WSS}?{urllib.parse.urlencode(params)}"


def text_to_speech(
    text: str,
    voice_id: Optional[str] = None,
    speed: int = 50,
    volume: int = 50,
    pitch: int = 50,
    emotion: Optional[str] = None,  # 讯飞不支撑，仅保持与火山接口一致
) -> tuple[Optional[str], Optional[str]]:
    """
    讯飞超拟人 TTS，返回 (本地 mp3 路径, error_msg)。
    文本超长会按 MAX_TEXT_BYTES 分段合成并拼接为单文件。
    """
    if not text or not text.strip():
        return None, "文本为空"
    if not IFLYTEK_APP_ID:
        return None, "IFLYTEK_APP_ID 未配置"
    if not IFLYTEK_API_KEY or not IFLYTEK_API_SECRET:
        return None, "IFLYTEK_API_KEY / IFLYTEK_API_SECRET 未配置"
    if websocket is None:
        return None, "请安装 websocket-client: pip install websocket-client"

    text = text.strip()
    vcn = _voice_id_to_vcn(voice_id)
    chunks: list[str] = []
    remaining = text.encode("utf-8")
    while remaining:
        if len(remaining) <= MAX_TEXT_BYTES:
            chunks.append(remaining.decode("utf-8"))
            break
        chunk_bytes = remaining[:MAX_TEXT_BYTES]
        last = chunk_bytes.rfind(b" ")
        if last <= 0:
            last = len(chunk_bytes)
        chunk_bytes = remaining[:last]
        remaining = remaining[last:]
        chunks.append(chunk_bytes.decode("utf-8", errors="ignore"))

    audio_parts: list[bytes] = []
    for chunk_text in chunks:
        if not chunk_text.strip():
            continue
        path, err = _synthesize_one(chunk_text, vcn=vcn, speed=speed, volume=volume, pitch=pitch)
        if err or not path:
            return None, err or "合成返回空"
        try:
            with open(path, "rb") as f:
                audio_parts.append(f.read())
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
    if not audio_parts:
        return None, "无音频数据"

    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    for part in audio_parts:
        out.write(part)
    out.close()
    return out.name, None


def _synthesize_one(
    text: str,
    vcn: str = DEFAULT_VCN,
    speed: int = 50,
    volume: int = 50,
    pitch: int = 50,
) -> tuple[Optional[str], Optional[str]]:
    """单段文本超拟人合成，返回 (临时 mp3 路径, error_msg)。"""
    auth_url = _build_auth_url()
    # 超拟人 payload.text 为 base64 编码的原文
    text_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    request_body = {
        "header": {
            "app_id": IFLYTEK_APP_ID,
            "status": 2,
        },
        "parameter": {
            "oral": {"oral_level": "mid"},
            "tts": {
                "vcn": vcn,
                "speed": speed,
                "volume": volume,
                "pitch": pitch,
                "bgs": 0,
                "reg": 0,
                "rdn": 0,
                "rhy": 0,
                "audio": {
                    "encoding": "lame",
                    "sample_rate": 24000,
                    "channels": 1,
                    "bit_depth": 16,
                    "frame_size": 0,
                },
            },
        },
        "payload": {
            "text": {
                "encoding": "utf8",
                "compress": "raw",
                "format": "plain",
                "status": 2,
                "seq": 0,
                "text": text_b64,
            },
        },
    }
    payload = json.dumps(request_body, ensure_ascii=False)

    collected: list[tuple[int, bytes]] = []  # (seq, audio_bytes)
    err_msg: Optional[str] = None
    done = threading.Event()

    def on_open(ws):
        ws.send(payload)

    def on_message(ws, message):
        nonlocal err_msg
        try:
            obj = json.loads(message)
            header = obj.get("header", {})
            code = header.get("code", -1)
            if code != 0:
                err_msg = header.get("message") or f"code={code}"
                done.set()
                return
            pl = obj.get("payload") or {}
            audio_block = pl.get("audio")
            if audio_block:
                seq = audio_block.get("seq", 0)
                audio_b64 = audio_block.get("audio")
                if audio_b64:
                    collected.append((seq, base64.b64decode(audio_b64)))
                if audio_block.get("status") == 2:
                    done.set()
        except Exception as e:
            err_msg = str(e)
            done.set()

    def on_error(ws, error):
        nonlocal err_msg
        err_msg = str(error) if error else "WebSocket error"
        done.set()

    ws = websocket.WebSocketApp(
        auth_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
    )
    t = threading.Thread(target=lambda: ws.run_forever())
    t.daemon = True
    t.start()
    done.wait(timeout=25)
    try:
        ws.close()
    except Exception:
        pass
    t.join(timeout=2)

    if err_msg:
        return None, err_msg
    if not collected:
        return None, "未收到音频数据"

    # 按 seq 排序后拼接
    collected.sort(key=lambda x: x[0])
    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    for _, part in collected:
        out.write(part)
    out.close()
    return out.name, None
