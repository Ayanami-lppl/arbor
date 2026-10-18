# tools/publish-github.ps1 -- 创建私有仓库并推送（凭据取自 Windows 凭据管理器）
$ErrorActionPreference = 'Stop'
$env:GCM_INTERACTIVE = 'never'
$repo = 'arbor'
$user = 'Ayanami-lppl'

$out = "protocol=https`nhost=github.com`n`n" | git credential fill
$token = ''
$username = ''
foreach ($line in $out) {
    if ($line -match '^username=(.*)$') { $username = $matches[1] }
    if ($line -match '^password=(.*)$') { $token = $matches[1] }
}
if (-not $token) { throw 'credential store has no github token' }
"cred-user=$username token-len=$($token.Length)"

$headers = @{
    Authorization = "token $token"
    Accept = 'application/vnd.github+json'
    'User-Agent' = 'arbor-publisher'
}
$api = "https://api.github.com/repos/$user/$repo"
$exists = $false
try {
    $r = Invoke-RestMethod -Method Get -Uri $api -Headers $headers -TimeoutSec 30
    $exists = $true
    "repo-exists: $($r.full_name) private=$($r.private)"
} catch {
    "repo-missing: $($_.Exception.Message)"
}

if (-not $exists) {
    $body = @{
        name = $repo
        description = 'Arbor - read-only disk space analyzer (Windows / Python + Tkinter)'
        private = $true
    } | ConvertTo-Json
    $r = Invoke-RestMethod -Method Post -Uri 'https://api.github.com/user/repos' `
        -Headers $headers -Body $body -ContentType 'application/json' -TimeoutSec 60
    "repo-created: $($r.full_name) private=$($r.private)"
}

git remote remove origin 2>$null
git remote add origin "https://github.com/$user/$repo.git"
git push -u origin main 2>&1 | Select-Object -Last 6
"push-exit=$LASTEXITCODE"
