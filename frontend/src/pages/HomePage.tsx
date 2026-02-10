/** 首页：进入小剧（短剧生成） */
import { Link } from "react-router-dom";

const DRAMA_NAME = "小剧";
const DRAMA_TAGLINE = "你的短剧小导演";

export default function HomePage() {
  return (
    <div className="flex flex-col gap-10 max-w-xl mx-auto">
      <div className="flex flex-col gap-2">
        <p className="text-slate-500 text-sm flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-violet-400/80 shadow-[0_0_8px_rgba(167,139,250,0.4)]" />
          选择助手开始
        </p>
        <h1 className="text-2xl font-bold text-slate-200 tracking-tight">
          智能创作 <span className="text-slate-500 font-normal">·</span> 一键成片
        </h1>
      </div>
      <p className="text-sm text-slate-500 flex items-center gap-2 flex-wrap">
        <Link to="/history" className="text-violet-400 hover:underline">我的创作</Link>
        <span className="text-slate-600">·</span>
        <Link to="/me" className="text-amber-400 hover:underline">我的</Link>
        <span className="text-slate-600">·</span>
        <span>会员、积分与充值</span>
      </p>
      <div className="grid grid-cols-1 gap-5">
        <Link
          to="/drama"
          className="group relative px-6 py-6 rounded-2xl overflow-hidden glass-panel card-hover border border-violet-500/25 hover:border-violet-400/50 hover:shadow-[0_0_32px_rgba(167,139,250,0.12),inset_0_1px_0_rgba(255,255,255,0.04)]"
        >
          <div className="absolute inset-0 bg-gradient-to-br from-violet-500/08 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
          <div className="absolute top-0 right-0 w-24 h-24 bg-violet-500/5 rounded-full blur-2xl -translate-y-1/2 translate-x-1/2 group-hover:bg-violet-500/10 transition-colors" />
          <div className="relative">
            <span className="font-semibold block text-lg text-gradient-violet">{DRAMA_NAME}</span>
            <span className="text-xs text-violet-400/90 mt-0.5 block">{DRAMA_TAGLINE}</span>
            <span className="text-sm mt-3 text-slate-400 block leading-relaxed">粘贴剧本/对白，上传角色参考图，生成分镜与视频；可选添加 BGM</span>
          </div>
          <span className="absolute top-4 right-4 text-violet-500/50 group-hover:text-violet-400 group-hover:translate-x-0.5 transition-all duration-200 text-lg">→</span>
        </Link>
      </div>
    </div>
  );
}
