/** 创作接口请求与响应类型 */

export interface CreateRequest {
  input: string;
  with_video?: boolean;
  /** 生成视频时是否在本地用 ffmpeg 将多段素材合成成片（默认 true，成片仅本地合成） */
  concat_segments?: boolean;
  /** 成片是否添加 BGM 背景音乐（MiniMax） */
  with_bgm?: boolean;
  /** 短剧角色参考：主角/配角+参考图，全自动生成视频时按镜使用 */
  character_references?: CharacterReference[];
  /** 可灵：false 时仅创建任务并返回 task_ids，前端轮询状态（绿/蓝/红）后调 concat-after-kling-tasks 剪辑，减少下载超时 */
  wait_for_tasks_before_concat?: boolean;
}

export interface VideoGenerationResult {
  video_mode: string;
  task_ids: string[];
  download_urls: string[];
  status_by_task: Record<string, string>;
  error?: string;
  /** LLM 推荐该模式时的一句话理由 */
  video_mode_reason?: string;
  /** 多段剪辑合并后的成片下载路径，仅当 concat_segments 且多段成功时返回 */
  merged_download_url?: string;
  /** BGM 背景音乐单独下载路径 */
  bgm_download_url?: string;
}

export interface StoryboardItem {
  index: number;
  shot_type?: string;
  shot_desc: string;
  /** 对白/旁白（后端可能返回 copy 或 copy_text） */
  copy?: string;
  copy_text?: string;
  duration_sec?: number;
  /** 本镜卖点标题，叠加在画面上方（仅商品短视频） */
  shot_title?: string;
  shot_arrangement?: string;
  shooting_approach?: string;
  camera_technique?: string;
  t2v_prompt?: string;
  /** 本镜生成方式：t2v文生视频 | i2v图生视频 | fl2v首尾帧 */
  generation_method?: string;
  /** 本镜人物角色名，与角色参考 name 对应（短剧按镜拉取参考图） */
  character_name?: string;
  /** 本镜出镜角色名列表；主角与配角同镜时多人，生成时按镜拉取每人参考图 */
  character_names?: string[] | null;
}

export interface ScriptDramaResult {
  storyboard: StoryboardItem[];
  prompts: string[];
  message?: string;
  video?: VideoGenerationResult;
}

export interface ClarifyResult {
  suggested_pipeline?: "script_drama";
  message: string;
}

export type CreateResponse = {
  input_type: "script" | "natural_language";
  pipeline: "script_drama" | "clarify";
  result: ScriptDramaResult | ClarifyResult;
  debug_router_note?: string;
};

/** 开发时直连后端 8000，避免代理 404；生产构建用 VITE_API_BASE 或相对路径 */
const API_BASE =
  import.meta.env.VITE_API_BASE !== undefined
    ? import.meta.env.VITE_API_BASE
    : import.meta.env.DEV
      ? "http://localhost:8000"
      : "";

const DEVICE_ID_KEY = "creative_device_id";

/** 获取或生成设备 ID（用于会员/积分接口），带 X-Device-ID 的请求头 */
export function getDeviceId(): string {
  let id = localStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    id = crypto.randomUUID?.() ?? `dev_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

export function headersWithDevice(): Record<string, string> {
  return { "X-Device-ID": getDeviceId() };
}

/** 步骤1：分类结果 */
export interface ClassifyResponse {
  input_type: "script" | "natural_language";
  pipeline: "script_drama" | "clarify";
  debug_router_note?: string;
  message?: string;
  suggested_pipeline?: "script_drama";
}

/** 步骤2：仅内容（无 video） */
export type ContentResponse = { pipeline: "script_drama"; result: ScriptDramaResult };

export async function createContent(
  input: string,
  withVideo: boolean = false,
  concatSegments: boolean = true,
  withBgm: boolean = false,
  characterReferences?: CharacterReference[],
  options?: { wait_for_tasks_before_concat?: boolean }
): Promise<CreateResponse> {
  const body: CreateRequest = {
    input,
    with_video: withVideo,
    concat_segments: concatSegments,
    with_bgm: withBgm,
  };
  if (characterReferences?.length) body.character_references = characterReferences;
  if (typeof options?.wait_for_tasks_before_concat === "boolean")
    body.wait_for_tasks_before_concat = options.wait_for_tasks_before_concat;
  const res = await fetch(`${API_BASE}/api/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`请求失败: ${res.status}`);
  return res.json();
}

/** 步骤1：识别输入类型与管线 */
export async function classifyInput(input: string): Promise<ClassifyResponse> {
  const res = await fetch(`${API_BASE}/api/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
  });
  if (!res.ok) throw new Error(`分类失败: ${res.status}`);
  return res.json();
}

/** 步骤2：仅生成脚本/分镜 */
export async function fetchContent(input: string): Promise<ContentResponse> {
  const res = await fetch(`${API_BASE}/api/script-drama/content`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, pipeline: "script_drama" }),
  });
  if (!res.ok) throw new Error(`生成内容失败: ${res.status}`);
  return res.json();
}

/** 演员参考：主角或配角 + 参考图（data URL，由上传文件生成） */
export interface CharacterReference {
  role: "主角" | "配角";
  /** 角色名，如 李华、小明；与分镜 character_name 对应，生成时按镜拉取该角色参考图 */
  name?: string;
  image_base64: string; // data:image/jpeg;base64,...
}

/** 步骤3：根据分镜生成视频；可选 BGM、本地合成、可灵任务模式 */
export async function fetchVideo(
  storyboard: StoryboardItem[],
  scriptSummary: string = "",
  options?: {
    characterReferences?: CharacterReference[];
    concatSegments?: boolean;
    /** 是否添加 BGM（MiniMax） */
    withBgm?: boolean;
    /** 可灵：false 时仅创建任务并返回 task_ids，前端轮询状态后点「开始剪辑」再下载剪辑 */
    waitForTasksBeforeConcat?: boolean;
  }
): Promise<VideoGenerationResult> {
  const body: Record<string, unknown> = {
    storyboard,
    script_summary: scriptSummary,
    concat_segments: options?.concatSegments !== false,
    with_bgm: options?.withBgm === true,
    pipeline: "script_drama",
  };
  if (typeof options?.waitForTasksBeforeConcat === "boolean") body.wait_for_tasks_before_concat = options.waitForTasksBeforeConcat;
  if (options?.characterReferences?.length) body.character_references = options.characterReferences;
  const res = await fetch(`${API_BASE}/api/video`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`生成视频失败: ${res.status}`);
  return res.json();
}

/** 短剧：单镜重新生成（可覆盖提示词），返回该镜的 download_url */
export async function regenerateShot(
  storyboard: StoryboardItem[],
  shotIndex: number,
  options: {
    overrideT2vPrompt?: string;
    characterReferences?: CharacterReference[];
    character_reference_image?: string;
  }
): Promise<{ download_url: string; shot_index: number }> {
  const body: Record<string, unknown> = {
    storyboard,
    shot_index: shotIndex,
    pipeline: "script_drama",
  };
  if (options.overrideT2vPrompt?.trim()) body.override_t2v_prompt = options.overrideT2vPrompt.trim();
  if (options.characterReferences?.length) {
    body.character_references = options.characterReferences.map((r) => ({
      role: r.role,
      name: (r.name || "").trim() || undefined,
      image_base64: r.image_base64,
    }));
  }
  if (options.character_reference_image?.trim()) body.character_reference_image = options.character_reference_image.trim();
  const res = await fetch(`${API_BASE}/api/video/regenerate-shot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `重新生成失败: ${res.status}`);
  }
  return res.json();
}

/** 短剧：从已有分段 URL 剪辑成片（转场 + 字幕） */
export async function concatFromSegments(
  segmentUrls: string[],
  storyboard: StoryboardItem[],
  options: { withCaptions?: boolean; withTransitions?: boolean } = {}
): Promise<{ merged_download_url: string }> {
  const body: Record<string, unknown> = {
    segment_urls: segmentUrls,
    storyboard,
    with_captions: options.withCaptions === true,
    with_voiceover: false,
    with_transitions: options.withTransitions !== false,
    pipeline: "script_drama",
  };
  const res = await fetch(`${API_BASE}/api/video/concat-from-segments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `剪辑失败: ${res.status}`);
  }
  return res.json();
}

/** 可灵任务状态：供前端按镜头展示 绿 succeed / 蓝 processing / 红 failed */
export interface KlingTaskStatusItem {
  task_id: string;
  status: "succeed" | "processing" | "failed";
  url?: string;
  task_status_msg?: string;
}

export interface KlingTaskStatusResponse {
  items: KlingTaskStatusItem[];
  all_succeed: boolean;
}

/** 查询可灵任务状态（轮询用）；全部成功后再调 concatAfterKlingTasks 剪辑 */
export async function getKlingTaskStatus(
  taskIds: string[],
  useOmni: boolean = true
): Promise<KlingTaskStatusResponse> {
  if (!taskIds.length) return { items: [], all_succeed: false };
  const q = new URLSearchParams({ task_ids: taskIds.join(","), use_omni: String(useOmni) });
  const res = await fetch(`${API_BASE}/api/video/kling-task-status?${q}`);
  if (!res.ok) throw new Error(`查询任务状态失败: ${res.status}`);
  return res.json();
}

/** 可灵任务全部成功后再下载并剪辑成片（使用任务返回的 URL 立即下载，减少超时） */
export async function concatAfterKlingTasks(params: {
  task_ids: string[];
  use_omni?: boolean;
  storyboard: StoryboardItem[];
  script_summary?: string;
  with_bgm?: boolean;
  character_references?: CharacterReference[];
}): Promise<{ merged_download_url: string; bgm_download_url?: string | null }> {
  const body: Record<string, unknown> = {
    task_ids: params.task_ids,
    use_omni: params.use_omni !== false,
    storyboard: params.storyboard,
    script_summary: params.script_summary ?? "",
    concat_segments: true,
    with_transitions: true,
    with_voiceover: false,
    with_bgm: params.with_bgm === true,
  };
  if (params.character_references?.length) body.character_references = params.character_references;
  const res = await fetch(`${API_BASE}/api/video/concat-after-kling-tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `剪辑失败: ${res.status}`);
  }
  return res.json();
}

// ---------- 数据持久化：任务 CRUD ----------

export type PipelineType = "script_drama";

export interface TaskSummary {
  id: string;
  pipeline: PipelineType;
  input: string;
  input_preview: string;
  title?: string | null;
  created_at: string;
  updated_at: string;
  merged_download_url?: string | null;
}

export interface TaskDetail {
  id: string;
  pipeline: PipelineType;
  input: string;
  title?: string | null;
  content_result: Record<string, unknown>;
  video_result?: Record<string, unknown> | null;
  merged_download_url?: string | null;
  /** 短剧角色快照：{ protagonists, supportingActors, defaultVoiceId }，刷新后可恢复 */
  character_references?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export async function createTask(payload: {
  pipeline: PipelineType;
  input: string;
  title?: string;
  content_result: Record<string, unknown>;
  video_result?: Record<string, unknown> | null;
  merged_download_url?: string | null;
  character_references?: Record<string, unknown> | null;
}): Promise<{ id: string }> {
  const res = await fetch(`${API_BASE}/api/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`保存失败: ${res.status}`);
  return res.json();
}

export async function updateTask(
  taskId: string,
  payload: {
    title?: string;
    content_result?: Record<string, unknown>;
    video_result?: Record<string, unknown> | null;
    merged_download_url?: string | null;
    character_references?: Record<string, unknown> | null;
  }
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`更新失败: ${res.status}`);
}

export async function listTasks(params?: {
  pipeline?: PipelineType;
  limit?: number;
  offset?: number;
}): Promise<TaskSummary[]> {
  const q = new URLSearchParams();
  if (params?.pipeline) q.set("pipeline", params.pipeline);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const url = `${API_BASE}/api/tasks${q.toString() ? `?${q}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`获取列表失败: ${res.status}`);
  return res.json();
}

export async function getTask(taskId: string): Promise<TaskDetail> {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`);
  if (!res.ok) throw new Error(`获取任务失败: ${res.status}`);
  return res.json();
}

export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`删除失败: ${res.status}`);
}

// ---------- 我的 · 会员与积分 ----------

export interface MembershipTierInfo {
  id: string;
  code: string;
  name: string;
  level: number;
  daily_task_quota: number;
  max_storyboard_shots: number;
  can_export_merged_video: boolean;
  price_per_month_credits: number;
  description?: string | null;
}

export interface UserMembershipSummary {
  tier_code: string;
  tier_name: string;
  level: number;
  daily_task_quota: number;
  max_storyboard_shots: number;
  can_export_merged_video: boolean;
  expires_at?: string | null;
}

export interface MeProfile {
  user_id: string;
  membership: UserMembershipSummary;
  points: { user_id: string; balance: number };
  usage_today: {
    content_count: number;
    video_count: number;
    total_used: number;
    quota: number;
    can_use: boolean;
  };
}

export interface PointTransactionItem {
  id: string;
  user_id: string;
  amount: number;
  type: string;
  ref_id?: string | null;
  description?: string | null;
  created_at: string;
}

export async function getMeProfile(): Promise<MeProfile> {
  const res = await fetch(`${API_BASE}/api/me/profile`, {
    headers: headersWithDevice(),
  });
  if (!res.ok) throw new Error(`获取个人信息失败: ${res.status}`);
  return res.json();
}

export async function getMembershipTiers(): Promise<MembershipTierInfo[]> {
  const res = await fetch(`${API_BASE}/api/membership/tiers`);
  if (!res.ok) throw new Error(`获取档位列表失败: ${res.status}`);
  return res.json();
}

export async function getMePointsHistory(params?: { limit?: number; offset?: number }): Promise<PointTransactionItem[]> {
  const q = new URLSearchParams();
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const url = `${API_BASE}/api/me/points/history${q.toString() ? `?${q}` : ""}`;
  const res = await fetch(url, { headers: headersWithDevice() });
  if (!res.ok) throw new Error(`获取积分流水失败: ${res.status}`);
  return res.json();
}

export async function signIn(): Promise<{ points_earned: number; balance_after: number; message: string }> {
  const res = await fetch(`${API_BASE}/api/me/points/sign-in`, {
    method: "POST",
    headers: { ...headersWithDevice(), "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `签到失败: ${res.status}`);
  }
  return res.json();
}

export async function redeemMembership(tierCode: string, months: number = 1): Promise<{ ok: boolean; membership_id: string; tier_code: string; months: number; points_spent: number }> {
  const res = await fetch(`${API_BASE}/api/me/membership/redeem`, {
    method: "POST",
    headers: { ...headersWithDevice(), "Content-Type": "application/json" },
    body: JSON.stringify({ tier_code: tierCode, months }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `兑换失败: ${res.status}`);
  }
  return res.json();
}
