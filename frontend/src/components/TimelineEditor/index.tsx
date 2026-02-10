/** 多轨时间线编辑器：素材区 + 预览 + 视频轨/音效轨，自由拖拽管理 */
import { useCallback } from "react";
import type { TimelineProject, TimelineTrack, TimelineClip, MediaBinItem } from "../../types/timeline";
import MediaBin from "./MediaBin";
import TrackRow from "./TrackRow";

interface TimelineEditorProps {
  project: TimelineProject;
  /** 媒体库素材（layout=full 时显示；tracksOnly 时由外部左侧面板提供） */
  mediaBinItems: MediaBinItem[];
  /** 总时长（秒），用于时间轴刻度 */
  totalDurationSec: number;
  /** 每秒占多少像素 */
  pixelsPerSec?: number;
  disabled?: boolean;
  /** 视频轨顺序变化时（仅视频轨 clips 顺序有意义，用于成片顺序） */
  onVideoOrderChange?: (orderedSegmentIndices: number[]) => void;
  /** 从素材区拖入视频轨的上传素材（segmentIndex -1） */
  onUploadedClipDrop?: (trackId: string, clip: { id: string; url: string; durationSec: number; label: string }, startAtSec: number) => void;
  /** 音频轨音量/静音变化（可选持久化） */
  onAudioTrackChange?: (trackId: string, patch: { volume?: number; muted?: boolean }) => void;
  /** 从轨道移除片段（仅视频轨） */
  onRemoveClip?: (trackId: string, clipId: string) => void;
  /** full = 左侧素材+预览+时间线；tracksOnly = 仅时间轴+轨道（用于嵌入四宫格底部） */
  layout?: "full" | "tracksOnly";
}

export default function TimelineEditor({
  project,
  mediaBinItems,
  totalDurationSec,
  pixelsPerSec = 24,
  disabled,
  onVideoOrderChange,
  onUploadedClipDrop,
  onAudioTrackChange,
  onRemoveClip,
  layout = "full",
}: TimelineEditorProps) {
  const { videoTrack, audioTracks } = project;
  const tracksOnly = layout === "tracksOnly";

  const findInsertIndex = useCallback(
    (startAtSec: number) => {
      let cum = 0;
      for (let j = 0; j < videoTrack.clips.length; j++) {
        if (cum + videoTrack.clips[j].durationSec > startAtSec) return j;
        cum += videoTrack.clips[j].durationSec;
      }
      return videoTrack.clips.length;
    },
    [videoTrack.clips]
  );

  const handleDropFromBin = useCallback(
    (trackId: string, data: { id?: string; segmentIndex: number; url: string; durationSec: number; label: string }, startAtSec: number) => {
      if (trackId !== videoTrack.id) return;
      /** 上传素材（segmentIndex -1）拖入轨道：由父组件合并展示 */
      if (data.segmentIndex < 0) {
        if (data.id && onUploadedClipDrop) onUploadedClipDrop(trackId, { id: data.id, url: data.url, durationSec: data.durationSec, label: data.label }, startAtSec);
        return;
      }
      const order = videoTrack.clips.map((c) => c.segmentIndex!);
      const without = order.filter((i) => i !== data.segmentIndex);
      const insertAt = findInsertIndex(startAtSec);
      const newOrder = [...without];
      newOrder.splice(insertAt, 0, data.segmentIndex);
      onVideoOrderChange?.(newOrder);
    },
    [videoTrack.id, videoTrack.clips, findInsertIndex, onVideoOrderChange, onUploadedClipDrop]
  );

  const handleDropClipOnTrack = useCallback(
    (trackId: string, clip: TimelineClip, startAtSec: number) => {
      if (trackId !== videoTrack.id || clip.segmentIndex == null) return;
      const order = videoTrack.clips.map((c) => c.segmentIndex!);
      const without = order.filter((i) => i !== clip.segmentIndex);
      const insertAt = findInsertIndex(startAtSec);
      const newOrder = [...without];
      newOrder.splice(insertAt, 0, clip.segmentIndex);
      onVideoOrderChange?.(newOrder);
    },
    [videoTrack.id, videoTrack.clips, findInsertIndex, onVideoOrderChange]
  );

  const handleRemoveClip = useCallback(
    (trackId: string, clipId: string) => {
      if (trackId !== videoTrack.id) {
        onRemoveClip?.(trackId, clipId);
        return;
      }
      const clip = videoTrack.clips.find((c) => c.id === clipId);
      if (clip?.segmentIndex == null || clip.segmentIndex < 0) {
        onRemoveClip?.(trackId, clipId);
        return;
      }
      const rest = videoTrack.clips.filter((c) => c.id !== clipId);
      const newOrder = rest.map((c) => c.segmentIndex!).filter((i) => i >= 0);
      onVideoOrderChange?.(newOrder);
    },
    [videoTrack.id, videoTrack.clips, onVideoOrderChange, onRemoveClip]
  );

  const handleVolumeChange = useCallback(
    (trackId: string, volume: number) => {
      onAudioTrackChange?.(trackId, { volume });
    },
    [onAudioTrackChange]
  );

  const handleMuteToggle = useCallback(
    (trackId: string, muted: boolean) => {
      onAudioTrackChange?.(trackId, { muted });
    },
    [onAudioTrackChange]
  );

  const trackContent = (
    <>
      {/* 时间轴刻度 */}
      <div className="flex border-b border-slate-600/50 bg-slate-800/50">
        <div className="w-28 flex-shrink-0" />
        <div className="flex-1 flex overflow-x-auto py-1 min-w-0" style={{ width: Math.max(200, totalDurationSec * pixelsPerSec) }}>
          {Array.from({ length: Math.ceil(totalDurationSec / 5) + 1 }, (_, i) => i * 5).map((sec) => (
            <span
              key={sec}
              className="text-[10px] text-slate-500 flex-shrink-0"
              style={{ width: 5 * pixelsPerSec, textAlign: "center" }}
            >
              {sec}s
            </span>
          ))}
        </div>
      </div>
      <TrackRow
        track={videoTrack}
        totalDurationSec={totalDurationSec}
        pixelsPerSec={pixelsPerSec}
        disabled={disabled}
        onDropFromBin={handleDropFromBin}
        onDropClipOnTrack={handleDropClipOnTrack}
        onRemoveClip={handleRemoveClip}
      />
      {audioTracks.map((track) => (
        <TrackRow
          key={track.id}
          track={track}
          totalDurationSec={totalDurationSec}
          pixelsPerSec={pixelsPerSec}
          disabled={disabled}
          onVolumeChange={handleVolumeChange}
          onMuteToggle={handleMuteToggle}
        />
      ))}
    </>
  );

  if (tracksOnly) {
    return (
      <div className="flex flex-col flex-1 min-h-0 border-t border-slate-600/50 bg-slate-800/30">
        <div className="flex-1 min-h-0 overflow-auto flex flex-col">
          {trackContent}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-600/50 bg-slate-800/30 overflow-hidden flex flex-col">
      <div className="grid grid-cols-[200px_1fr] gap-0 flex-1 min-h-0" style={{ minHeight: 280 }}>
        <div className="border-r border-slate-600/50 flex flex-col min-h-[200px]">
          <MediaBin items={mediaBinItems} disabled={disabled} />
        </div>
        <div className="flex flex-col min-h-0">
          <div className="h-32 border-b border-slate-600/50 flex items-center justify-center bg-black/40 text-slate-500 text-sm">
            播放器 · 时间线
          </div>
          {trackContent}
        </div>
      </div>
      <p className="px-3 py-1.5 text-[10px] text-slate-500 border-t border-slate-600/50">
        多轨 · 拖拽素材到视频轨可调整成片顺序；音效轨可调节音量
      </p>
    </div>
  );
}

/** 从分镜 + 片段 URL 列表构建时间线项目与媒体库；order 为视频轨顺序（片段下标），未传则按 0,1,2,... */
export function buildTimelineFromSegments(
  segmentUrls: string[],
  storyboard: Array<{ index: number; shot_type?: string; duration_sec?: number }>,
  options?: { order?: number[]; bgmUrl?: string; voiceoverUrl?: string }
): { project: TimelineProject; mediaBin: MediaBinItem[] } {
  const mediaBin: MediaBinItem[] = segmentUrls.map((url, i) => {
    const shot = storyboard[i];
    const dur = shot?.duration_sec ?? 4;
    return {
      segmentIndex: i,
      label: `镜头 ${(shot?.index ?? i + 1)}${shot?.shot_type ? ` · ${shot.shot_type}` : ""}`,
      url,
      durationSec: dur,
    };
  });

  const order = options?.order ?? segmentUrls.map((_, i) => i);
  let startAt = 0;
  const videoClips: TimelineClip[] = order
    .filter((i) => i >= 0 && i < segmentUrls.length)
    .map((i) => {
      const url = segmentUrls[i];
      const shot = storyboard[i];
      const dur = shot?.duration_sec ?? 4;
      const clip: TimelineClip = {
        id: `v-${i}-${startAt}`,
        segmentIndex: i,
        label: `镜头 ${(shot?.index ?? i + 1)}`,
        url,
        durationSec: dur,
        startAtSec: startAt,
      };
      startAt += dur;
      return clip;
    });

  const totalDurationSec = videoClips.reduce((s, c) => s + c.durationSec, 0);

  const audioTracks: TimelineTrack[] = [];
  if (options?.bgmUrl) {
    audioTracks.push({
      id: "audio-bgm",
      kind: "audio",
      name: "BGM",
      clips: [{ id: "bgm-1", label: "BGM", url: options.bgmUrl, durationSec: totalDurationSec, startAtSec: 0 }],
      volume: 0.6,
      muted: false,
    });
  }
  if (options?.voiceoverUrl) {
    audioTracks.push({
      id: "audio-voice",
      kind: "audio",
      name: "配音",
      clips: [{ id: "vo-1", label: "配音", url: options.voiceoverUrl, durationSec: totalDurationSec, startAtSec: 0 }],
      volume: 1,
      muted: false,
    });
  }

  const project: TimelineProject = {
    videoTrack: {
      id: "video-1",
      kind: "video",
      name: "视频轨",
      clips: videoClips,
    },
    audioTracks,
  };

  return { project, mediaBin };
}
