/** 时间线相关类型：多轨、自由拖拽管理素材与音效 */

export type TrackKind = "video" | "audio";

/** 时间线上的一个片段（视频轨或音效轨上的一个块） */
export interface TimelineClip {
  id: string;
  /** 视频轨：对应分镜/片段下标；音频轨可无 */
  segmentIndex?: number;
  /** 展示用标签，如 "镜头 1"、"BGM" */
  label: string;
  /** 视频/音频 URL */
  url: string;
  /** 时长（秒） */
  durationSec: number;
  /** 在该轨上的起始时间（秒），用于多段 BGM/音效 时对齐 */
  startAtSec: number;
}

/** 单条轨道 */
export interface TimelineTrack {
  id: string;
  kind: TrackKind;
  name: string;
  clips: TimelineClip[];
  /** 仅音频轨：音量 0–1 */
  volume?: number;
  /** 仅音频轨：是否静音 */
  muted?: boolean;
}

/** 时间线项目（一个草稿对应一条时间线） */
export interface TimelineProject {
  /** 视频轨：按顺序的片段，拖拽重排即改变成片顺序 */
  videoTrack: TimelineTrack;
  /** 音效轨：BGM、配音等，可多条 */
  audioTracks: TimelineTrack[];
}

/** 媒体库中的一条素材（来自自动剪辑的分镜片段或用户上传） */
export interface MediaBinItem {
  /** 分镜片段下标；上传素材可为 -1，用 id 区分 */
  segmentIndex: number;
  label: string;
  url: string;
  durationSec: number;
  /** 可选：封面图或首帧 */
  thumb?: string;
  /** 可选：上传素材唯一 id，用于 key */
  id?: string;
}
