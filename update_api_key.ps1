# ============================================================
# Riot API キー更新スクリプト
# ダブルクリックではなく PowerShell から実行してください
#   powershell -ExecutionPolicy Bypass -File update_api_key.ps1
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Riot API キー 更新ツール" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: ブラウザを開く ────────────────────────────────
Write-Host "[1/3] Riot Developer Portal を開いています..." -ForegroundColor Yellow
Start-Process "https://developer.riotgames.com"
Write-Host ""
Write-Host "  ① ログイン後、ページ中央の「REGENERATE API KEY」をクリック" -ForegroundColor White
Write-Host "  ② 表示された「RGAPI-xxxx-...」をコピー" -ForegroundColor White
Write-Host ""

# ── Step 2: キーを入力 ────────────────────────────────────
Write-Host "[2/3] 新しい API キーを貼り付けてください" -ForegroundColor Yellow
$apiKey = Read-Host "  > API Key"
$apiKey = $apiKey.Trim()

if (-not $apiKey.StartsWith("RGAPI-")) {
    Write-Host ""
    Write-Host "❌ キーが「RGAPI-」で始まっていません。コピーし直してください。" -ForegroundColor Red
    Read-Host "  Enter キーで終了"
    exit 1
}

# ── Step 3: GitHub Secret を更新 ──────────────────────────
Write-Host ""
Write-Host "[3/3] GitHub Secret を更新しています..." -ForegroundColor Yellow

# gh CLI の存在確認
$ghExists = Get-Command gh -ErrorAction SilentlyContinue

if ($ghExists) {
    # リポジトリを git remote から自動検出
    try {
        $remoteUrl = git remote get-url origin 2>$null
        # https://github.com/owner/repo.git  または  git@github.com:owner/repo.git
        if ($remoteUrl -match "github\.com[:/](.+?)(?:\.git)?$") {
            $repo = $Matches[1]
        } else {
            $repo = $null
        }
    } catch {
        $repo = $null
    }

    if ($repo) {
        gh secret set RIOT_API_KEY --body $apiKey --repo $repo
        Write-Host ""
        Write-Host "✅ GitHub Secret「RIOT_API_KEY」を更新しました！" -ForegroundColor Green
        Write-Host "   リポジトリ: $repo" -ForegroundColor Gray
    } else {
        # リポジトリを手動入力
        Write-Host "  リポジトリが自動検出できませんでした。" -ForegroundColor Yellow
        $repo = Read-Host "  GitHub リポジトリ名を入力してください（例: yourname/lol-discord-tracker）"
        gh secret set RIOT_API_KEY --body $apiKey --repo $repo.Trim()
        Write-Host ""
        Write-Host "✅ GitHub Secret「RIOT_API_KEY」を更新しました！" -ForegroundColor Green
    }
} else {
    # gh CLI がない場合：ブラウザで直接開く
    Write-Host ""
    Write-Host "  GitHub CLI (gh) が見つかりません。ブラウザで手動更新します..." -ForegroundColor Yellow

    # リポジトリを git remote から取得してブラウザで開く
    try {
        $remoteUrl = git remote get-url origin 2>$null
        if ($remoteUrl -match "github\.com[:/](.+?)(?:\.git)?$") {
            $repo = $Matches[1]
            $secretsUrl = "https://github.com/$repo/settings/secrets/actions"
            Start-Process $secretsUrl
            Write-Host ""
            Write-Host "  ブラウザで GitHub Secrets ページを開きました。" -ForegroundColor White
        }
    } catch {}

    Write-Host ""
    Write-Host "  手順:" -ForegroundColor White
    Write-Host "    1. GitHub リポジトリ → Settings → Secrets and variables → Actions" -ForegroundColor White
    Write-Host "    2. 「RIOT_API_KEY」の右の鉛筆アイコンをクリック" -ForegroundColor White
    Write-Host "    3. 下記のキーを貼り付けて「Update secret」をクリック" -ForegroundColor White
    Write-Host ""
    Write-Host "  新しいキー:" -ForegroundColor Yellow
    Write-Host "  $apiKey" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  ─── gh CLI の導入方法（次回から自動化できます）─────────────────" -ForegroundColor DarkGray
    Write-Host "  winget install --id GitHub.cli" -ForegroundColor DarkGray
    Write-Host "  gh auth login" -ForegroundColor DarkGray
    Write-Host "  ──────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
}

Write-Host ""
Read-Host "  Enter キーで終了"
