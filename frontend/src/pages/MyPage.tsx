/** 我的 · 会员、积分、今日配额、签到、兑换会员、付费充值入口（占位） */
import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import {
  getMeProfile,
  getMembershipTiers,
  getMePointsHistory,
  signIn,
  redeemMembership,
  type MeProfile,
  type MembershipTierInfo,
  type PointTransactionItem,
} from "../api";

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function quotaText(quota: number): string {
  return quota < 0 ? "不限" : String(quota);
}

export default function MyPage() {
  const [profile, setProfile] = useState<MeProfile | null>(null);
  const [tiers, setTiers] = useState<MembershipTierInfo[]>([]);
  const [pointsHistory, setPointsHistory] = useState<PointTransactionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showRecharge, setShowRecharge] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [redeeming, setRedeeming] = useState<string | null>(null);

  const loadProfile = async () => {
    setError(null);
    try {
      const p = await getMeProfile();
      setProfile(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setProfile(null);
    } finally {
      setLoading(false);
    }
  };

  const loadTiers = async () => {
    try {
      const list = await getMembershipTiers();
      setTiers(list.filter((t) => t.code !== "free"));
    } catch {
      setTiers([]);
    }
  };

  const loadHistory = async () => {
    if (!showHistory) return;
    try {
      const list = await getMePointsHistory({ limit: 30, offset: 0 });
      setPointsHistory(list);
    } catch {
      setPointsHistory([]);
    }
  };

  useEffect(() => {
    loadProfile();
    loadTiers();
  }, []);

  useEffect(() => {
    loadHistory();
  }, [showHistory]);

  const handleSignIn = async () => {
    setSigningIn(true);
    setError(null);
    try {
      await signIn();
      await loadProfile();
    } catch (e) {
      setError(e instanceof Error ? e.message : "签到失败");
    } finally {
      setSigningIn(false);
    }
  };

  const handleRedeem = async (tierCode: string, months: number = 1) => {
    setRedeeming(tierCode);
    setError(null);
    try {
      await redeemMembership(tierCode, months);
      await loadProfile();
    } catch (e) {
      setError(e instanceof Error ? e.message : "兑换失败");
    } finally {
      setRedeeming(null);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-xl font-semibold text-slate-200">我的</h1>
        <div className="py-12 text-center text-slate-500 text-sm">加载中…</div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-200">我的</h1>

      {error && (
        <div className="p-4 rounded-xl border border-red-500/40 bg-red-950/30 text-red-300 text-sm">
          {error}
        </div>
      )}

      {profile && (
        <>
          {/* 会员与积分概览 */}
          <section className="rounded-xl glass-panel border border-slate-600/50 p-4 space-y-4">
            <h2 className="text-sm font-medium text-slate-400">账号概览</h2>
            <div className="grid grid-cols-2 gap-4">
              <div className="p-3 rounded-lg bg-slate-800/60 border border-slate-600/50">
                <p className="text-xs text-slate-500 mb-0.5">当前档位</p>
                <p className="font-medium text-cyan-300">{profile.membership.tier_name}</p>
                {profile.membership.expires_at && (
                  <p className="text-xs text-slate-500 mt-1">到期：{formatDate(profile.membership.expires_at)}</p>
                )}
              </div>
              <div className="p-3 rounded-lg bg-slate-800/60 border border-slate-600/50">
                <p className="text-xs text-slate-500 mb-0.5">积分余额</p>
                <p className="font-medium text-amber-300">{profile.points.balance}</p>
              </div>
            </div>
            <div className="p-3 rounded-lg bg-slate-800/40 border border-slate-600/40">
              <p className="text-xs text-slate-500 mb-1">今日已用 / 配额</p>
              <p className="text-slate-200">
                内容 {profile.usage_today.content_count} 次 · 视频 {profile.usage_today.video_count} 次
                <span className="text-slate-500 ml-2">
                  （{profile.usage_today.total_used} / {quotaText(profile.usage_today.quota)}）
                </span>
              </p>
              {!profile.usage_today.can_use && profile.usage_today.quota >= 0 && (
                <p className="text-amber-400 text-xs mt-1">今日配额已用完，明日恢复或升级会员</p>
              )}
            </div>
          </section>

          {/* 签到 */}
          <section className="rounded-xl glass-panel border border-slate-600/50 p-4">
            <h2 className="text-sm font-medium text-slate-400 mb-2">每日签到</h2>
            <p className="text-xs text-slate-500 mb-3">签到可获得 10 积分</p>
            <button
              type="button"
              onClick={handleSignIn}
              disabled={signingIn}
              className="px-4 py-2 rounded-xl bg-amber-600/80 text-amber-100 font-medium hover:bg-amber-600 disabled:opacity-50"
            >
              {signingIn ? "签到中…" : "签到"}
            </button>
          </section>

          {/* 付费充值（占位） */}
          <section className="rounded-xl glass-panel border border-slate-600/50 p-4">
            <h2 className="text-sm font-medium text-slate-400 mb-2">付费充值</h2>
            <p className="text-xs text-slate-500 mb-3">积分不足时可在此购买积分，用于兑换会员等</p>
            <button
              type="button"
              onClick={() => setShowRecharge(true)}
              className="px-4 py-2 rounded-xl bg-emerald-600/80 text-emerald-100 font-medium hover:bg-emerald-600 border border-emerald-500/40"
            >
              去充值
            </button>
          </section>

          {/* 积分兑换会员 */}
          {tiers.length > 0 && (
            <section className="rounded-xl glass-panel border border-slate-600/50 p-4 space-y-3">
              <h2 className="text-sm font-medium text-slate-400">积分兑换会员</h2>
              <p className="text-xs text-slate-500">使用积分兑换更高档位，提升每日配额与导出权限</p>
              <ul className="space-y-2">
                {tiers.map((t) => (
                  <li
                    key={t.code}
                    className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-lg bg-slate-800/50 border border-slate-600/40"
                  >
                    <div>
                      <span className="font-medium text-slate-200">{t.name}</span>
                      <span className="text-slate-500 text-sm ml-2">
                        {quotaText(t.daily_task_quota)} 次/日 · {t.price_per_month_credits} 积分/月
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRedeem(t.code, 1)}
                      disabled={redeeming !== null || profile.points.balance < t.price_per_month_credits}
                      className="px-3 py-1.5 rounded-lg text-sm font-medium bg-cyan-600/80 text-cyan-100 hover:bg-cyan-600 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {redeeming === t.code ? "兑换中…" : "兑换 1 个月"}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 积分流水 */}
          <section className="rounded-xl glass-panel border border-slate-600/50 p-4">
            <button
              type="button"
              onClick={() => setShowHistory(!showHistory)}
              className="flex items-center justify-between w-full text-left"
            >
              <h2 className="text-sm font-medium text-slate-400">积分流水</h2>
              <span className="text-slate-500 text-sm">{showHistory ? "收起" : "展开"}</span>
            </button>
            {showHistory && (
              <ul className="mt-3 space-y-1.5 max-h-60 overflow-y-auto">
                {pointsHistory.length === 0 ? (
                  <li className="text-slate-500 text-sm py-2">暂无流水</li>
                ) : (
                  pointsHistory.map((tx) => (
                    <li key={tx.id} className="flex justify-between text-sm py-1.5 border-b border-slate-700/50 last:border-0">
                      <span className="text-slate-400">
                        {tx.type === "sign_in" && "签到"}
                        {tx.type === "redeem_membership" && "兑换会员"}
                        {tx.type === "task_content" && "内容生成"}
                        {tx.type === "task_video" && "视频生成"}
                        {!["sign_in", "redeem_membership", "task_content", "task_video"].includes(tx.type) && tx.description}
                        {!tx.description && tx.type}
                      </span>
                      <span className={tx.amount >= 0 ? "text-emerald-400" : "text-red-400"}>
                        {tx.amount >= 0 ? "+" : ""}{tx.amount}
                      </span>
                    </li>
                  ))
                )}
              </ul>
            )}
          </section>
        </>
      )}

      <p className="text-xs text-slate-500">
        <Link to="/" className="text-cyan-400 hover:underline">返回首页</Link>
        <span className="mx-2">·</span>
        <Link to="/history" className="text-cyan-400 hover:underline">我的创作</Link>
      </p>

      {/* 付费充值弹窗（占位） */}
      {showRecharge && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setShowRecharge(false)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="rounded-2xl glass-panel border border-slate-600 p-6 max-w-sm w-full mx-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-slate-200 mb-2">付费充值</h3>
            <p className="text-sm text-slate-400 mb-4">
              积分充值功能即将上线，届时支持微信/支付宝等支付方式购买积分，用于兑换会员与更多权益。
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowRecharge(false)}
                className="px-4 py-2 rounded-xl bg-slate-600 text-slate-200 hover:bg-slate-500"
              >
                关闭
              </button>
              <button
                type="button"
                className="px-4 py-2 rounded-xl bg-emerald-600 text-white hover:bg-emerald-500"
                disabled
              >
                即将上线
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
