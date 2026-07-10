"""
過去の試合データを一括取得して stats/ に保存するスクリプト
Discord には投稿しない（データ蓄積のみ）

Usage:
  python backfill.py              # 直近 100 試合
  python backfill.py --count 200  # 直近 200 試合
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── tracker.py と共通の設定 ──────────────────────────
RIOT_API_KEY    = os.environ["RIOT_API_KEY"]
GAME_NAME       = os.environ["GAME_NAME"]
TAG_LINE        = os.environ["TAG_LINE"]

PLATFORM = "jp1"
REGIONAL = "asia"
JST = timezone(timedelta(hours=9))

STATS_DIR = Path("stats")

QUEUE_FILE_MAP = {
    420: "ranked_solo",
    440: "ranked_flex",
    400: "normal",
    430: "normal",
    900: "normal",
    450: "normal",
    0:   "normal",
}

POSITION_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


# ── Riot API ──────────────────────────────────────────
def riot_get(url: str, params: dict = None) -> dict:
    headers = {"X-Riot-Token": RIOT_API_KEY}
    r = requests.get(url, headers=headers, params=params or {}, timeout=10)
    r.raise_for_status()
    return r.json()

def get_puuid() -> str:
    url = f"https://{REGIONAL}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}"
    return riot_get(url)["puuid"]

def get_match_ids(puuid: str, start: int, count: int) -> list[str]:
    url = f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
    return riot_get(url, {"start": start, "count": count})

def get_match(match_id: str) -> dict:
    url = f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    return riot_get(url)

def get_timeline(match_id: str) -> dict:
    url = f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    return riot_get(url)


# ── stats 保存 ────────────────────────────────────────
def load_stats(queue_key: str) -> list[dict]:
    path = STATS_DIR / f"{queue_key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []

def save_stats(queue_key: str, records: list[dict]):
    STATS_DIR.mkdir(exist_ok=True)
    path = STATS_DIR / f"{queue_key}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


# ── データ抽出（tracker.py と同じロジック） ───────────
def get_checkpoints(game_sec: int) -> list[int]:
    game_min = game_sec // 60
    if game_min < 5:
        return [game_min]
    cps = list(range(5, game_min, 5))
    if not cps or cps[-1] != game_min:
        cps.append(game_min)
    return cps

def extract_and_save(match: dict, timeline: dict, puuid: str):
    info         = match["info"]
    participants = info["participants"]
    me           = next((p for p in participants if p["puuid"] == puuid), None)
    if not me:
        return

    team_id     = me["teamId"]
    my_position = me.get("teamPosition", "")
    opponent    = next(
        (p for p in participants
         if p["teamId"] != team_id and p.get("teamPosition") == my_position),
        None,
    )
    enemy_jungler = next(
        (p for p in participants
         if p["teamId"] != team_id and p.get("teamPosition") == "JUNGLE"),
        None,
    )

    game_sec    = info["gameDuration"]
    cs_total    = me["totalMinionsKilled"] + me.get("neutralMinionsKilled", 0)
    my_damage   = me["totalDamageDealtToChampions"]
    gold_earned = me.get("goldEarned", 1)
    team_damage = sum(p["totalDamageDealtToChampions"] for p in participants if p["teamId"] == team_id)

    # タイムライン処理（GD@15 / CSD@15）
    pid_str  = str(me["participantId"])
    opp_str  = str(opponent["participantId"]) if opponent else None
    frames   = timeline["info"]["frames"] if timeline else []
    kill_log = [e for f in frames for e in f.get("events", []) if e.get("type") == "CHAMPION_KILL"]

    gd15 = csd15 = None
    cp15_ms = 15 * 60 * 1000
    if game_sec >= 15 * 60 - 30:
        target = None
        for f in frames:
            if f["timestamp"] <= cp15_ms:
                target = f
            else:
                break
        if target and opp_str:
            me_pf  = target["participantFrames"].get(pid_str, {})
            opp_pf = target["participantFrames"].get(opp_str, {})
            me_cs  = me_pf.get("minionsKilled", 0) + me_pf.get("jungleMinionsKilled", 0)
            opp_cs = opp_pf.get("minionsKilled", 0) + opp_pf.get("jungleMinionsKilled", 0)
            gd15   = me_pf.get("totalGold", 0) - opp_pf.get("totalGold", 0)
            csd15  = me_cs - opp_cs

    # デス原因
    pid_int = me["participantId"]
    opp_int = opponent["participantId"] if opponent else None
    ejg_int = enemy_jungler["participantId"] if enemy_jungler else None
    pid_to_champ = {p["participantId"]: p["championName"] for p in participants}

    deaths_detail = []
    for e in kill_log:
        if e.get("victimId") != pid_int:
            continue
        killer_id = e.get("killerId", 0)
        assists   = e.get("assistingParticipantIds", [])
        all_k     = [killer_id] + assists
        if ejg_int and ejg_int in all_k:
            cause = "ガンク"
        elif len(all_k) >= 4:
            cause = "集団戦"
        elif killer_id == opp_int or (opp_int in assists and len(all_k) <= 2):
            cause = "レーンキル"
        elif len(all_k) >= 2:
            cause = "集団戦"
        else:
            cause = "その他"
        deaths_detail.append({
            "time_min":        e["timestamp"] // 60000,
            "killer_champion": pid_to_champ.get(killer_id, "Unknown"),
            "assist_champions": [pid_to_champ.get(a, "Unknown") for a in assists],
            "cause":           cause,
        })

    solo_kills = sum(
        1 for e in kill_log
        if e.get("killerId") == pid_int and len(e.get("assistingParticipantIds", [])) == 0
    )

    queue_key = QUEUE_FILE_MAP.get(info["queueId"], "normal")
    records   = load_stats(queue_key)

    match_id    = match["metadata"]["matchId"]
    game_end_ts = info.get("gameEndTimestamp", info.get("gameCreation", 0))

    new_record = {
        "match_id":        match_id,
        "date":            datetime.fromtimestamp(game_end_ts / 1000, tz=JST).isoformat(),
        "lane":            me.get("teamPosition", "UNKNOWN"),
        "champion":        me["championName"],
        "opponent":        opponent["championName"] if opponent else "不明",
        "win":             me["win"],
        "kills":           me["kills"],
        "deaths":          me["deaths"],
        "assists":         me["assists"],
        "cs_per_min":      round(cs_total / max(game_sec / 60, 1), 1),
        "damage_share":    round(my_damage / max(team_damage, 1) * 100, 1),
        "gold_efficiency": round(my_damage / max(gold_earned / 1000, 0.1)),
        "gd15":            gd15,
        "csd15":           csd15,
        "game_duration_min": game_sec // 60,
        "solo_kills":      solo_kills,
        "first_blood":     me.get("firstBloodKill", False),
        "deaths_detail":   deaths_detail,
    }

    # 既存レコードを上書き or 新規追加
    existing_idx = next((i for i, r in enumerate(records) if r["match_id"] == match_id), None)
    if existing_idx is not None:
        records[existing_idx] = new_record
    else:
        records.append(new_record)

    save_stats(queue_key, records)


# ── メイン ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count",     type=int,  default=100,   help="取得する試合数（最大200）")
    parser.add_argument("--overwrite", action="store_true",      help="既存データも上書きして再取得する")
    args = parser.parse_args()

    total   = min(args.count, 200)
    print(f"[Backfill] 開始: {GAME_NAME}#{TAG_LINE} / 直近 {total} 試合")

    puuid = get_puuid()
    print(f"[Backfill] PUUID 取得完了")

    # 100件ずつ取得（API の上限）
    all_ids = []
    for start in range(0, total, 100):
        batch = get_match_ids(puuid, start=start, count=min(100, total - start))
        all_ids.extend(batch)
        if len(batch) < 100:
            break
        time.sleep(1)

    print(f"[Backfill] {len(all_ids)} 試合を取得")

    saved = skipped = failed = 0

    for i, match_id in enumerate(all_ids, 1):
        try:
            match    = get_match(match_id)
            timeline = get_timeline(match_id)

            queue_key = QUEUE_FILE_MAP.get(match["info"]["queueId"], "normal")
            records   = load_stats(queue_key)
            existing = any(r["match_id"] == match_id for r in records)
            if existing and not args.overwrite:
                skipped += 1
                print(f"[{i}/{len(all_ids)}] スキップ（保存済み）: {match_id}")
                continue

            extract_and_save(match, timeline, puuid)
            saved += 1
            print(f"[{i}/{len(all_ids)}] 保存: {match_id}")

            time.sleep(1.2)   # レート制限対策

        except Exception as e:
            failed += 1
            print(f"[{i}/{len(all_ids)}] 失敗: {match_id} → {e}")
            time.sleep(2)

    print(f"\n[Backfill] 完了 — 保存: {saved} / スキップ: {skipped} / 失敗: {failed}")


if __name__ == "__main__":
    main()
