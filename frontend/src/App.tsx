import { Routes, Route, Link, useLocation } from "react-router-dom";
import HomePage from "./pages/HomePage";
import DramaPage from "./pages/DramaPage";
import HistoryPage from "./pages/HistoryPage";
import MyPage from "./pages/MyPage";

const DRAMA_NAME = "小剧";

export default function App() {
  const location = useLocation();
  const isDrama = location.pathname === "/drama";

  return (
    <div className="min-h-screen flex flex-col relative">
      <header className="glass-panel sticky top-0 z-20 border-b border-slate-700/50 relative overflow-hidden">
        <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-violet-500/30 to-transparent opacity-80" />
        <div className="max-w-3xl mx-auto px-4 py-4 relative">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <Link
              to="/"
              className="text-xl font-semibold tracking-tight transition-all duration-200 hover:opacity-90"
            >
              <span className="text-gradient-violet">{DRAMA_NAME}</span>
            </Link>
            <nav className="flex items-center gap-2 text-sm">
              <Link
                to="/drama"
                className={`px-3 py-2 rounded-lg font-medium transition-all duration-200 ${
                  isDrama
                    ? "bg-violet-500/20 text-violet-300 border border-violet-500/50 shadow-[0_0_16px_rgba(167,139,250,0.2)]"
                    : "text-slate-400 hover:text-violet-300 hover:bg-slate-700/40 border border-transparent hover:border-violet-500/20"
                }`}
              >
                {DRAMA_NAME}
              </Link>
              <Link
                to="/history"
                className={`px-3 py-2 rounded-lg font-medium transition-all duration-200 ${
                  location.pathname === "/history"
                    ? "bg-slate-600 text-slate-200 border border-slate-500/50"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/40 border border-transparent hover:border-slate-500/20"
                }`}
              >
                我的创作
              </Link>
              <Link
                to="/me"
                className={`px-3 py-2 rounded-lg font-medium transition-all duration-200 ${
                  location.pathname === "/me"
                    ? "bg-amber-500/20 text-amber-300 border border-amber-500/50"
                    : "text-slate-400 hover:text-amber-300 hover:bg-slate-700/40 border border-transparent hover:border-amber-500/20"
                }`}
              >
                我的
              </Link>
            </nav>
          </div>
        </div>
      </header>

      <main className={`flex-1 w-full relative z-10 flex flex-col min-h-0 ${isDrama ? "px-0" : "mx-auto px-4 py-6 max-w-3xl"}`}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/drama" element={<DramaPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/me" element={<MyPage />} />
        </Routes>
      </main>

      <footer className="relative z-10 border-t border-slate-700/50 py-3 text-center text-slate-500 text-xs bg-gradient-to-t from-slate-900/30 to-transparent">
        {DRAMA_NAME}（上传角色参考图）
      </footer>
    </div>
  );
}
