# tools/patch-repo-meta.ps1 -- 设置仓库简介（description）与关键词（topics）
$ErrorActionPreference = 'Stop'
$env:GCM_INTERACTIVE = 'never'
$user = 'Ayanami-lppl'
$repo = 'arbor'

$out = "protocol=https`nhost=github.com`n`n" | git credential fill
$token = ''
foreach ($line in $out) {
    if ($line -match '^password=(.*)$') { $token = $matches[1] }
}
if (-not $token) { throw 'no github token in credential store' }

$desc = '只读的 Windows 磁盘空间分析工具：目录树 + 长条占比图，看得见隐藏文件，识别卸载残留，删前先隔离'
$topics = @(
    'disk-space', 'disk-usage', 'disk-analyzer', 'windows', 'python',
    'tkinter', 'hidden-files', 'cleanup', 'storage', 'desktop-app'
)
$headers = @{
    Authorization = "token $token"
    Accept = 'application/vnd.github+json'
    'User-Agent' = 'arbor-publisher'
}
$body = @{ description = $desc; topics = $topics } | ConvertTo-Json -Depth 3
$r = Invoke-RestMethod -Method Patch `
    -Uri "https://api.github.com/repos/$user/$repo" `
    -Headers $headers -Body $body -ContentType 'application/json' -TimeoutSec 60
"description: $($r.description)"
"topics: $($r.topics -join ', ')"

if (-not $r.topics -or $r.topics.Count -eq 0) {
    $headers.Accept = 'application/vnd.github.mercy-preview+json'
    $r = Invoke-RestMethod -Method Patch `
        -Uri "https://api.github.com/repos/$user/$repo" `
        -Headers $headers -Body $body -ContentType 'application/json' -TimeoutSec 60
    "topics(retry): $($r.topics -join ', ')"
}
