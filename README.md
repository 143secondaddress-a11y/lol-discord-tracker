# LoL Discord Match Tracker

League of Legends の試合結果を自動で Discord に投稿するボットです。

## 投稿内容

- 勝敗・キュー種別・ゲーム時間
- 使用チャンピオン・対面チャンピオン（KDA 付き）
- KDA・KP率・CS・CS/分・ダメージ
- ビジョンスコア・ドラゴン/バロン獲得数
- マルチキル（ペンタキルなど）
- 現在のランク（ソロ）

## セットアップ手順

### 1. Riot Games API キー取得

1. https://developer.riotgames.com にアクセスしてログイン
2. ダッシュボードで **Development API Key** をコピー
   - 開発用は 24 時間で期限切れ → 毎日更新が必要
   - 継続利用したい場合は **Personal API Key** を申請（無料）

### 2. Discord Webhook URL 取得

1. Discord で通知を送りたいサーバー・チャンネルを開く
2. チャンネル設定 → 連携サービス → **ウェブフック** → 新しいウェブフック
3. Webhook URL をコピー

### 3. GitHub リポジトリ作成 & Secrets 設定

```bash
cd lol-discord-tracker
git init
git add .
git commit -m "initial commit"
# GitHub で新規リポジトリを作成後:
git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
git push -u origin main
```

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録:

| シークレット名 | 値 |
|---|---|
| `RIOT_API_KEY` | `RGAPI-xxxx...` |
| `GAME_NAME` | Riot ID のゲーム名 |
| `TAG_LINE` | タグライン（例: `JP1`） |
| `DISCORD_WEBHOOK` | Discord Webhook URL |

### 4. GitHub Actions を有効化

リポジトリの **Actions** タブを開き、ワークフローを有効化してください。

> **注意**: プライベートリポジトリの場合、GitHub Actions の無料枠は月 2,000 分（約 3.3 時間）です。  
> 5 分おきに 1 回 × 約 30 秒 = 月 4,320 回 × 0.5 分 ≈ 2,160 分 となるため、無料枠をわずかに超える場合があります。  
> 最低限の実行時間に抑えるか、**パブリックリポジトリ**にすると無料枠無制限です。

### ローカルで試す

```bash
pip install -r requirements.txt

# .env.example をコピーして値を設定
copy .env.example .env
# .env を編集して各値を入力

# 実行（PowerShell の場合）
$env:RIOT_API_KEY="RGAPI-xxxx"
$env:GAME_NAME="あなたのゲーム名"
$env:TAG_LINE="JP1"
$env:DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
python tracker.py
```

## ファイル構成

```
lol-discord-tracker/
├── tracker.py            # メインスクリプト
├── requirements.txt      # Python 依存パッケージ
├── last_match_id.txt     # 最後に確認したマッチ ID（自動生成）
├── .env.example          # 環境変数テンプレート
└── .github/
    └── workflows/
        └── tracker.yml   # GitHub Actions ワークフロー
```
