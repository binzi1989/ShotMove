/** 左侧素材库：可拖拽到时间线轨道的片段列表 */
import type { MediaBinItem } from "../../types/timeline";

interface MediaBinProps {
  items: MediaBinItem[];
  disabled?: boolean;
}

const DRAG_TYPE = "application/x-drama-segment";

export default function MediaBin({ items, disabled }: MediaBinProps) {
  function handleDragStart(e: React.DragEvent, item: MediaBinItem) {
    if (disabled) return;
    const id = item.id ?? `seg-${item.segmentIndex}`;
    e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ id, segmentIndex: item.segmentIndex, url: item.url, durationSec: item.durationSec, label: item.label }));
    e.dataTransfer.effectAllowed = "copy";
  }

  if (items.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-slate-500 text-sm p-4 border border-slate-600/50 rounded-xl bg-slate-800/30">
        <p className="mb-2">暂无素材</p>
        <p className="text-xs">上传素材或生成视频后，镜头将出现在此处，可拖拽到时间线</p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col rounded-xl border border-slate-600/50 bg-slate-800/30 overflow-hidden">
      <div className="px-3 py-2 border-b border-slate-600/50 text-xs font-medium text-slate-400 flex items-center gap-2">
        <span>素材</span>
        <span className="text-slate-600">共 {items.length} 个</span>
      </div>
      <ul className="flex-1 overflow-y-auto p-2 space-y-2">
        {items.map((item) => (
          <li
            key={item.id ?? `seg-${item.segmentIndex}`}
            draggable={!disabled}
            onDragStart={(e) => handleDragStart(e, item)}
            className="flex items-center gap-2 p-2 rounded-lg border border-slate-600/50 bg-slate-800/50 cursor-grab active:cursor-grabbing hover:border-violet-500/40 hover:bg-slate-700/50 transition-colors"
          >
            {item.thumb ? (
              <img src={item.thumb} alt="" className="w-12 h-8 object-cover rounded flex-shrink-0" />
            ) : (
              <div className="w-12 h-8 rounded bg-slate-700 flex items-center justify-center text-slate-500 text-xs flex-shrink-0">
                {item.segmentIndex >= 0 ? item.segmentIndex + 1 : "↑"}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-200 truncate">{item.label}</p>
              <p className="text-xs text-slate-500">{item.durationSec}s</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="px-3 py-1.5 text-[10px] text-slate-600 border-t border-slate-600/50">
        拖拽到下方时间线可调整顺序
      </p>
    </div>
  );
}

export { DRAG_TYPE };
