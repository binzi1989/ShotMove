"""短剧创作 Agent：剧本 → 分镜 + 文生视频用 Prompt 列表"""
from app.schemas import ScriptDramaResult, StoryboardItem
from app.services import (
    has_llm,
    generate_storyboard_from_script_drama_llm,
    generate_storyboard_from_script_drama_template,
    refine_storyboard_t2v_prompts_llm,
)


def run_script_drama_agent(user_input: str) -> ScriptDramaResult:
    """
    从用户输入的剧本/对白生成结构化分镜与文生视频用 Prompt 列表。
    已配置 Kimi 时优先用深度思考模型生成分镜，失败则用模板；并在 message 中提示是否用了模板。
    有 LLM 时会对 t2v_prompt 做导演级精修（对齐 video-prompt-quality-skill），提升成片质量。
    """
    storyboard_raw = None
    kimi_error: str | None = None
    used_fallback = False
    if has_llm():
        storyboard_raw, kimi_error = generate_storyboard_from_script_drama_llm(user_input)
    if not storyboard_raw:
        storyboard_raw = generate_storyboard_from_script_drama_template(user_input)
        used_fallback = True

    # 有 LLM 时对分镜的 t2v_prompt 做精修，直接提升动画/视频生成提示词质量
    if has_llm() and storyboard_raw:
        refined = refine_storyboard_t2v_prompts_llm(
            storyboard_raw, "script_drama", script_snippet=user_input[:2000]
        )
        if refined:
            storyboard_raw = refined

    storyboard = [
        StoryboardItem(
            index=item["index"],
            shot_type=item.get("shot_type", ""),
            shot_desc=item["shot_desc"],
            copy_text=item.get("copy", ""),
            duration_sec=item.get("duration_sec"),
            shot_arrangement=item.get("shot_arrangement", ""),
            shooting_approach=item.get("shooting_approach", ""),
            camera_technique=item.get("camera_technique", ""),
            t2v_prompt=item.get("t2v_prompt", ""),
            generation_method=item.get("generation_method", "t2v"),
            character_name=item.get("character_name"),
            character_names=item.get("character_names"),
        )
        for item in storyboard_raw
    ]

    # 文生视频用 Prompt 列表：优先用导演级 t2v_prompt（含景别/角色/光线/运镜），否则用景别+画面+镜头手法拼
    prompts = [
        (s.t2v_prompt.strip() if (s.t2v_prompt and s.t2v_prompt.strip()) else f"{s.shot_type}，{s.shot_desc}。{s.camera_technique or '固定'}")
        for s in storyboard
    ]

    message = None
    if used_fallback:
        message = "分镜由本地模板生成。"
        if kimi_error:
            message += f" Kimi 调用失败：{kimi_error}"
        message += " 若需导演级分镜请配置 KIMI_API_KEY（在 platform.moonshot.ai 申请），并确认 .env 中 KIMI_BASE_URL=https://api.moonshot.ai/v1（勿用 .cn 以免 404）。"
    return ScriptDramaResult(
        storyboard=storyboard,
        prompts=prompts,
        message=message,
    )
