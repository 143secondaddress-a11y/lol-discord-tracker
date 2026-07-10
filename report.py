"""
LoL Match Tracker — 分析レポート生成
stats/*.json のデータを集計して Discord にレポートを投稿する

Usage:
  python report.py                          # ランクソロのレポート
  python report.py --queue ranked_flex      # フレックスのレポート
  python report.py --queue normal           # ノーマルのレポート
  python report.py --queue all              # 全キュー合算
  python report.py --days 60               # 直近60日（デフォルト30日）
  python report.py --min-games 3           # マッチアップ最低試合数（デフォルト3）
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"].strip()
STATS_DIR       = Path("stats")
JST = timezone(timedelta(hours=9))

QUEUE_LABELS = {
    "ranked_solo":  "ランク ソロ/デュオ",
    "ranked_flex":  "ランク フレックス",
    "ranked":       "ランク（ソロ/デュオ＋フレックス）",
    "normal":       "ノーマル",
    "all":          "全キュー（ランク＋ノーマル）",
}

# キュー種別 → 読み込む stats ファイルのリスト
QUEUE_KEYS_MAP = {
    "ranked_solo": ["ranked_solo"],
    "ranked_flex": ["ranked_flex"],
    "ranked":      ["ranked_solo", "ranked_flex"],
    "normal":      ["normal"],
    "all":         ["ranked_solo", "ranked_flex", "normal"],
    # aram は意図的にどの集計にも含まない
}

CAUSE_ORDER = ["ガンク", "レーンキル", "集団戦", "その他"]

LANE_LABELS = {
    "TOP":     "トップ",
    "JUNGLE":  "ジャングル",
    "MIDDLE":  "ミッド",
    "BOTTOM":  "ボットレーン（ADC）",
    "UTILITY": "サポート",
    "ALL":     "全ロール",
}


# ── データ読み込み ─────────────────────────────────────
def load_records(queue: str, days: int, lane: str = "ALL") -> list[dict]:
    cutoff = datetime.now(tz=JST) - timedelta(days=days)
    keys   = QUEUE_KEYS_MAP.get(queue, [queue])

    records = []
    for key in keys:
        path = STATS_DIR / f"{key}.json"
        if not path.exists():
            continue
        for r in json.loads(path.read_text(encoding="utf-8")):
            dt = datetime.fromisoformat(r["date"])
            if dt >= cutoff:
                records.append(r)

    if lane != "ALL":
        records = [r for r in records if r.get("lane", "UNKNOWN") == lane]

    return sorted(records, key=lambda r: r["date"])


# ── マッチアップ分析 ──────────────────────────────────
def analyze_matchups(records: list[dict], min_games: int) -> dict:
    """対面チャンピオン別の勝率・平均GD@15・CSD@15を集計"""
    data: dict[str, dict] = defaultdict(lambda: {
        "wins": 0, "games": 0,
        "gd15_sum": 0, "gd15_count": 0,
        "csd15_sum": 0, "csd15_count": 0,
        "kda_kills": 0, "kda_deaths": 0, "kda_assists": 0,
    })

    for r in records:
        opp = r.get("opponent", "不明")
        if opp == "不明":
            continue
        d = data[opp]
        d["games"]        += 1
        d["wins"]         += int(r["win"])
        d["kda_kills"]    += r["kills"]
        d["kda_deaths"]   += r["deaths"]
        d["kda_assists"]  += r["assists"]
        if r.get("gd15") is not None:
            d["gd15_sum"]   += r["gd15"]
            d["gd15_count"] += 1
        if r.get("csd15") is not None:
            d["csd15_sum"]   += r["csd15"]
            d["csd15_count"] += 1

    results = {}
    for opp, d in data.items():
        if d["games"] < min_games:
            continue
        avg_kda = round(
            (d["kda_kills"] + d["kda_assists"]) / max(d["kda_deaths"], 1), 2
        )
        results[opp] = {
            "games":    d["games"],
            "wins":     d["wins"],
            "win_rate": round(d["wins"] / d["games"] * 100),
            "avg_gd15":  round(d["gd15_sum"]  / d["gd15_count"])  if d["gd15_count"]  else None,
            "avg_csd15": round(d["csd15_sum"] / d["csd15_count"]) if d["csd15_count"] else None,
            "avg_kda":  avg_kda,
        }

    return results


# ── デス原因分析 ──────────────────────────────────────
def analyze_deaths(records: list[dict]) -> dict:
    cause_count: dict[str, int] = defaultdict(int)
    total = 0

    for r in records:
        for death in r.get("deaths_detail", []):
            cause = death.get("cause", "その他")
            cause_count[cause] += 1
            total += 1

    if total == 0:
        return {}

    result = {}
    for cause in CAUSE_ORDER:
        count = cause_count.get(cause, 0)
        result[cause] = {
            "count": count,
            "pct":   round(count / total * 100),
        }
    return result


# ── テキスト生成ヘルパー ──────────────────────────────
def bar(pct: int, width: int = 16) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)

def _sign(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"{n:,}"

def matchup_line(opp: str, d: dict) -> str:
    w = d["wins"]
    g = d["games"]
    wr = d["win_rate"]
    gd_str  = _sign(d["avg_gd15"])  if d["avg_gd15"]  is not None else "  -"
    csd_str = _sign(d["avg_csd15"]) if d["avg_csd15"] is not None else " -"
    return (
        f"{opp:<14}  {w}勝{g - w}敗  {wr:>3}%"
        f"  GD@15avg {gd_str:>7}"
        f"  CSD@15avg {csd_str:>4}"
        f"  KDA {d['avg_kda']}"
    )


# ── Discord 投稿 ──────────────────────────────────────
def post_report(
    queue: str,
    lane: str,
    records: list[dict],
    matchups: dict,
    deaths: dict,
    days: int,
    min_games: int,
):
    if not records:
        print("[Report] データなし")
        return

    label      = QUEUE_LABELS.get(queue, queue)
    lane_label = LANE_LABELS.get(lane, lane)
    total  = len(records)
    wins   = sum(int(r["win"]) for r in records)
    wr     = round(wins / total * 100)
    dates  = [r["date"][:10] for r in records]
    embeds = []

    # ── Embed 1: サマリー ──
    avg_cs  = round(sum(r["cs_per_min"]    for r in records) / total, 1)
    avg_dmg = round(sum(r["damage_share"]  for r in records) / total, 1)
    avg_eff = round(sum(r["gold_efficiency"] for r in records) / total)

    summary_lines = [
        f"**{total}試合**　{wins}勝{total - wins}敗　勝率 **{wr}%**",
        f"期間: {dates[0]} 〜 {dates[-1]}",
        "",
        f"平均 CS/分: `{avg_cs}`　平均ダメージシェア: `{avg_dmg}%`　平均ゴールド効率: `{avg_eff:,}`",
    ]

    role_suffix = f"　{lane_label}" if lane != "ALL" else ""
    embeds.append({
        "title":       f"📈 {label} 分析レポート（直近 {days} 日{role_suffix}）",
        "description": "\n".join(summary_lines),
        "color":       0x5865F2,
    })

    # ── Embed 2: マッチアップ ──
    if matchups:
        favorable   = sorted(matchups.items(), key=lambda x: (-x[1]["win_rate"], -x[1]["games"]))
        unfavorable = sorted(matchups.items(), key=lambda x:  (x[1]["win_rate"], -x[1]["games"]))

        top_favor   = [item for item in favorable   if item[1]["win_rate"] >= 60][:5]
        top_unfavor = [item for item in unfavorable if item[1]["win_rate"] <= 40][:5]

        mu_lines = []
        if top_favor:
            mu_lines.append("🏆 **得意マッチアップ**")
            for opp, d in top_favor:
                mu_lines.append(f"`{matchup_line(opp, d)}`")
        if top_unfavor:
            if mu_lines:
                mu_lines.append("")
            mu_lines.append("⚠️ **苦手マッチアップ**")
            for opp, d in top_unfavor:
                mu_lines.append(f"`{matchup_line(opp, d)}`")

        if not top_favor and not top_unfavor:
            mu_lines.append(f"まだ {min_games} 試合以上のマッチアップデータがありません")

        embeds.append({
            "title":       f"⚔️ マッチアップ分析（{min_games}試合以上）",
            "description": "\n".join(mu_lines),
            "color":       0x5865F2,
        })

    # ── Embed 3: デス原因 ──
    if deaths:
        total_deaths = sum(d["count"] for d in deaths.values())
        death_lines  = [f"合計デス数: **{total_deaths}** 回\n"]
        for cause, d in deaths.items():
            if d["count"] == 0:
                continue
            death_lines.append(
                f"**{cause}**　`{d['count']}回  {d['pct']:>3}%  {bar(d['pct'])}`"
            )

        embeds.append({
            "title":       "💀 デス原因分析",
            "description": "\n".join(death_lines),
            "color":       0x5865F2,
        })

    payload = {"embeds": embeds}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[Report] 投稿完了 ({label} / {total}試合)")


# ── メイン ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="LoL Match Report Generator")
    parser.add_argument("--queue",     default="ranked_solo",
                        choices=["ranked_solo", "ranked_flex", "ranked", "normal", "all"])
    parser.add_argument("--lane",      default="ALL",
                        choices=["ALL", "TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"])
    parser.add_argument("--days",      type=int, default=30)
    parser.add_argument("--min-games", type=int, default=3)
    args = parser.parse_args()

    print(f"[Report] 集計: {args.queue} / 直近{args.days}日 / マッチアップ最低{args.min_games}試合")

    records  = load_records(args.queue, args.days, args.lane)
    matchups = analyze_matchups(records, args.min_games)
    deaths   = analyze_deaths(records)

    post_report(args.queue, args.lane, records, matchups, deaths, args.days, args.min_games)


if __name__ == "__main__":
    main()
