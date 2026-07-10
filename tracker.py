"""
League of Legends Match Tracker → Discord
試合終了後に自動で勝敗・スタッツ・タイムラインを Discord へ投稿し、
stats/ にキュー別でデータを蓄積する
"""

import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 設定 ──────────────────────────────────────────────
RIOT_API_KEY    = os.environ["RIOT_API_KEY"]
GAME_NAME       = os.environ["GAME_NAME"]
TAG_LINE        = os.environ["TAG_LINE"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"].strip()

PLATFORM = "jp1"
REGIONAL = "asia"
JST = timezone(timedelta(hours=9))

LAST_MATCH_FILE = Path("last_match_id.txt")
STATS_DIR       = Path("stats")

# キューID → 保存ファイル名
QUEUE_FILE_MAP = {
    420: "ranked_solo",
    440: "ranked_flex",
    400: "normal",
    430: "normal",
    900: "normal",   # URF
    450: "normal",   # ARAM
    0:   "normal",   # カスタム
}

QUEUE_NAMES = {
    420: "ランク（ソロ/デュオ）",
    440: "ランク（フレックス）",
    450: "ARAM",
    400: "ノーマル（ドラフト）",
    430: "ノーマル（ブラインド）",
    900: "URF",
    1700: "アリーナ",
    0:   "カスタム",
}

TIER_EMOJIS = {
    "IRON": "⚫", "BRONZE": "🟤", "SILVER": "⚪",
    "GOLD": "🟡", "PLATINUM": "🟢", "EMERALD": "💚",
    "DIAMOND": "💎", "MASTER": "👑", "GRANDMASTER": "🏆", "CHALLENGER": "🔥",
}

LANE_EMOJI = {
    "TOP": "🛡️", "JUNGLE": "🌲", "MIDDLE": "🔮",
    "BOTTOM": "🏹", "UTILITY": "🩹", "": "❓",
}

POSITION_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


# ── Riot API ヘルパー ──────────────────────────────────
def riot_get(url: str, params: dict = None) -> dict:
    headers = {"X-Riot-Token": RIOT_API_KEY}
    r = requests.get(url, headers=headers, params=params or {}, timeout=10)
    r.raise_for_status()
    return r.json()

def get_puuid() -> str:
    url = f"https://{REGIONAL}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}"
    return riot_get(url)["puuid"]

def get_latest_match_ids(puuid: str, count: int = 10) -> list[str]:
    url = f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
    return riot_get(url, {"start": 0, "count": count})

def get_match(match_id: str) -> dict:
    url = f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    return riot_get(url)

def get_timeline(match_id: str) -> dict:
    url = f"https://{REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    return riot_get(url)

def get_rank(puuid: str) -> str | None:
    url = f"https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    entries = riot_get(url)
    for e in entries:
        if e.get("queueType") == "RANKED_SOLO_5x5":
            tier  = e["tier"]
            rank  = e["rank"]
            lp    = e["leaguePoints"]
            emoji = TIER_EMOJIS.get(tier, "")
            return f"{emoji} {tier} {rank} ({lp} LP)"
    return None


# ── チェックポイント算出 ──────────────────────────────
def get_checkpoints(game_sec: int) -> list[int]:
    game_min = game_sec // 60
    if game_min < 5:
        return [game_min]
    cps = list(range(5, game_min, 5))
    if not cps or cps[-1] != game_min:
        cps.append(game_min)
    return cps


# ── チーム構成 ────────────────────────────────────────
def extract_team_comps(match: dict, puuid: str) -> dict:
    info         = match["info"]
    participants = info["participants"]
    me           = next(p for p in participants if p["puuid"] == puuid)
    my_tid       = me["teamId"]
    enemy_tid    = 200 if my_tid == 100 else 100

    def sort_by_pos(players: list) -> list:
        return sorted(
            players,
            key=lambda p: POSITION_ORDER.index(p.get("teamPosition", ""))
            if p.get("teamPosition") in POSITION_ORDER else 9,
        )

    my_team    = sort_by_pos([p for p in participants if p["teamId"] == my_tid])
    enemy_team = sort_by_pos([p for p in participants if p["teamId"] == enemy_tid])

    def fmt(players: list, bold_puuid: str | None) -> str:
        parts = []
        for p in players:
            name = p["championName"]
            parts.append(f"**{name}**" if p["puuid"] == bold_puuid else name)
        return " / ".join(parts)

    blue_emoji = "🔵" if my_tid == 100 else "🔴"
    red_emoji  = "🔴" if my_tid == 100 else "🔵"

    return {
        "my_line":    f"{blue_emoji} {fmt(my_team, puuid)}",
        "enemy_line": f"{red_emoji} {fmt(enemy_team, None)}",
    }


# ── 最終スタッツ整形 ──────────────────────────────────
def extract_player_stats(match: dict, puuid: str) -> dict:
    info         = match["info"]
    participants = info["participants"]
    me           = next(p for p in participants if p["puuid"] == puuid)
    team_id      = me["teamId"]

    my_position = me.get("teamPosition", "")
    opponent    = next(
        (p for p in participants
         if p["teamId"] != team_id and p.get("teamPosition") == my_position),
        None,
    )

    team_data = next((t for t in info["teams"] if t["teamId"] == team_id), {})
    obj = team_data.get("objectives", {})

    game_sec    = info["gameDuration"]
    cs_total    = me["totalMinionsKilled"] + me.get("neutralMinionsKilled", 0)
    my_damage   = me["totalDamageDealtToChampions"]
    gold_earned = me.get("goldEarned", 1)

    kp_total       = sum(p["kills"] for p in participants if p["teamId"] == team_id)
    team_damage    = sum(p["totalDamageDealtToChampions"] for p in participants if p["teamId"] == team_id)
    team_dmg_sorted = sorted(
        [p["totalDamageDealtToChampions"] for p in participants if p["teamId"] == team_id],
        reverse=True,
    )

    # 敵ジャングラーの participantId（デス分類用）
    enemy_jungler = next(
        (p for p in participants
         if p["teamId"] != team_id and p.get("teamPosition") == "JUNGLE"),
        None,
    )

    return {
        "win":                me["win"],
        "champion":           me["championName"],
        "lane":               my_position,
        "queue_id":           info["queueId"],
        "kills":              me["kills"],
        "deaths":             me["deaths"],
        "assists":            me["assists"],
        "kda":                round((me["kills"] + me["assists"]) / max(me["deaths"], 1), 2),
        "cs":                 cs_total,
        "cs_per_min":         round(cs_total / max(game_sec / 60, 1), 1),
        "kp":                 round((me["kills"] + me["assists"]) / max(kp_total, 1) * 100),
        "damage_dealt":       my_damage,
        "damage_share":       round(my_damage / max(team_damage, 1) * 100, 1),
        "damage_rank":        team_dmg_sorted.index(my_damage) + 1,
        "gold_earned":        gold_earned,
        "gold_efficiency":    round(my_damage / max(gold_earned / 1000, 0.1)),
        "vision_score":       me["visionScore"],
        "wards_placed":       me["wardsPlaced"],
        "double_kills":       me.get("doubleKills", 0),
        "triple_kills":       me.get("tripleKills", 0),
        "quadra_kills":       me.get("quadraKills", 0),
        "penta_kills":        me.get("pentaKills", 0),
        "first_blood_kill":   me.get("firstBloodKill", False),
        "first_blood_assist": me.get("firstBloodAssist", False),
        "opponent":           opponent["championName"] if opponent else "不明",
        "opponent_pid":       opponent["participantId"] if opponent else None,
        "opponent_kda":       f"{opponent['kills']}/{opponent['deaths']}/{opponent['assists']}" if opponent else "-",
        "enemy_jungler_pid":  enemy_jungler["participantId"] if enemy_jungler else None,
        "queue_name":         QUEUE_NAMES.get(info["queueId"], f"キュー {info['queueId']}"),
        "game_duration":      f"{game_sec // 60}分{game_sec % 60}秒",
        "game_id":            match["metadata"]["matchId"],
        "game_end_ts":        info.get("gameEndTimestamp", info.get("gameCreation", 0)),
        "dragon_kills":       obj.get("dragon", {}).get("kills", 0),
        "baron_kills":        obj.get("baron", {}).get("kills", 0),
        "participant_id":     me["participantId"],
        "game_sec":           game_sec,
    }


# ── デス原因分類 ──────────────────────────────────────
def classify_deaths(
    timeline: dict,
    participant_id: int,
    opponent_pid: int | None,
    enemy_jungler_pid: int | None,
    pid_to_champion: dict[int, str],
) -> list[dict]:
    """自分が死んだ全イベントを分類して返す"""
    deaths = []
    for frame in timeline["info"]["frames"]:
        for event in frame.get("events", []):
            if event.get("type") != "CHAMPION_KILL":
                continue
            if event.get("victimId") != participant_id:
                continue

            time_min    = event["timestamp"] // 60000
            killer_id   = event.get("killerId", 0)
            assists     = event.get("assistingParticipantIds", [])
            all_killers = [killer_id] + assists

            killer_champ = pid_to_champion.get(killer_id, "Unknown")
            assist_champs = [pid_to_champion.get(a, "Unknown") for a in assists]

            # 死因分類
            if enemy_jungler_pid and enemy_jungler_pid in all_killers:
                cause = "ガンク"
            elif len(all_killers) >= 4:
                cause = "集団戦"
            elif killer_id == opponent_pid or (opponent_pid in assists and len(all_killers) <= 2):
                cause = "レーンキル"
            elif len(all_killers) >= 2:
                cause = "集団戦"
            else:
                cause = "その他"

            deaths.append({
                "time_min":       time_min,
                "killer_champion": killer_champ,
                "assist_champions": assist_champs,
                "cause":           cause,
            })

    return deaths


# ── ソロキル集計 ──────────────────────────────────────
def count_solo_kills(timeline: dict, participant_id: int) -> int:
    count = 0
    for frame in timeline["info"]["frames"]:
        for event in frame.get("events", []):
            if (
                event.get("type") == "CHAMPION_KILL"
                and event.get("killerId") == participant_id
                and len(event.get("assistingParticipantIds", [])) == 0
            ):
                count += 1
    return count


# ── タイムライン解析 ──────────────────────────────────
def extract_timeline_stats(
    timeline: dict,
    participant_id: int,
    opponent_pid: int | None,
    checkpoints: list[int],
    game_sec: int,
) -> list[dict | None]:
    frames  = timeline["info"]["frames"]
    pid_str = str(participant_id)
    opp_str = str(opponent_pid) if opponent_pid else None

    kill_log: list[dict] = []
    for frame in frames:
        for event in frame.get("events", []):
            if event.get("type") == "CHAMPION_KILL":
                kill_log.append(event)

    results = []
    for cp_min in checkpoints:
        cp_ms = cp_min * 60 * 1000

        if game_sec < cp_min * 60 - 30:
            results.append(None)
            continue

        target_frame = None
        for frame in frames:
            if frame["timestamp"] <= cp_ms:
                target_frame = frame
            else:
                break

        if target_frame is None:
            results.append(None)
            continue

        def snap(pid: str) -> dict:
            pf = target_frame["participantFrames"].get(pid, {})
            return {
                "cs":     pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0),
                "damage": pf.get("damageStats", {}).get("totalDamageDoneToChampions", 0),
                "gold":   pf.get("totalGold", 0),
                "level":  pf.get("level", 0),
            }

        me_s  = snap(pid_str)
        opp_s = snap(opp_str) if opp_str else None

        pid_int = participant_id
        opp_int = opponent_pid

        me_k = sum(1 for e in kill_log if e.get("killerId") == pid_int and e["timestamp"] <= cp_ms)
        me_d = sum(1 for e in kill_log if e.get("victimId") == pid_int and e["timestamp"] <= cp_ms)
        me_a = sum(1 for e in kill_log if pid_int in e.get("assistingParticipantIds", []) and e["timestamp"] <= cp_ms)

        if opp_int and opp_s:
            op_k = sum(1 for e in kill_log if e.get("killerId") == opp_int and e["timestamp"] <= cp_ms)
            op_d = sum(1 for e in kill_log if e.get("victimId") == opp_int and e["timestamp"] <= cp_ms)
            op_a = sum(1 for e in kill_log if opp_int in e.get("assistingParticipantIds", []) and e["timestamp"] <= cp_ms)
        else:
            op_k = op_d = op_a = None

        results.append({
            "min":         cp_min,
            "cs":          me_s["cs"],
            "cs_pm":       round(me_s["cs"] / cp_min, 1),
            "damage":      me_s["damage"],
            "gold":        me_s["gold"],
            "level":       me_s["level"],
            "me_kda":      f"{me_k}/{me_d}/{me_a}",
            "opp_kda":     f"{op_k}/{op_d}/{op_a}" if op_k is not None else None,
            "cs_diff":     (me_s["cs"]     - opp_s["cs"])     if opp_s else None,
            "gold_diff":   (me_s["gold"]   - opp_s["gold"])   if opp_s else None,
            "damage_diff": (me_s["damage"] - opp_s["damage"]) if opp_s else None,
        })

    return results


# ── タイムライン表テキスト生成 ────────────────────────
def _vlen(s: str) -> int:
    return sum(2 if ord(c) > 0x7F else 1 for c in s)

def _pad(s: str, w: int) -> str:
    return " " * max(w - _vlen(s), 0) + s

def _sign(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"{n:,}"

def build_timeline_table(checkpoints: list[dict | None]) -> str:
    valid = [c for c in checkpoints if c is not None]
    if not valid:
        return "（タイムラインデータなし）"

    headers  = [f"{c['min']}分" for c in valid]
    has_opp  = any(c.get("opp_kda") is not None for c in valid)
    has_diff = any(c.get("cs_diff") is not None for c in valid)

    self_rows: list[tuple[str, list[str]]] = [
        ("CS",      [str(c["cs"])       for c in valid]),
        ("CS/分",   [str(c["cs_pm"])    for c in valid]),
        ("Dmg",     [f"{c['damage']:,}" for c in valid]),
        ("自分KDA", [c["me_kda"]        for c in valid]),
    ]
    if has_opp:
        self_rows.append((
            "対面KDA",
            [c["opp_kda"] if c.get("opp_kda") else "-" for c in valid],
        ))
    self_rows += [
        ("Lv",   [str(c["level"])    for c in valid]),
        ("Gold", [f"{c['gold']:,}"   for c in valid]),
    ]

    diff_rows: list[tuple[str, list[str]]] = []
    if has_diff:
        diff_rows = [
            ("CS差",   [_sign(c["cs_diff"])     if c.get("cs_diff")     is not None else "-" for c in valid]),
            ("Gold差", [_sign(c["gold_diff"])   if c.get("gold_diff")   is not None else "-" for c in valid]),
            ("Dmg差",  [_sign(c["damage_diff"]) if c.get("damage_diff") is not None else "-" for c in valid]),
        ]

    all_rows = self_rows + diff_rows
    col_widths = [
        max(_vlen(h), max(_vlen(row[1][i]) for row in all_rows))
        for i, h in enumerate(headers)
    ]
    label_w = max(_vlen(row[0]) for row in all_rows)

    def make_row(label: str, values: list[str]) -> str:
        pad   = " " * max(label_w - _vlen(label), 0)
        cells = "  ".join(_pad(v, col_widths[i]) for i, v in enumerate(values))
        return f"{label}{pad} {cells}"

    sep = "─" * (label_w + 1 + sum(col_widths) + 2 * max(len(col_widths) - 1, 0))
    header_line = " " * (label_w + 1) + "  ".join(_pad(h, col_widths[i]) for i, h in enumerate(headers))

    lines = [header_line, sep]
    for row in self_rows:
        lines.append(make_row(*row))

    if diff_rows:
        lines.append(sep)
        vs_label = "vs 対面"
        lines.append(f"{vs_label}{' ' * max(label_w - _vlen(vs_label), 0)}")
        for row in diff_rows:
            lines.append(make_row(*row))

    return "\n".join(lines)


# ── stats.json 保存 ───────────────────────────────────
def load_stats(queue_key: str) -> list[dict]:
    path = STATS_DIR / f"{queue_key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []

def save_stats(queue_key: str, records: list[dict]):
    STATS_DIR.mkdir(exist_ok=True)
    path = STATS_DIR / f"{queue_key}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

def save_match_record(
    stats: dict,
    cp_data: list[dict | None],
    deaths_detail: list[dict],
):
    """試合データをキュー別に追記する（重複は skip）"""
    queue_key = QUEUE_FILE_MAP.get(stats["queue_id"], "normal")
    records   = load_stats(queue_key)

    # 既に保存済みならスキップ
    if any(r["match_id"] == stats["game_id"] for r in records):
        return

    # GD@15 / CSD@15
    cp15  = next((c for c in cp_data if c and c["min"] == 15), None)
    gd15  = cp15["gold_diff"]  if cp15 and cp15.get("gold_diff")  is not None else None
    csd15 = cp15["cs_diff"]    if cp15 and cp15.get("cs_diff")    is not None else None

    record = {
        "match_id":        stats["game_id"],
        "date":            datetime.fromtimestamp(stats["game_end_ts"] / 1000, tz=JST).isoformat(),
        "lane":            stats["lane"],
        "champion":        stats["champion"],
        "opponent":        stats["opponent"],
        "win":             stats["win"],
        "kills":           stats["kills"],
        "deaths":          stats["deaths"],
        "assists":         stats["assists"],
        "cs_per_min":      stats["cs_per_min"],
        "damage_share":    stats["damage_share"],
        "gold_efficiency": stats["gold_efficiency"],
        "gd15":            gd15,
        "csd15":           csd15,
        "game_duration_min": stats["game_sec"] // 60,
        "solo_kills":      0,
        "first_blood":     stats["first_blood_kill"],
        "deaths_detail":   deaths_detail,
    }

    records.append(record)
    save_stats(queue_key, records)
    print(f"[Stats] 保存完了: {queue_key}/{stats['game_id']}")


# ── Discord 投稿 ──────────────────────────────────────
def post_to_discord(
    stats: dict,
    comps: dict,
    checkpoints: list[dict | None],
    solo_kills: int,
    rank_str: str | None,
    summoner_name: str,
):
    win         = stats["win"]
    color       = 0x57F287 if win else 0xED4245
    result_text = "🏆 **勝利**" if win else "💀 **敗北**"

    kda_str    = f"{stats['kills']}/{stats['deaths']}/{stats['assists']}"
    multi_kill = ""
    if stats["penta_kills"]:    multi_kill = "🎉 **ペンタキル！！**"
    elif stats["quadra_kills"]: multi_kill = "✨ クアドラキル"
    elif stats["triple_kills"]: multi_kill = "⚡ トリプルキル"
    elif stats["double_kills"]: multi_kill = "👊 ダブルキル"

    if stats["first_blood_kill"]:
        fb_str = "🩸 **ファーストブラッド**"
    elif stats["first_blood_assist"]:
        fb_str = "🩸 ファーストブラッド（アシスト）"
    else:
        fb_str = None

    cp15 = next((c for c in checkpoints if c and c["min"] == 15), None)
    gd15_str = None
    if cp15 and cp15.get("gold_diff") is not None:
        gd  = cp15["gold_diff"]
        csd = cp15["cs_diff"]
        gd15_str = (
            f"{'📈' if gd  >= 0 else '📉'} GD@15: `{_sign(gd)}`　"
            f"{'📈' if csd >= 0 else '📉'} CSD@15: `{_sign(csd)}`"
        )

    end_dt  = datetime.fromtimestamp(stats["game_end_ts"] / 1000, tz=JST)
    end_str = end_dt.strftime("%Y/%m/%d %H:%M")
    lane_emoji = LANE_EMOJI.get(stats["lane"], "❓")

    desc_lines = [
        f"{result_text}　{stats['queue_name']}　{stats['game_duration']}",
        "",
        comps["my_line"],
        comps["enemy_line"],
        "",
        f"**{lane_emoji} {stats['champion']}** vs {stats['opponent']}（対面最終KDA: {stats['opponent_kda']}）",
        "",
        f"KDA: `{kda_str}` ({stats['kda']})　KP: `{stats['kp']}%`　ソロキル: `{solo_kills}`",
        f"CS: `{stats['cs']}` ({stats['cs_per_min']}/分)　ダメージ: `{stats['damage_dealt']:,}`",
        f"ダメージシェア: `{stats['damage_share']}%`（チーム{stats['damage_rank']}位）　効率: `{stats['gold_efficiency']:,} dmg/1000g`",
        f"ビジョン: `{stats['vision_score']}`　ワード: `{stats['wards_placed']}`",
        f"ドラゴン: `{stats['dragon_kills']}`　バロン: `{stats['baron_kills']}`",
    ]
    if gd15_str:
        desc_lines += ["", gd15_str]
    if fb_str:
        desc_lines += ["", fb_str]
    if multi_kill:
        desc_lines += ["", multi_kill]

    footer_text = f"Match ID: {stats['game_id']}　| {end_str} JST"
    if rank_str:
        footer_text = f"{rank_str}　|　{footer_text}"

    embed_main = {
        "title":       f"{summoner_name} の試合結果",
        "description": "\n".join(desc_lines),
        "color":       color,
        "footer":      {"text": footer_text},
    }

    table = build_timeline_table(checkpoints)
    embed_timeline = {
        "title":       "📊 タイムライン（ボットレーン）",
        "description": f"```\n{table}\n```",
        "color":       color,
    }

    payload = {"embeds": [embed_main, embed_timeline]}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[Discord] 投稿完了: {stats['game_id']} ({result_text.strip()})")


# ── 永続化 ────────────────────────────────────────────
def load_last_match_id() -> str | None:
    if LAST_MATCH_FILE.exists():
        return LAST_MATCH_FILE.read_text(encoding="utf-8").strip() or None
    return None

def save_last_match_id(match_id: str):
    LAST_MATCH_FILE.write_text(match_id, encoding="utf-8")


# ── メイン ────────────────────────────────────────────
def main():
    print(f"[Tracker] 開始: {GAME_NAME}#{TAG_LINE} (JP1)")

    puuid     = get_puuid()
    last_id   = load_last_match_id()
    match_ids = get_latest_match_ids(puuid, count=10)

    if not match_ids:
        print("[Tracker] 試合なし")
        return

    new_ids = []
    for mid in match_ids:
        if mid == last_id:
            break
        new_ids.append(mid)

    if not new_ids:
        print("[Tracker] 新しい試合なし")
        save_last_match_id(match_ids[0])
        return

    print(f"[Tracker] 新しい試合 {len(new_ids)} 件を投稿します")

    try:
        rank_str = get_rank(puuid)
    except Exception as e:
        print(f"[Tracker] ランク取得失敗: {e}")
        rank_str = None

    summoner_name = f"{GAME_NAME}#{TAG_LINE}"

    success_count = 0

    for match_id in reversed(new_ids):
        try:
            match = get_match(match_id)
            stats = extract_player_stats(match, puuid)
            comps = extract_team_comps(match, puuid)
            checkpoints = get_checkpoints(stats["game_sec"])

            pid_to_champion = {
                p["participantId"]: p["championName"]
                for p in match["info"]["participants"]
            }

            try:
                timeline      = get_timeline(match_id)
                cp_data       = extract_timeline_stats(
                    timeline,
                    stats["participant_id"],
                    stats["opponent_pid"],
                    checkpoints,
                    stats["game_sec"],
                )
                solo_kills    = count_solo_kills(timeline, stats["participant_id"])
                deaths_detail = classify_deaths(
                    timeline,
                    stats["participant_id"],
                    stats["opponent_pid"],
                    stats["enemy_jungler_pid"],
                    pid_to_champion,
                )
            except Exception as e:
                print(f"[Tracker] タイムライン取得失敗: {e}")
                cp_data       = [None] * len(checkpoints)
                solo_kills    = 0
                deaths_detail = []

            post_to_discord(stats, comps, cp_data, solo_kills, rank_str, summoner_name)

            # stats 保存
            save_match_record(stats, cp_data, deaths_detail)
            queue_key = QUEUE_FILE_MAP.get(stats["queue_id"], "normal")
            records   = load_stats(queue_key)
            for r in records:
                if r["match_id"] == stats["game_id"]:
                    r["solo_kills"] = solo_kills
                    break
            save_stats(queue_key, records)

            success_count += 1
            time.sleep(1.5)
        except Exception as e:
            print(f"[Tracker] {match_id} の処理失敗: {e}")

    # 1件でも成功した場合のみ最新IDを記録する
    if success_count > 0:
        save_last_match_id(match_ids[0])

    print(f"[Tracker] 完了（{success_count}/{len(new_ids)} 件成功）")


if __name__ == "__main__":
    main()
