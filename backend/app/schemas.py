"""API 请求与响应的 Pydantic 模型"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class CreateRequest(BaseModel):
    """用户创作请求"""
    input: str = Field(..., description="用户输入：剧本、对白或自然语言需求")
    with_video: bool = Field(False, description="是否在生成脚本/分镜后调用 MiniMax 生成视频并返回下载链接")
    concat_segments: bool = Field(
        True,
        description="生成视频时是否在本地用 ffmpeg 将多段素材合成成片（成片仅本地合成，不用模型合成）",
    )
    with_bgm: bool = Field(False, description="成片是否添加 MiniMax 生成的 BGM 背景音乐")
    with_voiceover: bool = Field(False, description="成片是否添加 TTS 配音（讯飞超拟人，脚本/旁白转语音）")
    voice_id: Optional[str] = Field(None, description="配音音色：不传则自动推断。可选 vcn id 或中文名如聆飞逸、旁白男声等")
    with_transitions: bool = Field(True, description="生成视频并合并多段时是否加入转场（xfade）")
    with_captions: bool = Field(False, description="是否烧录字幕（默认关闭，仅配音不烧字）")
    caption_narration: bool = Field(True, description="短剧旁白是否加字幕；False=仅对话加。默认 True")
    with_stickers: bool = Field(False, description="是否叠加花纸/贴纸与氛围层（默认关闭）")
    subtitle_style: Literal["clean", "note", "bold"] = Field("clean", description="字幕样式：clean简洁 | note手帐纸条 | bold强对比")
    sticker_style: Literal["film", "sparkle", "note"] = Field("film", description="花纸/氛围风格：film胶片 | sparkle闪光 | note手帐")
    title_caption_style: Optional[str] = Field(
        None,
        description="标题花字样式：bubble_yellow黄底白字 | bubble_cream | bubble_mint | bubble_pink | bubble_sky | bubble_soft | classic；不传用后端 .env",
    )
    character_references: Optional[list["CharacterReferenceItem"]] = Field(
        None,
        description="短剧角色参考：主角/配角+参考图（data URL），全自动生成视频时按镜使用；需配置 BACKEND_PUBLIC_URL",
    )
    wait_for_tasks_before_concat: bool = Field(
        True,
        description="可灵任务是否在后端等全部完成后再下载剪辑。False 时仅创建任务并返回 task_ids，前端轮询 kling-task-status 展示每镜状态（绿/蓝/红），全部成功后再调 concat-after-kling-tasks 剪辑，避免下载超时。",
    )


class ClassifyResponse(BaseModel):
    """步骤1：分类结果（pipeline=clarify 时含引导文案）"""
    input_type: Literal["script", "natural_language"]
    pipeline: Literal["script_drama", "clarify"]
    debug_router_note: Optional[str] = None
    message: Optional[str] = None  # clarify 时展示的引导文案
    suggested_pipeline: Optional[Literal["script_drama"]] = None


class StoryboardItem(BaseModel):
    """单条分镜（以 script-drama-skill 为准：序号|景别|画面描述|对白/旁白|时长 + 具体细节）"""
    index: int
    shot_type: str = Field("", description="景别：如全景/中景/近景/特写")
    shot_desc: str = Field(..., description="画面描述")
    copy_text: str = Field("", alias="copy", description="文案/对白")
    duration_sec: Optional[int] = None
    shot_arrangement: str = Field("", description="镜头安排：本镜在整体中的位置与作用")
    shooting_approach: str = Field("", description="拍摄方式：如实景/绿幕、机位设想")
    camera_technique: str = Field("", description="镜头手法：推拉摇移跟、切/叠等衔接")
    t2v_prompt: str = Field("", description="文生视频用描述：采用「主体+场景+动作+风格+镜头语言」结构，可直接喂给文生视频API")
    generation_method: str = Field("t2v", description="本镜生成方式：t2v文生视频|i2v图生视频|fl2v首尾帧（与上一镜衔接）")
    shot_title: str = Field("", description="本镜卖点标题，叠加在画面上方（产品短视频；如「防水防泼溅」「触屏可用」）")
    character_name: Optional[str] = Field(None, description="本镜主要人物角色名（兼容单人或主角色）；与角色参考 name 对应")
    character_names: Optional[list[str]] = Field(None, description="本镜出镜角色名列表；主角与配角同镜时填多人，生成视频时按镜拉取每人参考图；有则优先于 character_name")

    model_config = {"populate_by_name": True}


class ContentRequest(BaseModel):
    """步骤2：生成内容请求"""
    input: str = Field(..., description="用户输入")
    pipeline: Literal["script_drama"] = Field(..., description="管线类型（短剧）")


class CharacterReferenceItem(BaseModel):
    """演员参考：角色名 + 主角/配角 + 参考图；名字与分镜按镜绑定，生成时按镜拉取对应参考图"""
    name: str = Field("", description="角色名，如 李华、小明、穆林；与分镜 character_name 对应，用于按镜拉取参考图")
    role: Literal["主角", "配角"] = Field(..., description="主角 / 配角；可有多名主角、多名配角")
    image_base64: Optional[str] = Field(None, description="参考图：data:image/jpeg;base64,... 或留空表示仅占位")


class VideoRequest(BaseModel):
    """步骤3：生成视频请求"""
    storyboard: list[StoryboardItem] = Field(..., description="分镜列表")
    script_summary: str = Field("", description="脚本摘要，用于 t2v_single")
    character_reference_image: Optional[str] = Field(
        None,
        description="[兼容] 人物参考图 URL；若有 character_references 则优先用主角图",
    )
    character_references: Optional[list[CharacterReferenceItem]] = Field(
        None,
        description="演员列表：主角/配角 + 上传参考图（data URL）。S2V 时优先使用主角图",
    )
    concat_segments: bool = Field(True, description="是否将多段视频剪辑合并成一片并返回成片下载链接")
    with_transitions: bool = Field(True, description="多段合并时是否加入转场（xfade）")
    with_bgm: bool = Field(False, description="成片是否添加 MiniMax BGM 背景音乐")
    with_voiceover: bool = Field(False, description="成片是否添加 TTS 配音（讯飞超拟人）")
    voice_id: Optional[str] = Field(
        None,
        description="配音音色：不传则自动推断。可选 x6_lingfeiyi_pro(聆飞逸男)/x6_lingxiaoxuan_pro(聆小璇女)/x5_lingyuzhao_flow(聆玉昭女)/x6_lingxiaoyue_pro(聆小玥女)/x6_lingyuyan_pro(聆玉言女)/x6_wennuancixingnansheng_mini(温暖磁性男)/x6_pangbainan1_pro(旁白男)，或传中文名如聆飞逸、旁白男声",
    )
    shot_voice_ids: Optional[list[Optional[str]]] = Field(
        None,
        description="短剧按镜配音：每镜对应一个音色 id 或空（该镜自动推断）。长度与 storyboard 一致时生效，否则用 voice_id",
    )
    with_captions: bool = Field(False, description="是否烧录字幕（默认关闭，仅配音不烧字）")
    caption_narration: bool = Field(
        True,
        description="短剧专用：旁白是否加字幕。True=对话与旁白都加；False=仅对话加字幕、旁白不加。默认 True",
    )
    with_stickers: bool = Field(False, description="是否叠加花纸/贴纸与氛围层（默认关闭）")
    subtitle_style: Literal["clean", "note", "bold"] = Field("clean", description="字幕样式：clean简洁 | note手帐纸条 | bold强对比")
    sticker_style: Literal["film", "sparkle", "note"] = Field("film", description="花纸/氛围风格：film胶片 | sparkle闪光 | note手帐")
    title_caption_style: Optional[str] = Field(
        None,
        description="标题花字样式：bubble_yellow黄底白字 | bubble_cream | bubble_mint | ... | classic；不传用 .env",
    )
    pipeline: Optional[Literal["script_drama"]] = Field(
        None,
        description="管线类型，供 LLM 推荐视频生成方式时使用（短剧）",
    )
    wait_for_tasks_before_concat: bool = Field(
        True,
        description="可灵：True=后端等全部任务完成再下载剪辑；False=仅创建任务并返回 task_ids，前端轮询状态（绿/蓝/红）后点「开始剪辑」再下载剪辑，减少超时。",
    )


class RegenerateShotRequest(BaseModel):
    """单镜重新生成请求（短剧：按镜调整提示词后重生成）"""
    storyboard: list[StoryboardItem] = Field(..., description="完整分镜列表")
    shot_index: int = Field(..., ge=0, description="要重新生成的镜头下标")
    pipeline: Literal["script_drama"] = Field("script_drama")
    override_t2v_prompt: Optional[str] = Field(None, description="覆盖该镜的 t2v 提示词；不传则用分镜中的 t2v_prompt")
    character_reference_image: Optional[str] = None
    character_references: Optional[list[CharacterReferenceItem]] = None


class VoiceoverOnlyRequest(BaseModel):
    """仅对已有成片按分镜添加配音（不重新生成视频）。"""
    merged_url: str = Field(..., description="已合并的成片路径，如 /api/merged/xxx.mp4")
    storyboard: list[StoryboardItem] = Field(..., description="分镜列表，用于按镜台词与时长")
    script_summary: str = Field("", description="剧本摘要，用于情绪推断上下文")
    voice_id: Optional[str] = Field(None, description="配音音色，不传则自动推断")
    shot_voice_ids: Optional[list[Optional[str]]] = Field(None, description="每镜音色，长度与 storyboard 一致时生效")


class ConcatAfterKlingTasksRequest(BaseModel):
    """可灵任务全部成功后再下载并剪辑成片（避免下载超时、且前端可先展示每镜状态 绿/蓝/红）"""
    task_ids: list[str] = Field(..., description="可灵任务 ID 列表，与分镜顺序一致")
    use_omni: bool = Field(True, description="是否用 Omni-Video 接口查询（短剧/商品多为 true）")
    storyboard: list[StoryboardItem] = Field(..., description="分镜列表（用于字幕、按镜配音与时长对齐）")
    script_summary: str = Field("", description="剧本摘要，用于情绪推断与配音")
    concat_segments: bool = True
    with_transitions: bool = True
    with_voiceover: bool = False
    with_bgm: bool = False
    voice_id: Optional[str] = None
    shot_voice_ids: Optional[list[Optional[str]]] = None
    character_references: Optional[list[CharacterReferenceItem]] = None


class ConcatFromSegmentsRequest(BaseModel):
    """从已有分镜视频 URL 列表剪辑成片（短剧：确认无误后整体剪辑）"""
    segment_urls: list[str] = Field(..., description="已生成的分段视频 URL 列表，顺序与 storyboard 一致")
    storyboard: list[StoryboardItem] = Field(..., description="分镜列表（用于字幕、画面标题与按镜配音）")
    with_captions: bool = False
    caption_narration: bool = Field(True, description="旁白是否加字幕。False=仅对话加字幕。默认 True")
    with_voiceover: bool = Field(False, description="是否按镜添加讯飞配音（无台词镜静音）")
    voice_id: Optional[str] = Field(
        None,
        description="配音音色：不传则自动推断。可选 vcn id 或中文名如聆飞逸、聆小璇、旁白男声等",
    )
    shot_voice_ids: Optional[list[Optional[str]]] = Field(
        None,
        description="短剧按镜配音：每镜对应一个音色 id 或空（该镜自动推断）。长度与 storyboard 一致时生效",
    )
    with_transitions: bool = True
    title_caption_style: Optional[str] = Field(None, description="标题花字样式：bubble_yellow黄底白字 | bubble_cream | ... | classic；不传用 .env")
    pipeline: Literal["script_drama"] = Field("script_drama")


class VideoGenerationResult(BaseModel):
    """MiniMax 视频生成结果"""
    video_mode: str = Field(..., description="t2v_single / t2v_multi / i2v_multi / s2v_multi / smart_multiframe(智能多帧)")
    task_ids: list[str] = Field(default_factory=list)
    download_urls: list[str] = Field(default_factory=list)
    status_by_task: dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None
    video_mode_reason: Optional[str] = Field(None, description="由 LLM 推荐该模式时的一句话理由")
    merged_download_url: Optional[str] = Field(
        None,
        description="多段剪辑合并后的成片下载路径（/api/merged/xxx.mp4），仅当 concat_segments 且多段成功时返回",
    )
    voiceover_download_url: Optional[str] = Field(None, description="配音音频单独下载路径（/api/merged/xxx_voice.mp3）")
    bgm_download_url: Optional[str] = Field(None, description="BGM 背景音乐单独下载路径（/api/merged/xxx_bgm.mp3）")


class ScriptDramaResult(BaseModel):
    """短剧创作管线结果"""
    storyboard: list[StoryboardItem] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list, description="文生视频用 Prompt 列表")
    message: Optional[str] = None
    video: Optional[VideoGenerationResult] = None


class ClarifyResult(BaseModel):
    """需求澄清结果（自然语言且意图不清）"""
    suggested_pipeline: Optional[Literal["script_drama"]] = None
    message: str


class CreateResponse(BaseModel):
    """创作接口统一响应"""
    input_type: Literal["script", "natural_language"]
    pipeline: Literal["script_drama", "clarify"]
    result: ScriptDramaResult | ClarifyResult
    debug_router_note: Optional[str] = None


# ---------- 数据持久化：任务 CRUD ----------

class TaskCreate(BaseModel):
    """创建/保存任务请求"""
    pipeline: Literal["script_drama"] = Field(..., description="管线类型")
    input: str = Field(..., description="用户输入（链接或剧本）")
    title: Optional[str] = Field(None, description="可选标题")
    content_result: dict = Field(..., description="步骤2 内容结果 JSON，可为 {} 表示生成中")
    video_result: Optional[dict] = Field(None, description="步骤3 视频结果 JSON")
    merged_download_url: Optional[str] = Field(None, description="成片下载路径")
    character_references: Optional[dict] = Field(None, description="短剧角色快照：{ protagonists, supportingActors, defaultVoiceId }，刷新后可恢复")


class TaskUpdate(BaseModel):
    """更新任务（仅更新传入的字段）"""
    title: Optional[str] = None
    content_result: Optional[dict] = None
    video_result: Optional[dict] = None
    merged_download_url: Optional[str] = None
    character_references: Optional[dict] = None


class TaskSummary(BaseModel):
    """任务列表项（不含大字段）"""
    id: str
    pipeline: Literal["script_drama"]
    input: str
    input_preview: str = ""
    title: Optional[str] = None
    created_at: str
    updated_at: str
    merged_download_url: Optional[str] = None


class TaskDetail(BaseModel):
    """任务详情（含 content_result、video_result、character_references）"""
    id: str
    pipeline: Literal["script_drama"]
    input: str
    title: Optional[str] = None
    content_result: dict
    video_result: Optional[dict] = None
    merged_download_url: Optional[str] = None
    character_references: Optional[dict] = None
    created_at: str
    updated_at: str


# ---------- 会员体系 ----------

class MembershipTier(BaseModel):
    """会员档位配置"""
    id: str
    code: str
    name: str
    level: int
    daily_task_quota: int = Field(..., description="-1 表示不限")
    max_storyboard_shots: int = Field(..., description="-1 表示不限")
    can_export_merged_video: bool
    price_per_month_credits: int
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserMembershipSummary(BaseModel):
    """当前用户会员摘要（含免费默认）"""
    tier_code: str
    tier_name: str
    level: int
    daily_task_quota: int
    max_storyboard_shots: int
    can_export_merged_video: bool
    expires_at: Optional[str] = Field(None, description="付费会员过期时间，免费为 null")


class RedeemMembershipRequest(BaseModel):
    """积分兑换会员请求"""
    tier_code: str = Field(..., description="档位 code：basic / premium / vip")
    months: int = Field(1, ge=1, le=24, description="兑换月数")


# ---------- 积分体系 ----------

class PointBalance(BaseModel):
    """用户积分余额"""
    user_id: str
    balance: int


class PointTransactionItem(BaseModel):
    """单条积分流水"""
    id: str
    user_id: str
    amount: int
    type: str
    ref_id: Optional[str] = None
    description: Optional[str] = None
    created_at: str


class SignInResponse(BaseModel):
    """签到响应"""
    points_earned: int
    balance_after: int
    message: str = "签到成功"


# 解析 CreateRequest 中 character_references 的前向引用
CreateRequest.model_rebuild()
