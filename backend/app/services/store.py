"""数据持久化：创作任务 SQLite 存储。"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

# 数据库文件放在 backend/data 下
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BACKEND_ROOT / "data"
DB_PATH = DATA_DIR / "creative.db"


def _ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _get_conn() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """创建 tasks 表及会员/积分相关表（若不存在）。"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                pipeline TEXT NOT NULL,
                input TEXT NOT NULL,
                title TEXT,
                content_result TEXT NOT NULL,
                video_result TEXT,
                merged_download_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_pipeline ON tasks(pipeline)"
        )
        # 短剧角色参考图与配音快照（JSON），刷新页面后可恢复
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN character_references TEXT")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # ---------- 会员体系 ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS membership_tiers (
                id TEXT PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                level INTEGER NOT NULL DEFAULT 0,
                daily_task_quota INTEGER NOT NULL DEFAULT 3,
                max_storyboard_shots INTEGER NOT NULL DEFAULT 5,
                can_export_merged_video INTEGER NOT NULL DEFAULT 0,
                price_per_month_credits INTEGER NOT NULL DEFAULT 0,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                device_id TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memberships (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tier_code TEXT NOT NULL,
                started_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_memberships_user_active ON user_memberships(user_id, is_active)"
        )
        # ---------- 积分体系 ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS point_transactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                ref_id TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_point_tx_user_created ON point_transactions(user_id, created_at DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                content_count INTEGER NOT NULL DEFAULT 0,
                video_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, usage_date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()
        _seed_membership_tiers(conn)
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_task(
    pipeline: str,
    input_text: str,
    content_result: dict[str, Any],
    video_result: Optional[dict[str, Any]] = None,
    merged_download_url: Optional[str] = None,
    title: Optional[str] = None,
    character_references: Optional[dict[str, Any]] = None,
) -> str:
    """保存一条创作任务，返回 task_id。character_references 为短剧角色快照（含参考图 base64、配音音色），刷新后可恢复。"""
    task_id = uuid4().hex
    now = _now_iso()
    content_json = json.dumps(content_result, ensure_ascii=False)
    video_json = json.dumps(video_result, ensure_ascii=False) if video_result else None
    char_ref_json = json.dumps(character_references, ensure_ascii=False) if character_references else None
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO tasks (id, pipeline, input, title, content_result, video_result, merged_download_url, character_references, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, pipeline, input_text, title, content_json, video_json, merged_download_url, char_ref_json, now, now),
        )
        conn.commit()
        return task_id
    finally:
        conn.close()


def list_tasks(
    pipeline: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """分页列出任务摘要（不含 content_result / video_result 大字段）。"""
    conn = _get_conn()
    try:
        if pipeline:
            cursor = conn.execute(
                """
                SELECT id, pipeline, input, title, created_at, updated_at,
                       merged_download_url
                FROM tasks
                WHERE pipeline = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (pipeline, limit, offset),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, pipeline, input, title, created_at, updated_at,
                       merged_download_url
                FROM tasks
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
        rows = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "pipeline": r["pipeline"],
                "input": r["input"],
                "input_preview": (r["input"] or "")[:80] + ("..." if len(r["input"] or "") > 80 else ""),
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "merged_download_url": r["merged_download_url"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    """按 id 获取完整任务（含 content_result、video_result）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        content_result = json.loads(row["content_result"]) if row["content_result"] else {}
        video_result = json.loads(row["video_result"]) if row["video_result"] else None
        char_ref = None
        if "character_references" in row.keys() and row["character_references"]:
            char_ref = json.loads(row["character_references"])
        return {
            "id": row["id"],
            "pipeline": row["pipeline"],
            "input": row["input"],
            "title": row["title"],
            "content_result": content_result,
            "video_result": video_result,
            "merged_download_url": row["merged_download_url"],
            "character_references": char_ref,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def delete_task(task_id: str) -> bool:
    """删除一条任务，存在则返回 True。"""
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_task_title(task_id: str, title: str) -> bool:
    """更新任务标题。"""
    conn = _get_conn()
    try:
        now = _now_iso()
        cur = conn.execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, task_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_task(
    task_id: str,
    *,
    title: Optional[str] = None,
    content_result: Optional[dict[str, Any]] = None,
    video_result: Optional[dict[str, Any]] = None,
    merged_download_url: Optional[str] = None,
    character_references: Optional[dict[str, Any]] = None,
) -> bool:
    """更新任务（仅更新传入的非 None 字段）。"""
    conn = _get_conn()
    try:
        now = _now_iso()
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if content_result is not None:
            updates.append("content_result = ?")
            params.append(json.dumps(content_result, ensure_ascii=False))
        if video_result is not None:
            updates.append("video_result = ?")
            params.append(json.dumps(video_result, ensure_ascii=False))
        if merged_download_url is not None:
            updates.append("merged_download_url = ?")
            params.append(merged_download_url)
        if character_references is not None:
            updates.append("character_references = ?")
            params.append(json.dumps(character_references, ensure_ascii=False))
        params.append(task_id)
        cur = conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------- 会员体系 ----------

DEFAULT_TIERS = [
    ("free", "免费", 0, 3, 5, 0, 0, "体验基础能力"),
    ("basic", "基础", 1, 10, 10, 1, 100, "日常轻度创作"),
    ("premium", "专业", 2, 30, 20, 1, 300, "高频创作与导出"),
    ("vip", "尊享", 3, -1, -1, 1, 800, "无限制+优先队列"),
]


def _seed_membership_tiers(conn: sqlite3.Connection) -> None:
    """若档位表为空则写入默认档位。"""
    row = conn.execute("SELECT COUNT(*) FROM membership_tiers").fetchone()
    if row[0] > 0:
        return
    now = _now_iso()
    for code, name, level, daily_quota, max_shots, can_export, price, desc in DEFAULT_TIERS:
        conn.execute(
            """
            INSERT INTO membership_tiers (id, code, name, level, daily_task_quota, max_storyboard_shots,
                can_export_merged_video, price_per_month_credits, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid4().hex, code, name, level, daily_quota, max_shots, can_export, price, desc, now, now),
        )
    conn.commit()


def get_or_create_user_by_device(device_id: str) -> str:
    """按 device_id 获取或创建用户，返回 user_id。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM users WHERE device_id = ?", (device_id,)).fetchone()
        if row:
            return row["id"]
        user_id = uuid4().hex
        now = _now_iso()
        conn.execute(
            "INSERT INTO users (id, device_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, device_id, now, now),
        )
        conn.execute(
            "INSERT INTO user_points (user_id, balance, updated_at) VALUES (?, 0, ?)",
            (user_id, now),
        )
        conn.commit()
        return user_id
    finally:
        conn.close()


def list_membership_tiers() -> list[dict[str, Any]]:
    """返回所有会员档位配置。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, code, name, level, daily_task_quota, max_storyboard_shots, "
            "can_export_merged_video, price_per_month_credits, description, created_at, updated_at "
            "FROM membership_tiers ORDER BY level ASC"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "code": r["code"],
                "name": r["name"],
                "level": r["level"],
                "daily_task_quota": r["daily_task_quota"],
                "max_storyboard_shots": r["max_storyboard_shots"],
                "can_export_merged_video": bool(r["can_export_merged_video"]),
                "price_per_month_credits": r["price_per_month_credits"],
                "description": r["description"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_tier_by_code(tier_code: str) -> Optional[dict[str, Any]]:
    """按 code 获取档位配置。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, code, name, level, daily_task_quota, max_storyboard_shots, "
            "can_export_merged_video, price_per_month_credits, description "
            "FROM membership_tiers WHERE code = ?",
            (tier_code,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "level": row["level"],
            "daily_task_quota": row["daily_task_quota"],
            "max_storyboard_shots": row["max_storyboard_shots"],
            "can_export_merged_video": bool(row["can_export_merged_video"]),
            "price_per_month_credits": row["price_per_month_credits"],
            "description": row["description"],
        }
    finally:
        conn.close()


def get_user_effective_membership(user_id: str) -> Optional[dict[str, Any]]:
    """获取用户当前生效的会员记录（未过期且 is_active=1）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT um.id, um.user_id, um.tier_code, um.started_at, um.expires_at, um.is_active,
                   mt.name AS tier_name, mt.level, mt.daily_task_quota, mt.max_storyboard_shots,
                   mt.can_export_merged_video
            FROM user_memberships um
            JOIN membership_tiers mt ON mt.code = um.tier_code
            WHERE um.user_id = ? AND um.is_active = 1 AND um.expires_at > ?
            ORDER BY um.expires_at DESC LIMIT 1
            """,
            (user_id, _now_iso()),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "tier_code": row["tier_code"],
            "tier_name": row["tier_name"],
            "level": row["level"],
            "started_at": row["started_at"],
            "expires_at": row["expires_at"],
            "daily_task_quota": row["daily_task_quota"],
            "max_storyboard_shots": row["max_storyboard_shots"],
            "can_export_merged_video": bool(row["can_export_merged_video"]),
        }
    finally:
        conn.close()


def get_user_effective_tier(user_id: str) -> dict[str, Any]:
    """获取用户当前生效档位（含 free 默认）。返回与 get_tier_by_code 结构一致 + expires_at。"""
    membership = get_user_effective_membership(user_id)
    if membership:
        tier = get_tier_by_code(membership["tier_code"])
        if tier:
            tier["expires_at"] = membership["expires_at"]
            return tier
    tier = get_tier_by_code("free")
    if not tier:
        tier = {
            "code": "free",
            "name": "免费",
            "level": 0,
            "daily_task_quota": 3,
            "max_storyboard_shots": 5,
            "can_export_merged_video": False,
            "price_per_month_credits": 0,
            "description": "体验基础能力",
            "expires_at": None,
        }
    else:
        tier["expires_at"] = None
    return tier


def create_user_membership(
    user_id: str,
    tier_code: str,
    months: int = 1,
) -> Optional[str]:
    """为用户开通/续费会员。先将会员表中该用户 is_active 置 0，再插入新记录。返回新记录 id。"""
    tier = get_tier_by_code(tier_code)
    if not tier:
        return None
    conn = _get_conn()
    try:
        now = _now_iso()
        from datetime import datetime, timedelta, timezone
        dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        expires = (dt + timedelta(days=months * 31)).isoformat().replace("+00:00", "Z")
        conn.execute(
            "UPDATE user_memberships SET is_active = 0, updated_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        membership_id = uuid4().hex
        conn.execute(
            """
            INSERT INTO user_memberships (id, user_id, tier_code, started_at, expires_at, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (membership_id, user_id, tier_code, now, expires, now, now),
        )
        conn.commit()
        return membership_id
    finally:
        conn.close()


# ---------- 积分体系 ----------


def get_user_balance(user_id: str) -> int:
    """获取用户积分余额。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT balance FROM user_points WHERE user_id = ?", (user_id,)).fetchone()
        return int(row["balance"]) if row else 0
    finally:
        conn.close()


def add_point_transaction(
    user_id: str,
    amount: int,
    tx_type: str,
    ref_id: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    """增加一条积分流水并更新余额。amount 可正可负。"""
    conn = _get_conn()
    try:
        now = _now_iso()
        tx_id = uuid4().hex
        conn.execute(
            "INSERT INTO point_transactions (id, user_id, amount, type, ref_id, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tx_id, user_id, amount, tx_type, ref_id, description, now),
        )
        conn.execute(
            "UPDATE user_points SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (amount, now, user_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def deduct_points(user_id: str, amount: int, tx_type: str, ref_id: Optional[str] = None, description: Optional[str] = None) -> bool:
    """扣减积分（amount 为正数），余额不足返回 False。"""
    if amount <= 0:
        return False
    balance = get_user_balance(user_id)
    if balance < amount:
        return False
    return add_point_transaction(user_id, -amount, tx_type, ref_id, description)


def has_signed_in_today(user_id: str) -> bool:
    """今日是否已签到。"""
    conn = _get_conn()
    try:
        today = _now_iso()[:10]
        row = conn.execute(
            "SELECT 1 FROM point_transactions WHERE user_id = ? AND type = 'sign_in' AND date(created_at) = date(?) LIMIT 1",
            (user_id, today),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_point_transactions(user_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """分页查询用户积分流水。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, user_id, amount, type, ref_id, description, created_at FROM point_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "amount": r["amount"],
                "type": r["type"],
                "ref_id": r["ref_id"],
                "description": r["description"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------- 每日配额 ----------


def get_daily_usage(user_id: str) -> tuple[int, int]:
    """返回 (当日内容生成次数, 当日视频生成次数)。"""
    conn = _get_conn()
    try:
        today = _now_iso()[:10]
        row = conn.execute(
            "SELECT content_count, video_count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        ).fetchone()
        if not row:
            return 0, 0
        return int(row["content_count"]), int(row["video_count"])
    finally:
        conn.close()


def increment_daily_usage(user_id: str, is_video: bool = False) -> None:
    """增加当日使用次数。is_video=True 为视频生成，否则为内容生成。"""
    conn = _get_conn()
    try:
        now = _now_iso()
        today = now[:10]
        row = conn.execute(
            "SELECT content_count, video_count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        ).fetchone()
        if row:
            if is_video:
                conn.execute(
                    "UPDATE daily_usage SET video_count = video_count + 1 WHERE user_id = ? AND usage_date = ?",
                    (user_id, today),
                )
            else:
                conn.execute(
                    "UPDATE daily_usage SET content_count = content_count + 1 WHERE user_id = ? AND usage_date = ?",
                    (user_id, today),
                )
        else:
            conn.execute(
                "INSERT INTO daily_usage (user_id, usage_date, content_count, video_count) VALUES (?, ?, ?, ?)",
                (user_id, today, 0 if is_video else 1, 1 if is_video else 0),
            )
        conn.commit()
    finally:
        conn.close()


def check_can_use_quota(user_id: str) -> tuple[bool, int, int]:
    """检查用户当日是否还有配额。返回 (是否可用, 当日已用总次数, 配额上限)。"""
    content_used, video_used = get_daily_usage(user_id)
    total_used = content_used + video_used
    tier = get_user_effective_tier(user_id)
    quota = tier.get("daily_task_quota", 3)
    if quota < 0:
        return True, total_used, -1
    return total_used < quota, total_used, quota
