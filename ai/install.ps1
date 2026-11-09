# Connects Claude (Claude Code and the Claude app) to Laneh Team on Windows.
#   irm https://raw.githubusercontent.com/iamTheSilent/laneh-releases/main/ai/install.ps1 | iex
# Needs Python 3.10+; offers to install it with winget when it's missing.
$ErrorActionPreference = 'Stop'
$base = 'https://raw.githubusercontent.com/iamTheSilent/laneh-releases/main/ai'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

function Get-Python {
  foreach ($c in 'py', 'python') {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { continue }
    $a = if ($c -eq 'py') { @('-3') } else { @() }
    try {
      $exe = & $c @a -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
      if ($exe) { return $exe.Trim() }
    } catch {}
  }
  $own = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
  if ($own) { return $own.FullName }
}

$py = Get-Python
if (-not $py) {
  Write-Host 'Python 3.10+ is needed. Installing Python 3.12 for this user (winget)...'
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  $py = Get-Python
  if (-not $py) { throw 'Python not found. Install it from https://www.python.org/downloads/ and run this again.' }
}
Write-Host "Python: $py"

Write-Host 'Installing the MCP library...'
& $py -m pip install --user --upgrade --quiet --disable-pip-version-check 'mcp>=1.2'
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

$dir = Join-Path $HOME '.laneh'
New-Item -ItemType Directory -Force $dir | Out-Null
$file = Join-Path $dir 'laneh_mcp.py'
Invoke-WebRequest -UseBasicParsing "$base/laneh_mcp.py" -OutFile $file
& $py $file --install
