/** 小剧 · 短剧生成：粘贴剧本/对白 → 上传角色参考图 → 分镜 + 视频 */
import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import {
  fetchContent,
  fetchVideo,
  regenerateShot,
  concatFromSegments,
  getKlingTaskStatus,
  concatAfterKlingTasks,
  createTask,
  updateTask,
  getTask,
  type ContentResponse,
  type ScriptDramaResult,
  type VideoGenerationResult,
  type StoryboardItem,
  type CharacterReference,
  type KlingTaskStatusItem,
} from "../api";
import TimelineEditor, { buildTimelineFromSegments } from "../components/TimelineEditor";
import MediaBin from "../components/TimelineEditor/MediaBin";
import type { MediaBinItem, TimelineClip } from "../types/timeline";

const NAME = "小剧";

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

function isScriptContent(r: ContentResponse): r is { pipeline: "script_drama"; result: ScriptDramaResult } {
  return r.pipeline === "script_drama";
}

type StepStatus = "pending" | "running" | "done" | "skip";

export default function DramaPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const taskIdFromUrl = searchParams.get("taskId");

  const [input, setInput] = useState("");
  /** 是否添加 BGM（MiniMax） */
  const [withBgm, setWithBgm] = useState(false);
  /** 主角可多名：id、角色名、参考图；生成时按镜拉取对应角色参考图 */
  const [protagonists, setProtagonists] = useState<Array<{ id: string; name: string; dataUrl: string | null }>>([{ id: crypto.randomUUID(), name: "", dataUrl: null }]);
  /** 配角可多名：id、角色名、参考图 */
  const [supportingActors, setSupportingActors] = useState<Array<{ id: string; name: string; dataUrl: string | null }>>([]);
  /** 每镜绑定的角色名（与角色参考 name 对应），生成视频时按镜拉取该角色参考图 */
  /** 每镜选中的「本镜人物」；多选时以 shotCharacterNamesList 为准 */
  const [shotCharacterNames, setShotCharacterNames] = useState<Record<number, string>>({});
  /** 每镜出镜角色多选（用于参考图：主配角同镜时选多人）；未选时用分镜的 character_names/character_name */
  const [shotCharacterNamesList, setShotCharacterNamesList] = useState<Record<number, string[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [contentResult, setContentResult] = useState<ContentResponse | null>(null);
  const [videoResult, setVideoResult] = useState<VideoGenerationResult | null>(null);
  const [step1Status, setStep1Status] = useState<StepStatus>("pending");
  const [step2Status, setStep2Status] = useState<StepStatus>("pending");
  const [savedTaskId, setSavedTaskId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  /** 半自动：用户编辑后的「实际文生视频提示词」按镜头 index 存储，生成视频时优先使用 */
  const [storyboardPromptEdits, setStoryboardPromptEdits] = useState<Record<number, string>>({});
  /** 用户删除镜头后的分镜列表（null 表示未删减，用接口/生成结果原样）；删除后重排 index 为 1..n */
  const [storyboardOverride, setStoryboardOverride] = useState<StoryboardItem[] | null>(null);
  /** 各镜头当前视频 URL（与 storyboard 一一对应；重新生成单镜后更新对应项） */
  const [segmentUrls, setSegmentUrls] = useState<string[]>([]);
  /** 正在重新生成的镜头下标 */
  const [regeneratingShotIndex, setRegeneratingShotIndex] = useState<number | null>(null);
  /** 正在执行「确认并剪辑」 */
  const [concatInProgress, setConcatInProgress] = useState(false);
  /** 可灵任务状态（仅当有 task_ids 且未成片时轮询）：每镜 绿 succeed / 蓝 processing / 红 failed */
  const [klingStatusItems, setKlingStatusItems] = useState<KlingTaskStatusItem[] | null>(null);
  /** 正在执行「全部成功后开始剪辑」（concat-after-kling-tasks） */
  const [concatAfterKlingInProgress, setConcatAfterKlingInProgress] = useState(false);
  /** 是否根据镜头描述与对白自动识别本镜人物（不依赖分镜接口的 character_names） */
  const [autoSelectCharacters, setAutoSelectCharacters] = useState(false);
  /** 生成视频时是否先展示每镜状态（绿/蓝/红）再点「开始剪辑」，推荐开启以减少下载超时 */
  const [showTaskStatusFirst, setShowTaskStatusFirst] = useState(true);
  /** 时间线视频轨顺序（片段下标）；null 表示未改顺序，按 0,1,2,... */
  const [timelineOrder, setTimelineOrder] = useState<number[] | null>(null);
  /** 用户拖入视频轨的上传素材（segmentIndex -1），与生成镜头一起展示 */
  const [uploadedVideoClips, setUploadedVideoClips] = useState<TimelineClip[]>([]);
  /** 用户添加的音乐轨（上传的音频文件），与 BGM 等一起展示在时间线 */
  const [userMusicTrack, setUserMusicTrack] = useState<{ url: string; name: string; durationSec: number } | null>(null);
  /** 右侧草稿参数（可编辑） */
  const [draftName, setDraftName] = useState("未命名");

  const scriptInputRef = useRef<HTMLTextAreaElement>(null);
  const userMusicUrlsRef = useRef<typeof userMusicTrack>(null);
  userMusicUrlsRef.current = userMusicTrack;

  const running = step1Status === "running" || step2Status === "running" || concatInProgress || concatAfterKlingInProgress;

  /** 添加音乐轨：上传音频文件，作为时间线中的一条音轨（可调节音量） */
  const handleAddMusic = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !file.type.startsWith("audio/")) return;
    const url = URL.createObjectURL(file);
    const audio = document.createElement("audio");
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      setUserMusicTrack({ url, name: file.name, durationSec: audio.duration || 0 });
      e.target.value = "";
    };
    audio.onerror = () => {
      setUserMusicTrack({ url, name: file.name, durationSec: 0 });
      e.target.value = "";
    };
    audio.src = url;
  };

  /** 当前生效的分镜列表（用户删除镜头后为删减并重排 index 的列表，否则为接口/生成结果） */
  const effectiveStoryboard: StoryboardItem[] =
    contentResult && isScriptContent(contentResult)
      ? (storyboardOverride ?? contentResult.result.storyboard)
      : [];

  const handleDeleteShot = (positionIndex: number) => {
    if (effectiveStoryboard.length <= 1) return;
    const list = effectiveStoryboard;
    const newList = list
      .filter((_, i) => i !== positionIndex)
      .map((s, i) => ({ ...s, index: i + 1 })) as StoryboardItem[];
    setStoryboardOverride(newList);
    const newEdits: Record<number, string> = {};
    const newCharNames: Record<number, string> = {};
    const newCharNamesList: Record<number, string[]> = {};
    newList.forEach((newShot, j) => {
      const oldPos = j < positionIndex ? j : j + 1;
      const oldShot = list[oldPos];
      const oldIdx = (oldShot as { index?: number }).index ?? oldPos + 1;
      if (storyboardPromptEdits[oldIdx] !== undefined) newEdits[newShot.index] = storyboardPromptEdits[oldIdx];
      if (shotCharacterNames[oldIdx] !== undefined) newCharNames[newShot.index] = shotCharacterNames[oldIdx];
      if (shotCharacterNamesList[oldIdx]?.length) newCharNamesList[newShot.index] = shotCharacterNamesList[oldIdx];
    });
    setStoryboardPromptEdits(newEdits);
    setShotCharacterNames(newCharNames);
    setShotCharacterNamesList((prev) => (Object.keys(newCharNamesList).length ? { ...newCharNamesList } : prev));
    setSegmentUrls((prev) => {
      if (prev.length !== list.length) return prev;
      return prev.filter((_, i) => i !== positionIndex);
    });
  };

  useEffect(() => {
    return () => {
      if (userMusicUrlsRef.current?.url) URL.revokeObjectURL(userMusicUrlsRef.current.url);
    };
  }, []);

  // 可灵任务状态轮询：有 task_ids 且未成片时每 5s 查一次，用于展示每镜 绿/蓝/红
  useEffect(() => {
    const taskIds = videoResult?.task_ids;
    if (!taskIds?.length || videoResult?.merged_download_url) {
      setKlingStatusItems(null);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await getKlingTaskStatus(taskIds, true);
        if (!cancelled) setKlingStatusItems(data.items);
      } catch (_) {}
    };
    poll();
    const t = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [videoResult?.task_ids?.join(","), videoResult?.merged_download_url]);

  useEffect(() => {
    if (!taskIdFromUrl) return;
    setError(null);
    getTask(taskIdFromUrl)
      .then((task) => {
        if (task.pipeline !== "script_drama") return;
        setInput(task.input);
        setSavedTaskId(task.id);
        const cr = task.content_result as unknown as ScriptDramaResult;
        const hasContent = cr && Array.isArray(cr.storyboard) && cr.storyboard.length > 0;
        if (hasContent) {
          setContentResult({ pipeline: "script_drama", result: cr });
          setStoryboardOverride(null);
          const vr = (task.video_result as unknown as VideoGenerationResult) ?? null;
          setVideoResult(vr);
          setSegmentUrls(vr?.download_urls ?? []);
          setStep1Status("done");
          setStep2Status(task.video_result ? "done" : "pending");
        } else {
          setContentResult(null);
          setVideoResult(null);
          setSegmentUrls([]);
          setStep1Status("pending");
          setStep2Status("pending");
        }
        const refs = task.character_references as { protagonists?: Array<{ id: string; name: string; dataUrl: string | null }>; supportingActors?: Array<{ id: string; name: string; dataUrl: string | null }> } | undefined;
        if (Array.isArray(refs?.protagonists)) {
          setProtagonists(refs.protagonists.length ? refs.protagonists.map((p) => ({
            id: p.id || crypto.randomUUID(),
            name: p.name ?? "",
            dataUrl: p.dataUrl ?? null,
          })) : [{ id: crypto.randomUUID(), name: "", dataUrl: null }]);
        }
        if (Array.isArray(refs?.supportingActors)) {
          setSupportingActors(refs.supportingActors.map((s) => ({
            id: s.id || crypto.randomUUID(),
            name: s.name ?? "",
            dataUrl: s.dataUrl ?? null,
          })));
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载任务失败"));
  }, [taskIdFromUrl]);

  /** 分镜完成后，用分镜中的 character_names/character_name 自动勾选「本镜人物」，用户只需微调 */
  useEffect(() => {
    if (!contentResult || !isScriptContent(contentResult)) return;
    const storyboard = contentResult.result.storyboard;
    if (!Array.isArray(storyboard) || storyboard.length === 0) return;
    const listByIndex: Record<number, string[]> = {};
    const firstByIndex: Record<number, string> = {};
    for (const s of storyboard) {
      const names = (s as { character_names?: string[] }).character_names ?? ((s as { character_name?: string }).character_name ? [(s as { character_name?: string }).character_name!] : []);
      const list = names.map((n) => (n || "").trim()).filter(Boolean);
      if (list.length) {
        listByIndex[s.index] = list;
        firstByIndex[s.index] = list[0];
      }
    }
    if (Object.keys(listByIndex).length > 0) {
      setShotCharacterNamesList(listByIndex);
      setShotCharacterNames(firstByIndex);
    }
  }, [contentResult]);

  function buildCharacterReferences(): CharacterReference[] | undefined {
    const list: CharacterReference[] = [];
    protagonists.forEach((p) => {
      if (p.dataUrl) list.push({ role: "主角", name: p.name.trim() || undefined, image_base64: p.dataUrl });
    });
    supportingActors.forEach((s) => {
      if (s.dataUrl) list.push({ role: "配角", name: s.name.trim() || undefined, image_base64: s.dataUrl });
    });
    return list.length ? list : undefined;
  }

  /** 角色快照，用于保存到任务；刷新后可恢复 */
  function buildCharacterReferencesSnapshot(): Record<string, unknown> {
    return {
      protagonists: protagonists.map((p) => ({ id: p.id, name: p.name, dataUrl: p.dataUrl })),
      supportingActors: supportingActors.map((s) => ({ id: s.id, name: s.name, dataUrl: s.dataUrl })),
    };
  }

  /** 所有已填写的角色名（用于按镜选择「本镜人物/出镜角色」） */
  const characterNameOptions = [
    ...protagonists.filter((p) => p.name.trim()).map((p) => p.name.trim()),
    ...supportingActors.filter((s) => s.name.trim()).map((s) => s.name.trim()),
  ];

  /** 根据镜头描述、对白与提示词文案识别本镜出镜角色（不依赖分镜接口，通用识别） */
  function inferCharactersFromShotText(shot: StoryboardItem): string[] {
    const copy = (shot.copy ?? (shot as { copy_text?: string }).copy_text ?? "").trim();
    const text = [
      (shot.shot_desc ?? "").trim(),
      copy,
      ((shot as { t2v_prompt?: string }).t2v_prompt ?? "").trim(),
    ].filter(Boolean).join(" ");
    const found = characterNameOptions.filter((name) => name && text.includes(name));
    if (found.length === 0) return [];
    const speakerMatch = copy.match(/^([A-Za-z\u4e00-\u9fa5]{1,6})\s*[：:]\s*/);
    const speaker = speakerMatch ? speakerMatch[1].trim() : null;
    if (speaker && characterNameOptions.includes(speaker)) {
      const rest = found.filter((n) => n !== speaker);
      return [speaker, ...rest];
    }
    return found;
  }

  /** 开启「自动选择角色」时，根据文案为每镜识别并填充本镜人物 */
  useEffect(() => {
    if (!autoSelectCharacters || effectiveStoryboard.length === 0 || characterNameOptions.length === 0) return;
    const listByIndex: Record<number, string[]> = {};
    const firstByIndex: Record<number, string> = {};
    for (const s of effectiveStoryboard) {
      const list = inferCharactersFromShotText(s);
      if (list.length) {
        listByIndex[s.index] = list;
        firstByIndex[s.index] = list[0];
      }
    }
    if (Object.keys(listByIndex).length > 0) {
      setShotCharacterNamesList((prev) => ({ ...prev, ...listByIndex }));
      setShotCharacterNames((prev) => ({ ...prev, ...firstByIndex }));
    }
  }, [autoSelectCharacters, contentResult, storyboardOverride, protagonists, supportingActors]);

  /** 本镜出镜角色列表（用于参考图）；用户多选优先，否则用分镜的 character_names/character_name */
  function getSelectedCharactersForShot(s: StoryboardItem): string[] {
    const list = shotCharacterNamesList[s.index];
    if (list && list.length > 0) return list;
    const names = (s as { character_names?: string[] }).character_names;
    if (names && names.length > 0) return names;
    const one = (s as { character_name?: string }).character_name;
    return one ? [one] : [];
  }

  /** 切换某镜是否包含某角色（多选，主配角同镜时勾选多人） */
  function toggleShotCharacter(shotIndex: number, name: string) {
    const s = effectiveStoryboard.find((sh) => sh.index === shotIndex);
    if (!s) return;
    const current = getSelectedCharactersForShot(s);
    const next = current.includes(name) ? current.filter((x) => x !== name) : [...current, name];
    setShotCharacterNamesList((prev) => ({ ...prev, [shotIndex]: next }));
    setShotCharacterNames((prev) => ({ ...prev, [shotIndex]: next[0] ?? "" }));
  }

  const handleSubmit = async () => {
    if (!input.trim()) return;
    setError(null);
    setContentResult(null);
    setVideoResult(null);
    setStoryboardPromptEdits({});
    setShotCharacterNames({});
    setShotCharacterNamesList({});
    setStep1Status("running");
    setStep2Status("pending");

    try {
      const content = await fetchContent(input.trim());
      if (!isScriptContent(content)) return;
      setContentResult(content);
      setStoryboardOverride(null);
      setStep1Status("done");
      setStep2Status("pending");
      try {
        const { id } = await createTask({
          pipeline: "script_drama",
          input: input.trim(),
          content_result: content.result as unknown as Record<string, unknown>,
          character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
        });
        setSavedTaskId(id);
        setSearchParams((prev) => {
          const p = new URLSearchParams(prev);
          p.set("taskId", id);
          return p;
        });
      } catch (_) {
        // 自动保存失败不影响主流程
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
      setStep1Status((s) => (s === "running" ? "pending" : s));
      setStep2Status((s) => (s === "running" ? "pending" : s));
    }
  };

  const handleSave = async () => {
    if (!contentResult || !isScriptContent(contentResult)) return;
    setSaving(true);
    setError(null);
    try {
      if (savedTaskId) {
        await updateTask(savedTaskId, {
          content_result: { ...contentResult.result, storyboard: effectiveStoryboard } as unknown as Record<string, unknown>,
          video_result: videoResult ? (videoResult as unknown as Record<string, unknown>) : undefined,
          merged_download_url: videoResult?.merged_download_url ?? undefined,
          character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
        });
      } else {
        const { id } = await createTask({
          pipeline: "script_drama",
          input,
          content_result: { ...contentResult.result, storyboard: effectiveStoryboard } as unknown as Record<string, unknown>,
          video_result: videoResult ? (videoResult as unknown as Record<string, unknown>) : undefined,
          merged_download_url: videoResult?.merged_download_url ?? undefined,
          character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
        });
        setSavedTaskId(id);
        setSearchParams((prev) => {
          const p = new URLSearchParams(prev);
          p.set("taskId", id);
          return p;
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleGenerateVideo = async () => {
    if (!contentResult || !isScriptContent(contentResult)) return;
    const rawStoryboard = effectiveStoryboard;
    if (rawStoryboard.length === 0) return;
    setStep2Status("running");
    setError(null);
    try {
      const storyboardToSend = rawStoryboard.map((s: StoryboardItem) => {
        const edited = storyboardPromptEdits[s.index];
        const selected = getSelectedCharactersForShot(s);
        const character_names = selected.length ? selected : undefined;
        const character_name = selected[0] ?? (s as { character_name?: string }).character_name ?? undefined;
        let item: StoryboardItem & { character_name?: string; character_names?: string[] } = {
          ...s,
          character_name,
          character_names: character_names ?? undefined,
        };
        if (edited != null && edited.trim() !== "") {
          item = { ...item, t2v_prompt: edited.trim() };
        }
        return item;
      });
      const scriptSummary = rawStoryboard.map((s) => s.copy ?? s.copy_text ?? "").join(" ").slice(0, 500);
      const video = await fetchVideo(storyboardToSend, scriptSummary, {
        characterReferences: buildCharacterReferences(),
        concatSegments: true,
        withBgm: withBgm,
        waitForTasksBeforeConcat: !showTaskStatusFirst,
      });
      setVideoResult(video);
      setSegmentUrls(video.download_urls ?? []);
      setStep2Status("done");
      if (savedTaskId) {
        try {
          await updateTask(savedTaskId, {
            content_result: { ...contentResult.result, storyboard: effectiveStoryboard } as unknown as Record<string, unknown>,
            video_result: video as unknown as Record<string, unknown>,
            merged_download_url: video.merged_download_url ?? undefined,
            character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
          });
        } catch (_) {}
      } else {
        try {
          const { id } = await createTask({
            pipeline: "script_drama",
            input,
            content_result: { ...contentResult.result, storyboard: effectiveStoryboard } as unknown as Record<string, unknown>,
            video_result: video as unknown as Record<string, unknown>,
            merged_download_url: video.merged_download_url ?? undefined,
            character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
          });
          setSavedTaskId(id);
          setSearchParams((prev) => {
            const p = new URLSearchParams(prev);
            p.set("taskId", id);
            return p;
          });
        } catch (_) {}
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成视频失败");
      setStep2Status("pending");
    }
  };

  const handleRegenerateShot = async (shotIndex: number) => {
    if (!contentResult || !isScriptContent(contentResult)) return;
    const storyboard = effectiveStoryboard;
    if (shotIndex < 0 || shotIndex >= storyboard.length) return;
    const shot = storyboard[shotIndex];
    const selectedShot = getSelectedCharactersForShot(shot);
    const character_names = selectedShot.length ? selectedShot : undefined;
    const character_name = selectedShot[0] ?? (shot as { character_name?: string }).character_name ?? undefined;
    const shotWithChar = { ...shot, character_name, character_names } as StoryboardItem & { character_name?: string; character_names?: string[] };
    const storyboardWithChar = storyboard.map((s, i) => {
      if (i === shotIndex) return shotWithChar;
      const sel = getSelectedCharactersForShot(s);
      const cns = sel.length ? sel : undefined;
      const cn = sel[0] ?? (s as { character_name?: string }).character_name ?? undefined;
      return { ...s, character_name: cn, character_names: cns };
    });
    const overridePrompt = storyboardPromptEdits[shot.index] ?? shot.t2v_prompt ?? "";
    setRegeneratingShotIndex(shotIndex);
    setError(null);
    try {
      const data = await regenerateShot(storyboardWithChar, shotIndex, {
        overrideT2vPrompt: overridePrompt.trim() || undefined,
        characterReferences: buildCharacterReferences(),
      });
      setSegmentUrls((prev) => {
        const next = prev.length ? [...prev] : [...(videoResult?.download_urls ?? [])];
        while (next.length <= shotIndex) next.push("");
        next[shotIndex] = data.download_url;
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "该镜重新生成失败");
    } finally {
      setRegeneratingShotIndex(null);
    }
  };

  const handleConcatAfterKling = async () => {
    if (!contentResult || !isScriptContent(contentResult) || !videoResult?.task_ids?.length) return;
    setConcatAfterKlingInProgress(true);
    setError(null);
    try {
      const scriptSummary = effectiveStoryboard.map((s) => s.copy ?? s.copy_text ?? "").join(" ").slice(0, 800);
      const out = await concatAfterKlingTasks({
        task_ids: videoResult.task_ids,
        use_omni: true,
        storyboard: effectiveStoryboard,
        script_summary: scriptSummary,
        with_bgm: withBgm,
        character_references: buildCharacterReferences() ?? undefined,
      });
      setVideoResult((prev) =>
        prev
          ? {
              ...prev,
              merged_download_url: out.merged_download_url,
              bgm_download_url: out.bgm_download_url ?? undefined,
              download_urls: prev.download_urls,
            }
          : null
      );
      setKlingStatusItems(null);
      if (savedTaskId) {
        try {
          await updateTask(savedTaskId, {
            merged_download_url: out.merged_download_url,
            video_result: {
              ...videoResult,
              merged_download_url: out.merged_download_url,
              bgm_download_url: out.bgm_download_url,
            } as unknown as Record<string, unknown>,
          });
        } catch (_) {}
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "剪辑失败");
    } finally {
      setConcatAfterKlingInProgress(false);
    }
  };

  const handleConcatConfirm = async () => {
    if (!contentResult || !isScriptContent(contentResult)) return;
    const storyboard = effectiveStoryboard;
    const urls = segmentUrls.length > 0 ? segmentUrls : (videoResult?.download_urls ?? []);
    if (urls.length !== storyboard.length || urls.some((u) => !u?.trim())) {
      setError("请确保每个镜头都有视频后再确认剪辑");
      return;
    }
    const order = timelineOrder ?? urls.map((_, i) => i);
    const orderedUrls = order.map((i) => urls[i]).filter(Boolean);
    const orderedStoryboard = order.map((origIdx, newPos) => ({
      ...storyboard[origIdx],
      index: newPos + 1,
    })) as StoryboardItem[];
    if (orderedUrls.length !== orderedStoryboard.length) {
      setError("时间线顺序与分镜数量不一致，请刷新后重试");
      return;
    }
    setConcatInProgress(true);
    setError(null);
    try {
      const data = await concatFromSegments(orderedUrls, orderedStoryboard, {
        withCaptions: false,
        withTransitions: true,
      });
      const newVideoResult = {
        ...(videoResult ?? {
          video_mode: "",
          task_ids: [],
          download_urls: urls,
          status_by_task: {},
        }),
        merged_download_url: data.merged_download_url,
      };
      setVideoResult(newVideoResult);
      if (savedTaskId) {
        try {
          await updateTask(savedTaskId, {
            content_result: { ...contentResult.result, storyboard: effectiveStoryboard } as unknown as Record<string, unknown>,
            video_result: newVideoResult as unknown as Record<string, unknown>,
            merged_download_url: data.merged_download_url ?? undefined,
            character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
          });
        } catch (_) {}
      } else {
        try {
          const { id } = await createTask({
            pipeline: "script_drama",
            input,
            content_result: { ...contentResult.result, storyboard: effectiveStoryboard } as unknown as Record<string, unknown>,
            video_result: newVideoResult as unknown as Record<string, unknown>,
            merged_download_url: data.merged_download_url ?? undefined,
            character_references: buildCharacterReferencesSnapshot() as Record<string, unknown>,
          });
          setSavedTaskId(id);
          setSearchParams((prev) => {
            const p = new URLSearchParams(prev);
            p.set("taskId", id);
            return p;
          });
        } catch (_) {}
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "剪辑失败");
    } finally {
      setConcatInProgress(false);
    }
  };

  async function onProtagonistFileChange(id: string, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !file.type.startsWith("image/")) return;
    const dataUrl = await readFileAsDataUrl(file);
    setProtagonists((prev) => prev.map((p) => (p.id === id ? { ...p, dataUrl } : p)));
  }

  function addProtagonist() {
    setProtagonists((prev) => [...prev, { id: crypto.randomUUID(), name: "", dataUrl: null }]);
  }

  function removeProtagonist(id: string) {
    setProtagonists((prev) => prev.filter((p) => p.id !== id));
  }

  async function onSupportingFileChange(id: string, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !file.type.startsWith("image/")) return;
    const dataUrl = await readFileAsDataUrl(file);
    setSupportingActors((prev) => prev.map((a) => (a.id === id ? { ...a, dataUrl } : a)));
  }

  function addSupportingActor() {
    setSupportingActors((prev) => [...prev, { id: crypto.randomUUID(), name: "", dataUrl: null }]);
  }

  function removeSupportingActor(id: string) {
    setSupportingActors((prev) => prev.filter((a) => a.id !== id));
  }

  const stepLabel = (step: number, status: StepStatus) => {
    const t = step === 1 ? "生成分镜" : "生成视频";
    if (status === "done") return `步骤 ${step}/2：${t} ✓`;
    if (status === "running") return `步骤 ${step}/2：${t}`;
    if (status === "skip") return `步骤 ${step}/2：${t} —`;
    return `步骤 ${step}/2：${t}`;
  };

  const urls = segmentUrls.length > 0 ? segmentUrls : (videoResult?.download_urls ?? []);
  const hasSegments = urls.length > 0 && effectiveStoryboard.length > 0 && urls.length === effectiveStoryboard.length;
  const { project: timelineProject, mediaBin: segmentBinItems } = hasSegments
    ? buildTimelineFromSegments(urls, effectiveStoryboard, {
        order: timelineOrder && timelineOrder.length === urls.length ? timelineOrder : undefined,
        bgmUrl: videoResult?.bgm_download_url,
      })
    : { project: { videoTrack: { id: "v1", kind: "video" as const, name: "视频轨", clips: [] }, audioTracks: [] }, mediaBin: [] as MediaBinItem[] };
  /** 视频轨 = 生成镜头 + 用户拖入的上传素材，按 startAtSec 排序 */
  const mergedVideoClips = [...timelineProject.videoTrack.clips, ...uploadedVideoClips].sort((a, b) => a.startAtSec - b.startAtSec);
  const totalTimelineSec = Math.max(
    1,
    mergedVideoClips.reduce((max, c) => Math.max(max, c.startAtSec + c.durationSec), 0)
  );
  /** 音轨 = 生成 BGM/配音 + 用户添加的音乐 */
  const mergedAudioTracks = [
    ...timelineProject.audioTracks,
    ...(userMusicTrack
      ? [
          {
            id: "audio-user-music",
            kind: "audio" as const,
            name: userMusicTrack.name || "自定义音乐",
            clips: [
              {
                id: "user-music-1",
                label: userMusicTrack.name || "音乐",
                url: userMusicTrack.url,
                durationSec: Math.max(userMusicTrack.durationSec, totalTimelineSec),
                startAtSec: 0,
              },
            ],
            volume: 0.6,
            muted: false,
          },
        ]
      : []),
  ];
  const mergedProject: typeof timelineProject = {
    ...timelineProject,
    videoTrack: { ...timelineProject.videoTrack, clips: mergedVideoClips },
    audioTracks: mergedAudioTracks,
  };
  /** 素材区仅展示生成后的镜头（视频自动加轨，不再展示上传素材入口） */
  const mediaBinItems: MediaBinItem[] = segmentBinItems.map((s) => ({ ...s, id: s.id ?? `seg-${s.segmentIndex}` }));

  return (
    <div className="flex flex-col flex-1 min-h-0 w-full">
      {error && (
        <div className="flex-shrink-0 px-4 py-2 border-b border-red-500/40 bg-red-950/30 text-red-300 text-sm">{error}</div>
      )}
      <div className="flex flex-1 min-h-0">
        {/* 中间：创作区 + 素材区 + 播放器（左右 6:4） */}
        <section className="flex-[6] min-w-0 flex flex-col bg-black/40 border-r border-slate-600/50 min-h-0">
          {/* 创作区：剧本 + 角色 + BGM + 添加音乐 + 开始生成 */}
          <div className="flex-shrink-0 border-b border-slate-600/50 p-3 space-y-3 bg-slate-800/40">
            <div className="flex gap-3 items-start">
              <div className="flex-1 min-w-0">
                <label className="text-xs text-slate-500 block mb-1">剧本/对白</label>
                <textarea
                  ref={scriptInputRef}
                  className="w-full min-h-[60px] max-h-24 px-3 py-2 rounded-lg text-sm input-tech input-tech-violet text-slate-100 placeholder-slate-500 resize-y"
                  placeholder="粘贴剧本或对白…"
                  value={input}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setInput(e.target.value)}
                  disabled={running}
                />
              </div>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={running || !input.trim()}
                className="flex-shrink-0 self-end px-4 py-2.5 rounded-xl bg-violet-600 text-white text-sm font-medium disabled:opacity-50"
              >
                {running ? "运行中…" : "开始生成"}
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-slate-500">角色：</span>
              {protagonists.map((p) => (
                <span key={p.id} className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-slate-700/80">
                  <input type="text" placeholder="名" value={p.name} onChange={(e) => setProtagonists((prev) => prev.map((x) => (x.id === p.id ? { ...x, name: e.target.value } : x)))} className="w-14 bg-transparent text-slate-200 text-xs" disabled={running} />
                  <label className="cursor-pointer text-violet-400 text-xs">{p.dataUrl ? "✓ 已传" : "上传参考图"}<input type="file" accept="image/*" className="sr-only" disabled={running} onChange={(e) => onProtagonistFileChange(p.id, e)} /></label>
                  {protagonists.length > 1 && <button type="button" onClick={() => removeProtagonist(p.id)} className="text-slate-500 hover:text-red-400" disabled={running}>×</button>}
                </span>
              ))}
              <button type="button" onClick={addProtagonist} className="text-slate-500 hover:text-violet-400 text-xs" disabled={running}>+主角</button>
              {supportingActors.map((a) => (
                <span key={a.id} className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-slate-700/80">
                  <input type="text" placeholder="名" value={a.name} onChange={(e) => setSupportingActors((prev) => prev.map((x) => (x.id === a.id ? { ...x, name: e.target.value } : x)))} className="w-14 bg-transparent text-slate-200 text-xs" disabled={running} />
                  <label className="cursor-pointer text-violet-400 text-xs">{a.dataUrl ? "✓ 已传" : "上传参考图"}<input type="file" accept="image/*" className="sr-only" disabled={running} onChange={(e) => onSupportingFileChange(a.id, e)} /></label>
                  <button type="button" onClick={() => removeSupportingActor(a.id)} className="text-slate-500 hover:text-red-400" disabled={running}>×</button>
                </span>
              ))}
              <button type="button" onClick={addSupportingActor} className="text-slate-500 hover:text-violet-400 text-xs" disabled={running}>+配角</button>
              <span className="w-px h-4 bg-slate-600" />
              <label className="flex items-center gap-1.5 cursor-pointer text-slate-400"><input type="checkbox" checked={withBgm} onChange={(e) => setWithBgm(e.target.checked)} disabled={running} className="rounded text-violet-500" /><span>BGM</span></label>
              <label className="flex items-center gap-1.5 cursor-pointer text-slate-400">
                <input type="file" accept="audio/*" className="sr-only" disabled={running} onChange={handleAddMusic} />
                <span className="text-violet-400/90">添加音乐</span>
              </label>
              {userMusicTrack && (
                <span className="text-slate-500 text-[10px] truncate max-w-[80px]" title={userMusicTrack.name}>
                  ✓ {userMusicTrack.name}
                </span>
              )}
            </div>
          </div>
          {/* 素材区：有素材时显示列表，可拖到下方时间线 */}
          {mediaBinItems.length > 0 && (
            <div className="flex-shrink-0 border-b border-slate-600/50 max-h-28 overflow-hidden flex flex-col bg-slate-800/30">
              <p className="px-3 py-1.5 text-[10px] font-medium text-slate-400">素材（可拖到下方时间轨道）</p>
              <div className="flex-1 min-h-0 overflow-auto px-2 pb-2">
                <MediaBin items={mediaBinItems} disabled={running} />
              </div>
            </div>
          )}
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-slate-600/50 text-xs text-slate-500">
            <span>播放器-时间线01</span>
          </div>
          <div className="flex-1 min-h-0 flex items-center justify-center text-slate-500 text-sm">
            {videoResult?.merged_download_url ? (
              <video src={videoResult.merged_download_url} controls className="max-h-full max-w-full" />
            ) : (
              <span>00:00:00:00 / 00:00:00:00</span>
            )}
          </div>
        </section>

        {/* 右侧：可编辑草稿参数 + 进度 + 分镜（6:4 中占 40%） */}
        <aside className="flex-[4] min-w-0 flex-shrink-0 border-l border-slate-600/50 bg-slate-800/30 flex flex-col overflow-hidden min-h-0">
          <div className="p-2 border-b border-slate-600/50 flex-shrink-0 space-y-2">
            <div className="text-[10px] space-y-1.5">
              <div className="flex items-center gap-2">
                <label className="text-slate-500 w-16 flex-shrink-0">草稿名称</label>
                <input type="text" value={draftName} onChange={(e) => setDraftName(e.target.value)} className="flex-1 min-w-0 px-2 py-1 rounded bg-slate-700/80 text-slate-200 text-xs border border-slate-600/50" placeholder="未命名" disabled={running} />
              </div>
            </div>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden p-2 space-y-3">
      {running && (
        <section className="rounded border border-violet-500/40 bg-violet-950/20 overflow-hidden flex-shrink-0">
          <div className="p-2 space-y-1.5">
            <div className="flex items-center gap-1.5 text-violet-300 text-xs font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
              {NAME} 生成中
            </div>
            <ul className="text-[10px] space-y-1 text-slate-300">
              <li className="flex items-center gap-2">
                {step1Status === "done" ? (
                  <span className="text-emerald-400">✓</span>
                ) : step1Status === "running" ? (
                  <span className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
                ) : (
                  <span className="w-2 h-2 rounded-full bg-slate-500" />
                )}
                <span className={step1Status === "running" ? "font-medium text-violet-300" : ""}>
                  步骤 1/2：解析剧本并生成分镜
                  {step1Status === "done" && "（已完成）"}
                  {step1Status === "running" && "（进行中…）"}
                  {step1Status === "pending" && "（待执行）"}
                </span>
              </li>
              <li className="flex items-center gap-2">
                {step2Status === "done" ? (
                  <span className="text-emerald-400">✓</span>
                ) : step2Status === "running" ? (
                  <span className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
                ) : (
                  <span className="w-2 h-2 rounded-full bg-slate-500" />
                )}
                <span className={step2Status === "running" ? "font-medium text-violet-300" : ""}>
                  步骤 2/2：生成视频
                  {step2Status === "done" && "（已完成）"}
                  {step2Status === "running" && "（进行中…）"}
                  {step2Status === "pending" && "（待执行）"}
                </span>
              </li>
            </ul>
            {step2Status === "running" && (
              <p className="text-[10px] text-slate-400">
                {effectiveStoryboard.length > 0 ? `共 ${effectiveStoryboard.length} 镜，生成中…` : "生成视频中…"}
              </p>
            )}
            <div className="h-1 rounded-full bg-slate-700/80 overflow-hidden">
              <div className="running-bar violet h-full rounded-full transition-all duration-300" style={{ width: step1Status === "running" ? "40%" : step2Status === "running" ? "85%" : "0%" }} />
            </div>
          </div>
        </section>
      )}

      {(contentResult || step1Status !== "pending") && !running && (
        <section className="rounded border border-slate-600/50 p-2 space-y-1.5 flex-shrink-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] text-slate-500">
              {step1Status === "done" && <span className="text-emerald-400">✓</span>} {stepLabel(1, step1Status)}
              {" · "}
              {step2Status === "done" && <span className="text-emerald-400">✓</span>} {stepLabel(2, step2Status)}
            </span>
            {contentResult && isScriptContent(contentResult) && (
              <button type="button" onClick={handleSave} disabled={saving} className="px-2 py-1 rounded text-[10px] font-medium bg-slate-600 text-slate-200 hover:bg-slate-500 disabled:opacity-50">
                {saving ? "保存中…" : savedTaskId ? "更新" : "保存"}
              </button>
            )}
          </div>
        </section>
      )}

      {contentResult && isScriptContent(contentResult) && (
        <div className="flex flex-col gap-3">
          {contentResult.result.message && (
            <div className="p-2 rounded border border-amber-500/40 bg-amber-950/20 text-amber-200 text-xs">
              {contentResult.result.message}
            </div>
          )}
          <details className="rounded border border-slate-600/50 bg-slate-800/50 overflow-hidden">
            <summary className="px-2 py-1.5 text-xs font-medium text-slate-400 cursor-pointer list-none">分镜表（序号/景别/画面/对白/时长）</summary>
          <div className="overflow-x-auto max-h-40 overflow-y-auto border-t border-slate-600/50">
            <table className="w-full min-w-[360px] text-[10px] table-fixed">
              <colgroup>
                <col style={{ width: "6%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "28%" }} />
                <col style={{ width: "26%" }} />
                <col style={{ width: "8%" }} />
                <col style={{ width: "12%" }} />
                {characterNameOptions.length > 0 && <col style={{ width: "10%" }} />}
              </colgroup>
              <thead>
                <tr className="border-b border-slate-600/50">
                  <th className="text-left py-1 px-1 font-medium text-slate-400">序</th>
                  <th className="text-left py-1 px-1 font-medium text-slate-400">景别</th>
                  <th className="text-left py-1 px-1 font-medium text-slate-400">画面描述</th>
                  <th className="text-left py-1 px-1 font-medium text-slate-400">对白/旁白</th>
                  <th className="text-left py-1 px-1 font-medium text-slate-400">秒</th>
                  <th className="text-left py-1 px-1 font-medium text-slate-400">生成</th>
                  {characterNameOptions.length > 0 && (
                    <th className="text-left py-1 px-1 font-medium text-slate-400">
                      <span className="block">本镜人物</span>
                      <label className="flex items-center gap-1 mt-0.5 cursor-pointer" title="根据镜头描述与对白自动识别">
                        <input type="checkbox" checked={autoSelectCharacters} onChange={(e) => setAutoSelectCharacters(e.target.checked)} disabled={running} className="rounded border-slate-500 text-violet-500" />
                        <span className="text-[10px]">自动</span>
                      </label>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {effectiveStoryboard.map((s: StoryboardItem) => (
                  <tr key={s.index} className="border-b border-slate-700/50">
                    <td className="py-1 px-1 text-slate-500">{s.index}</td>
                    <td className="py-1 px-1 text-violet-300/90">{s.shot_type || "—"}</td>
                    <td className="py-1 px-1 text-slate-200 truncate max-w-[80px]" title={s.shot_desc}>{s.shot_desc}</td>
                    <td className="py-1 px-1 text-slate-400 truncate max-w-[70px]" title={s.copy ?? s.copy_text ?? ""}>{s.copy ?? s.copy_text ?? "—"}</td>
                    <td className="py-1 px-1 text-slate-500">{s.duration_sec ?? "—"}</td>
                    <td className="py-1 px-1 text-cyan-300/90">{s.generation_method === "i2v" ? "图生" : s.generation_method === "fl2v" ? "首尾" : "文生"}</td>
                    {characterNameOptions.length > 0 && (
                      <td className="py-1 px-1">
                        <div className="flex flex-wrap gap-0.5">
                          {characterNameOptions.map((n) => {
                            const selected = getSelectedCharactersForShot(s).includes(n);
                            return (
                              <label key={n} className="flex items-center gap-0.5 cursor-pointer">
                                <input type="checkbox" checked={selected} onChange={() => toggleShotCharacter(s.index, n)} disabled={running} className="rounded border-slate-500 text-violet-500" />
                                <span className="truncate max-w-[28px]">{n}</span>
                              </label>
                            );
                          })}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </details>
          {contentResult.result.prompts.length > 0 && (
            <details className="rounded border border-slate-600/50 overflow-hidden">
              <summary className="px-2 py-1.5 text-xs font-medium text-violet-400/90 cursor-pointer list-none">文生视频 Prompt 列表</summary>
              <div className="p-2 border-t border-slate-600/50 max-h-32 overflow-y-auto">
                <ol className="list-decimal list-inside space-y-1 text-[10px] text-slate-300">
                  {contentResult.result.prompts.map((p: string, i: number) => (
                    <li key={i} className="truncate" title={p}>{p}</li>
                  ))}
                </ol>
              </div>
            </details>
          )}

          {/* 每个镜头实际发送给视频模型的提示词 */}
          {effectiveStoryboard.length > 0 && (
            <details className="rounded border border-amber-500/30 bg-amber-950/10 overflow-hidden">
              <summary className="px-2 py-1.5 text-xs font-medium text-amber-400/90 cursor-pointer list-none">每镜提示词（可编辑）</summary>
              <div className="p-2 border-t border-slate-600/50 max-h-40 overflow-y-auto space-y-2">
              <ul className="space-y-3">
                {effectiveStoryboard.map((s: StoryboardItem, positionIndex: number) => {
                  const copy = (s.copy ?? (s as { copy_text?: string }).copy_text ?? "").trim();
                  let base = (s.t2v_prompt ?? "").trim();
                  if (!base) {
                    const maxCopy = 220;
                    base = `${s.shot_desc}，${copy.slice(0, maxCopy)}`.trim() || s.shot_desc;
                  }
                  const defaultFullPrompt = copy
                    ? `${base}。本镜对白/情节：${copy.slice(0, 220)}`.slice(0, 500)
                    : base.slice(0, 500);
                  const value = storyboardPromptEdits[s.index] ?? defaultFullPrompt;
                  return (
                    <li key={s.index} className="border border-slate-600/50 rounded-lg p-3 bg-slate-800/50">
                      <div className="flex items-center justify-between gap-2 mb-1.5">
                        <p className="text-xs font-medium text-violet-300">镜头 {s.index} · {s.shot_type || "—"} · {s.generation_method === "i2v" ? "图生视频" : s.generation_method === "fl2v" ? "首尾帧" : "文生视频"}</p>
                        {effectiveStoryboard.length > 1 && (
                          <button
                            type="button"
                            onClick={() => handleDeleteShot(positionIndex)}
                            disabled={running}
                            className="text-xs px-2 py-1 rounded border border-red-500/50 text-red-400 hover:bg-red-500/10 disabled:opacity-50"
                          >
                            删除镜头
                          </button>
                        )}
                      </div>
                      <textarea
                        className="w-full min-h-[80px] px-3 py-2 rounded-lg text-sm text-slate-200 bg-slate-900/80 border border-slate-600 focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/30 outline-none resize-y placeholder-slate-500"
                        placeholder={defaultFullPrompt}
                        value={value}
                        onChange={(e) => setStoryboardPromptEdits((prev) => ({ ...prev, [s.index]: e.target.value }))}
                        disabled={running}
                      />
                      {/* 生成开始后，在本镜下方直接显示该镜的可灵任务状态 */}
                      {(videoResult?.task_ids?.length ?? 0) > 0 && klingStatusItems && klingStatusItems[positionIndex] && (
                        <div
                          className={`mt-2 px-2.5 py-1.5 rounded-lg text-xs flex items-center gap-2 ${
                            klingStatusItems[positionIndex].status === "succeed"
                              ? "bg-green-500/15 text-green-400 border border-green-500/30"
                              : klingStatusItems[positionIndex].status === "failed"
                                ? "bg-red-500/15 text-red-400 border border-red-500/30"
                                : "bg-blue-500/15 text-blue-400 border border-blue-500/30 animate-pulse"
                          }`}
                        >
                          <span className="font-medium">
                            {klingStatusItems[positionIndex].status === "succeed"
                              ? "✓ 已生成"
                              : klingStatusItems[positionIndex].status === "failed"
                                ? "✗ 失败"
                                : "… 生成中"}
                          </span>
                          {klingStatusItems[positionIndex].status === "failed" && klingStatusItems[positionIndex].task_status_msg && (
                            <span className="opacity-90 truncate" title={klingStatusItems[positionIndex].task_status_msg}>
                              {klingStatusItems[positionIndex].task_status_msg.slice(0, 60)}
                              {(klingStatusItems[positionIndex].task_status_msg?.length ?? 0) > 60 ? "…" : ""}
                            </span>
                          )}
                          {klingStatusItems[positionIndex].status === "succeed" && klingStatusItems[positionIndex].url && (
                            <a
                              href={klingStatusItems[positionIndex].url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-violet-400 hover:underline"
                            >
                              预览
                            </a>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          </details>
          )}
          {step1Status === "done" && step2Status === "pending" && effectiveStoryboard.length > 0 && (
            <div className="flex flex-col gap-2">
              <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showTaskStatusFirst}
                  onChange={(e) => setShowTaskStatusFirst(e.target.checked)}
                  className="rounded border-slate-500 bg-slate-800 text-violet-500"
                />
                先展示每镜状态（绿/蓝/红）再剪辑（推荐，减少下载超时）
              </label>
              <div className="flex justify-end">
                <button type="button" onClick={handleGenerateVideo} className="px-4 py-2 rounded-xl bg-violet-600 text-white text-sm font-medium btn-primary-violet">
                  下一步：生成视频
                </button>
              </div>
            </div>
          )}

          {/* 镜头预览与调整（可折叠） */}
          {effectiveStoryboard.length > 0 && (segmentUrls.length > 0 || (videoResult?.download_urls?.length ?? 0) > 0) && (
            <details className="rounded border border-violet-500/30 bg-slate-800/50 overflow-hidden">
              <summary className="px-2 py-1.5 text-xs font-medium text-violet-400 cursor-pointer list-none">镜头预览与调整 · 确认并剪辑</summary>
              <div className="p-2 border-t border-slate-600/50 max-h-48 overflow-y-auto space-y-3">
              <ul className="space-y-3">
                {effectiveStoryboard.map((s: StoryboardItem, idx: number) => {
                  const urls = segmentUrls.length > 0 ? segmentUrls : (videoResult?.download_urls ?? []);
                  const url = urls[idx];
                  const status = regeneratingShotIndex === idx ? "generating" : url ? "done" : "failed";
                  const copy = (s.copy ?? (s as { copy_text?: string }).copy_text ?? "").trim();
                  const defaultPrompt = (s.t2v_prompt ?? "").trim() || `${s.shot_desc}${copy ? `。本镜对白/情节：${copy.slice(0, 220)}` : ""}`.slice(0, 500);
                  const promptValue = storyboardPromptEdits[s.index] ?? defaultPrompt;
                  return (
                    <li key={s.index} className="border border-slate-600/50 rounded-lg p-3 bg-slate-900/50 flex flex-col gap-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-violet-300">镜头 {s.index} · {s.shot_type || "—"}</span>
                          <span className={`text-xs px-2 py-0.5 rounded ${status === "done" ? "bg-emerald-500/20 text-emerald-400" : status === "generating" ? "bg-violet-500/20 text-violet-400" : "bg-amber-500/20 text-amber-400"}`}>
                            {status === "done" ? "已生成" : status === "generating" ? "生成中…" : "未生成"}
                          </span>
                          {characterNameOptions.length > 0 && (
                          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                            <span>本镜人物</span>
                            {characterNameOptions.map((n) => {
                              const selected = getSelectedCharactersForShot(s).includes(n);
                              return (
                                <label key={n} className="flex items-center gap-1 cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={selected}
                                    onChange={() => toggleShotCharacter(s.index, n)}
                                    disabled={running}
                                    className="rounded border-slate-500 text-violet-500 focus:ring-violet-500/50"
                                  />
                                  <span className="text-slate-400">{n}</span>
                                </label>
                              );
                            })}
                            <span className="text-slate-600">多选=同镜</span>
                          </div>
                        )}
                        </div>
                        {effectiveStoryboard.length > 1 && (
                          <button
                            type="button"
                            onClick={() => handleDeleteShot(idx)}
                            disabled={running}
                            className="text-xs px-2 py-1 rounded border border-red-500/50 text-red-400 hover:bg-red-500/10 disabled:opacity-50"
                          >
                            删除镜头
                          </button>
                        )}
                      </div>
                      {url && (
                        <div className="rounded-lg overflow-hidden bg-black/40 border border-slate-600/50">
                          <video src={url} controls className="w-full max-h-[240px]" preload="metadata" />
                        </div>
                      )}
                      <div>
                        <label className="text-xs text-slate-500 block mb-1">提示词（可修改后重新生成）</label>
                        <textarea
                          className="w-full min-h-[72px] px-3 py-2 rounded-lg text-sm text-slate-200 bg-slate-800 border border-slate-600 focus:border-violet-500/50 outline-none resize-y"
                          value={promptValue}
                          onChange={(e) => setStoryboardPromptEdits((prev) => ({ ...prev, [s.index]: e.target.value }))}
                          disabled={running}
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRegenerateShot(idx)}
                        disabled={running || regeneratingShotIndex !== null}
                        className="self-start px-3 py-1.5 rounded-lg text-sm font-medium bg-violet-600/80 text-white hover:bg-violet-600 disabled:opacity-50"
                      >
                        {regeneratingShotIndex === idx ? "生成中…" : "重新生成"}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleConcatConfirm}
                  disabled={running || (() => {
                    const u = segmentUrls.length > 0 ? segmentUrls : (videoResult?.download_urls ?? []);
                    return u.length !== effectiveStoryboard.length || u.some((x) => !x?.trim());
                  })()}
                  className="px-3 py-1.5 rounded-lg bg-violet-600 text-white text-xs font-medium disabled:opacity-50"
                >
                  {concatInProgress ? "剪辑中…" : "确认并剪辑"}
                </button>
              </div>
              </div>
            </details>
          )}
        </div>
      )}

      {videoResult && (
        <div className="p-2 rounded border border-violet-500/30 bg-slate-800/50 space-y-1.5">
          <p className="text-[10px] text-slate-400">模式：{videoResult.video_mode}</p>
          {videoResult.video_mode_reason && <p className="text-[10px] text-violet-300/80 truncate" title={videoResult.video_mode_reason}>{videoResult.video_mode_reason}</p>}
          {videoResult.error && <p className="text-[10px] text-amber-400">{videoResult.error}</p>}
          {videoResult.task_ids.length > 0 && !videoResult.merged_download_url && klingStatusItems && klingStatusItems.length > 0 && (
            <div className="mb-2 p-2 rounded border border-slate-600/50 bg-slate-800/30 space-y-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <p className="text-[10px] font-medium text-slate-300">每镜进度</p>
                <span className="text-[10px] text-slate-400">
                  已完成 {klingStatusItems.filter((i) => i.status === "succeed").length}/{klingStatusItems.length} 镜
                  {klingStatusItems.some((i) => i.status === "failed") &&
                    ` · ${klingStatusItems.filter((i) => i.status === "failed").length} 镜失败`}
                  <span className="text-slate-500 ml-1">· 状态每 5 秒刷新</span>
                </span>
              </div>
              <div className="w-full h-2 rounded-full bg-slate-700 overflow-hidden">
                <div
                  className="h-full bg-green-500/80 transition-all duration-300"
                  style={{
                    width: `${(klingStatusItems.filter((i) => i.status === "succeed").length / klingStatusItems.length) * 100}%`,
                  }}
                />
              </div>
              <ul className="space-y-2">
                {klingStatusItems.map((item, idx) => {
                  const shot = effectiveStoryboard[idx];
                  const desc = shot
                    ? (shot.shot_desc ?? "").trim() || (shot.copy ?? (shot as { copy_text?: string }).copy_text ?? "").trim().slice(0, 40)
                    : "";
                  const isSuccess = item.status === "succeed";
                  const isFailed = item.status === "failed";
                  const isProcessing = item.status === "processing";
                  return (
                    <li
                      key={item.task_id}
                      className={`flex items-start gap-3 p-2.5 rounded-lg border ${
                        isSuccess
                          ? "border-green-500/40 bg-green-950/20"
                          : isFailed
                            ? "border-red-500/40 bg-red-950/20"
                            : "border-blue-500/30 bg-slate-800/50"
                      }`}
                    >
                      <span
                        className={`flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium ${
                          isSuccess
                            ? "bg-green-500 text-white"
                            : isFailed
                              ? "bg-red-500 text-white"
                              : "bg-blue-500 text-white animate-pulse"
                        }`}
                      >
                        {isSuccess ? "✓" : isFailed ? "✗" : "…"}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-200">
                          镜头 {idx + 1}
                          {desc && <span className="text-slate-500 font-normal"> · {desc}{desc.length >= 40 ? "…" : ""}</span>}
                        </p>
                        <p className="text-xs mt-0.5">
                          {isSuccess && (
                            <span className="text-green-400">
                              已生成
                              {item.url && (
                                <a
                                  href={item.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="ml-1 text-violet-400 hover:underline"
                                >
                                  预览
                                </a>
                              )}
                            </span>
                          )}
                          {isProcessing && <span className="text-blue-400 animate-pulse">生成中…</span>}
                          {isFailed && (
                            <span className="text-red-400" title={item.task_status_msg ?? ""}>
                              {item.task_status_msg ? `失败：${item.task_status_msg.slice(0, 80)}${item.task_status_msg.length > 80 ? "…" : ""}` : "失败"}
                            </span>
                          )}
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ul>
              {klingStatusItems.every((i) => i.status === "succeed") && (
                <button
                  type="button"
                  onClick={handleConcatAfterKling}
                  disabled={concatAfterKlingInProgress}
                  className="px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium disabled:opacity-50"
                >
                  {concatAfterKlingInProgress ? "剪辑中…" : "全部完成，开始剪辑"}
                </button>
              )}
            </div>
          )}
          {videoResult.merged_download_url && (
            <div className="mb-2 p-2 rounded border border-violet-500/40 bg-violet-950/20">
              <a href={videoResult.merged_download_url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-violet-400 hover:underline">下载成片</a>
            </div>
          )}
          {videoResult.bgm_download_url && (
            <div className="mb-2 p-2 rounded border border-slate-600/50 bg-slate-800/30">
              <a href={videoResult.bgm_download_url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-violet-400 hover:underline">下载 BGM</a>
            </div>
          )}
          {videoResult.download_urls.length > 0 && (
            <p className="text-[10px] text-slate-500">
              {videoResult.download_urls.map((url: string, idx: number) => (
                <a key={idx} href={url} target="_blank" rel="noopener noreferrer" className="text-violet-400 hover:underline mr-2">视频{idx + 1}</a>
              ))}
            </p>
          )}
        </div>
      )}
          </div>
        </aside>
      </div>

      {/* 底部：多轨时间线（下移，与播放器留出间距不挤压上面） */}
      <div className="mt-6 min-h-[220px] h-[220px] flex-shrink-0 flex flex-col border-t border-slate-600/50 bg-slate-800/30 pt-2">
        <TimelineEditor
            project={mergedProject}
            mediaBinItems={mediaBinItems}
            totalDurationSec={Math.ceil(totalTimelineSec)}
            layout="tracksOnly"
            disabled={running}
            onVideoOrderChange={(ordered) => setTimelineOrder(ordered)}
            onUploadedClipDrop={(_trackId, clip, startAtSec) => {
              setUploadedVideoClips((prev) => [
                ...prev,
                {
                  id: clip.id,
                  segmentIndex: -1,
                  label: clip.label,
                  url: clip.url,
                  durationSec: clip.durationSec,
                  startAtSec,
                },
              ]);
            }}
            onRemoveClip={(_trackId, clipId) => {
              setUploadedVideoClips((prev) => prev.filter((c) => c.id !== clipId));
            }}
          />
      </div>
    </div>
  );
}
