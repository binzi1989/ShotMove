/** 单条轨道行：可放置视频/音频片段，支持拖拽重排与音量（音频轨） */
import type { TimelineTrack, TimelineClip } from "../../types/timeline";
import { DRAG_TYPE } from "./MediaBin";

const CLIP_DRAG_TYPE = "application/x-drama-timeline-clip";

interface TrackRowProps {
  track: TimelineTrack;
  totalDurationSec: number;
  pixelsPerSec: number;
  disabled?: boolean;
  onDropFromBin?: (trackId: string, data: { id?: string; segmentIndex: number; url: string; durationSec: number; label: string }, startAtSec: number) => void;
  onDropClipOnTrack?: (trackId: string, clip: TimelineClip, startAtSec: number) => void;
  onVolumeChange?: (trackId: string, volume: number) => void;
  onMuteToggle?: (trackId: string, muted: boolean) => void;
  onRemoveClip?: (trackId: string, clipId: string) => void;
}

export default function TrackRow({
  track,
  totalDurationSec,
  pixelsPerSec,
  disabled,
  onDropFromBin,
  onDropClipOnTrack,
  onVolumeChange,
  onMuteToggle,
  onRemoveClip,
}: TrackRowProps) {
  const isAudio = track.kind === "audio";

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    if (disabled) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const startAtSec = Math.max(0, Math.floor(x / pixelsPerSec));

    const clipJson = e.dataTransfer.getData(CLIP_DRAG_TYPE);
    if (clipJson) {
      try {
        const clip = JSON.parse(clipJson) as TimelineClip;
        if (clip.segmentIndex != null && onDropClipOnTrack) {
          onDropClipOnTrack(track.id, clip, startAtSec);
        }
      } catch (_) {}
      return;
    }

    const binJson = e.dataTransfer.getData(DRAG_TYPE);
    if (binJson) {
      try {
        const data = JSON.parse(binJson);
        if (data.segmentIndex !== undefined && onDropFromBin) {
          onDropFromBin(track.id, data, startAtSec);
        }
      } catch (_) {}
    }
  }

  function handleClipDragStart(e: React.DragEvent, clip: TimelineClip) {
    if (disabled) return;
    e.dataTransfer.setData(CLIP_DRAG_TYPE, JSON.stringify({ ...clip }));
    e.dataTransfer.effectAllowed = "move";
  }

  return (
    <div className="flex items-stretch border-b border-slate-700/50 min-h-[52px]">
      {/* 轨道名称 + 音量（音频轨） */}
      <div className="w-28 flex-shrink-0 flex flex-col justify-center gap-1 px-2 py-1.5 border-r border-slate-600/50 bg-slate-800/50">
        <p className="text-xs font-medium text-slate-300 truncate">{track.name}</p>
        {isAudio && (
          <div className="flex items-center gap-1">
            <input
              type="range"
              min={0}
              max={100}
              value={((track.volume ?? 1) * 100) | 0}
              onChange={(e) => onVolumeChange?.(track.id, Number(e.target.value) / 100)}
              disabled={disabled}
              className="w-14 h-1 rounded accent-violet-500"
            />
            <button
              type="button"
              onClick={() => onMuteToggle?.(track.id, !track.muted)}
              className="text-xs text-slate-500 hover:text-slate-300"
              title={track.muted ? "取消静音" : "静音"}
            >
              {track.muted ? "🔇" : "🔊"}
            </button>
          </div>
        )}
      </div>

      {/* 片段区域 */}
      <div
        className="flex-1 relative min-h-[48px] bg-slate-900/50"
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        style={{ width: totalDurationSec * pixelsPerSec }}
      >
        {track.clips.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-slate-600 text-xs">
            拖拽素材到这里
          </div>
        ) : (
          track.clips.map((clip) => (
            <div
              key={clip.id}
              draggable={!disabled && track.kind === "video"}
              onDragStart={(e) => handleClipDragStart(e, clip)}
              className="absolute top-1 bottom-1 rounded border border-violet-500/50 bg-violet-950/30 flex items-center overflow-hidden group"
              style={{
                left: clip.startAtSec * pixelsPerSec,
                width: clip.durationSec * pixelsPerSec,
                minWidth: 24,
              }}
            >
              <span className="text-[10px] text-violet-300/90 px-1 truncate flex-1">{clip.label}</span>
              {onRemoveClip && track.kind === "video" && (
                <button
                  type="button"
                  onClick={() => onRemoveClip(track.id, clip.id)}
                  className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-400 text-xs px-1"
                >
                  ×
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
