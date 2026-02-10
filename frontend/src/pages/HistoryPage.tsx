/** 我的创作 · 历史任务列表，支持打开、删除 */
import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { listTasks, deleteTask, type TaskSummary, type PipelineType } from "../api";

const PIPELINE_LABEL: Record<PipelineType, string> = {
  script_drama: "短剧",
};

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return iso;
  }
}

export default function HistoryPage() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listTasks({ limit: 50, offset: 0 });
      setTasks(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("确定删除这条创作？")) return;
    try {
      await deleteTask(id);
      setTasks((prev) => prev.filter((t) => t.id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  const path = (_t: TaskSummary) => "/drama";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold text-slate-200">我的创作</h1>
        <p className="text-sm text-slate-500">生成内容将自动保存到此，可查看全部任务与完成状态，点击进入查看详情</p>
      </div>

      {error && (
        <div className="p-4 rounded-xl border border-red-500/40 bg-red-950/30 text-red-300 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-8 text-center text-slate-500 text-sm">加载中…</div>
      ) : tasks.length === 0 ? (
        <div className="py-12 rounded-xl glass-panel border border-slate-600/50 text-center text-slate-500 text-sm">
          暂无创作记录。在短剧页生成分镜/视频后将自动保存到此，刷新页面也可在「我的创作」中继续查看。
        </div>
      ) : (
        <ul className="space-y-2">
          {tasks.map((t) => (
            <li key={t.id}>
              <Link
                to={`${path(t)}?taskId=${t.id}`}
                className="block p-4 rounded-xl glass-panel border border-slate-600/50 hover:border-violet-500/40 hover:bg-slate-800/40 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <span className="text-xs px-2 py-0.5 rounded bg-violet-500/20 text-violet-300">
                      {PIPELINE_LABEL[t.pipeline]}
                    </span>
                    <p className="text-sm text-slate-200 mt-1 truncate" title={t.input_preview || t.input}>
                      {t.input_preview || t.input || "—"}
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">{formatDate(t.updated_at)}</p>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => handleDelete(t.id, e)}
                    className="flex-shrink-0 text-slate-500 hover:text-red-400 text-sm px-2 py-1 rounded"
                  >
                    删除
                  </button>
                </div>
                {t.merged_download_url && (
                  <p className="text-xs text-violet-400 mt-2">
                    <a
                      href={t.merged_download_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="hover:underline"
                    >
                      下载成片
                    </a>
                  </p>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
