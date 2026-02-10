"""测试 video_generation 中按镜角色参考图解析（含多角色同镜）。"""
import os
import sys

_backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)
os.chdir(_backend_root)

from app.schemas import StoryboardItem
from app.agents.video_generation import _resolve_shot_character_urls


def test_resolve_shot_character_urls_multi():
    """多角色同镜：character_names 为 [主角名, 配角名] 时，返回顺序一致的多张参考图 URL。"""
    refs = [
        {"name": "李华", "role": "主角", "url": "https://example.com/lihua.jpg"},
        {"name": "小明", "role": "配角", "url": "https://example.com/xiaoming.jpg"},
    ]
    shot = StoryboardItem(
        index=1,
        shot_desc="两人对话",
        character_names=["李华", "小明"],
        character_name="李华",
    )
    urls = _resolve_shot_character_urls(shot, refs)
    assert urls == ["https://example.com/lihua.jpg", "https://example.com/xiaoming.jpg"]


def test_resolve_shot_character_urls_multi_dict():
    """shot 为 dict 时（如 API 入参），同样支持 character_names 多角色。"""
    refs = [
        {"name": "李华", "role": "主角", "url": "https://example.com/lihua.jpg"},
        {"name": "小明", "role": "配角", "url": "https://example.com/xiaoming.jpg"},
    ]
    shot = {
        "index": 1,
        "shot_desc": "两人对话",
        "character_names": ["李华", "小明"],
        "character_name": "李华",
    }
    urls = _resolve_shot_character_urls(shot, refs)
    assert urls == ["https://example.com/lihua.jpg", "https://example.com/xiaoming.jpg"]


def test_resolve_shot_character_urls_single():
    """仅 character_name 时返回单张参考图。"""
    refs = [
        {"name": "李华", "role": "主角", "url": "https://example.com/lihua.jpg"},
    ]
    shot = StoryboardItem(index=1, shot_desc="单人", character_name="李华")
    urls = _resolve_shot_character_urls(shot, refs)
    assert urls == ["https://example.com/lihua.jpg"]


def test_resolve_shot_character_urls_fallback():
    """未匹配到 name 时先主角后配角。"""
    refs = [
        {"name": "李华", "role": "主角", "url": "https://example.com/lihua.jpg"},
        {"name": "小明", "role": "配角", "url": "https://example.com/xiaoming.jpg"},
    ]
    shot = StoryboardItem(index=1, shot_desc="未填角色名")
    urls = _resolve_shot_character_urls(shot, refs)
    assert urls == ["https://example.com/lihua.jpg"]


def test_resolve_shot_character_urls_empty_refs():
    """无参考图时返回空列表。"""
    shot = StoryboardItem(index=1, shot_desc="x", character_names=["李华"])
    urls = _resolve_shot_character_urls(shot, [])
    assert urls == []
