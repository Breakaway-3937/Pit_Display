<#
.SYNOPSIS
    Install the Breakaway Pit Display on a Windows pit machine, in the layout
    that lets it update itself afterwards.

.DESCRIPTION
    Builds the versioned layout `app/update/install.py` expects:

        %LOCALAPPDATA%\Programs\Breakaway Pit Display\
            pointer.json
            current  ─▶ versions\1.4.2      (a directory junction)
            versions\1.4.2\Breakaway Pit Display.exe

    and points the Start Menu — and, with -Startup, `shell:startup` — at
    `current\`, so every future update is a link being repointed rather than a
    person copying a folder.

    **No administrator, ever.** Per-user install directory, and a *junction*
    rather than a symlink, because junctions need no privilege while symlinks
    need Developer Mode. Nothing here touches the data directory
    (`%LOCALAPPDATA%\Breakaway Pit Display\`), so re-running this over an
    existing machine keeps the database, checklists, CAN-id names, imported
    logs and the uploaded CAD model.

    The repository is private, so the release asset cannot be downloaded from a
    browser without signing in. Give this a token instead and it fetches the
    release itself — the same token it then leaves on the machine for the app.

.PARAMETER Token
    A GitHub fine-grained PAT with Contents: read on Breakaway-3937/Pit_Display
    and nothing else. Saved to the data directory so the app can update itself.

.PARAMETER Zip
    Install from a zip already on this machine instead of downloading one.

.PARAMETER Channel
    stable (default) or beta. Also written to the machine's update settings.

.PARAMETER Startup
    Also put a shortcut in shell:startup, so the pit machine boots into the
    display.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Token github_pat_xxx -Startup

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Zip .\Breakaway-Pit-Display-1.4.2-windows.zip
#>

[CmdletBinding()]
param(
    [string]$Token = "",
    [string]$Zip = "",
    [ValidateSet("stable", "beta")][string]$Channel = "stable",
    [string]$InstallRoot = "",
    [switch]$Startup,
    [switch]$SkipSelfCheck
)

$ErrorActionPreference = "Stop"
$AppName  = "Breakaway Pit Display"
$Owner    = "Breakaway-3937"
$Repo     = "Pit_Display"

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\$AppName" }
$DataRoot   = Join-Path $env:LOCALAPPDATA $AppName
$Versions   = Join-Path $InstallRoot "versions"
$Current    = Join-Path $InstallRoot "current"
$PointerFile= Join-Path $InstallRoot "pointer.json"

function Say($m)  { Write-Host "  $m" }
function Step($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "  x $m" -ForegroundColor Red; exit 1 }

Step "Breakaway Pit Display — install"
Say "install  $InstallRoot"
Say "data     $DataRoot  (never touched by an install or an update)"

# ── Get the build ────────────────────────────────────────────────────────────

$Version = ""
if ($Zip) {
    if (-not (Test-Path $Zip)) { Die "$Zip does not exist" }
    $Zip = (Resolve-Path $Zip).Path
    if ((Split-Path $Zip -Leaf) -match '-(\d+\.\d+[^-]*(?:-[A-Za-z0-9.]+)?)-windows\.zip$') {
        $Version = $Matches[1]
    } else {
        Die "Cannot read a version out of $(Split-Path $Zip -Leaf). Rename it to Breakaway-Pit-Display-<version>-windows.zip"
    }
    Step "Using $(Split-Path $Zip -Leaf)  (version $Version)"
} else {
    if (-not $Token) { Die "Give -Token (to download) or -Zip (to install a local file)." }

    Step "Finding the latest $Channel release"
    $headers = @{
        Authorization          = "Bearer $Token"
        Accept                 = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent"           = "breakaway-pit-display-installer"
    }
    try {
        $releases = Invoke-RestMethod -Headers $headers -Method Get `
            -Uri "https://api.github.com/repos/$Owner/$Repo/releases?per_page=30"
    } catch {
        Die "GitHub refused the token, or this machine has no internet: $($_.Exception.Message)"
    }

    $release = $releases | Where-Object {
        -not $_.draft -and ($Channel -eq "beta" -or -not $_.prerelease)
    } | Select-Object -First 1
    if (-not $release) { Die "No $Channel release exists yet." }

    $manifestAsset = $release.assets | Where-Object { $_.name -eq "manifest.json" }
    if (-not $manifestAsset) { Die "$($release.tag_name) has no manifest.json — it was not built by CI." }

    $assetHeaders = $headers.Clone()
    $assetHeaders.Accept = "application/octet-stream"
    $manifest = Invoke-RestMethod -Headers $assetHeaders -Method Get `
        -Uri "https://api.github.com/repos/$Owner/$Repo/releases/assets/$($manifestAsset.id)"

    $windows = $manifest.platforms.windows
    if (-not $windows) { Die "$($release.tag_name) has no Windows build." }
    $Version = $manifest.version
    $asset = $release.assets | Where-Object { $_.name -eq $windows.asset }
    if (-not $asset) { Die "manifest.json names $($windows.asset), which is not on the release." }

    Say "$($release.tag_name) — $Version, $([math]::Round($windows.size / 1MB)) MB"

    $Zip = Join-Path $env:TEMP $windows.asset
    Step "Downloading"
    # The asset endpoint 302s to signed object storage, which rejects a request
    # that *also* carries our bearer token. Follow the redirect by hand so the
    # second request goes out clean.
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.DefaultRequestHeaders.Add("Authorization", "Bearer $Token")
    $client.DefaultRequestHeaders.Add("Accept", "application/octet-stream")
    $client.DefaultRequestHeaders.Add("User-Agent", "breakaway-pit-display-installer")
    $response = $client.GetAsync("https://api.github.com/repos/$Owner/$Repo/releases/assets/$($asset.id)").Result
    $url = if ($response.Headers.Location) { $response.Headers.Location.AbsoluteUri } else { $null }
    $client.Dispose()
    if ($url) {
        Invoke-WebRequest -Uri $url -OutFile $Zip -UseBasicParsing
    } else {
        Die "GitHub did not hand over the asset (HTTP $([int]$response.StatusCode))."
    }

    $actual = (Get-FileHash -Path $Zip -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $windows.sha256.ToLower()) {
        Remove-Item $Zip -Force
        Die "The download does not match its checksum. Try again."
    }
    Say "checksum ok"
}

# ── Unpack into versions\<version> ───────────────────────────────────────────

Step "Unpacking $Version"
$target  = Join-Path $Versions $Version
$staging = "$target.staging"
foreach ($d in @($staging, $target)) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}
New-Item -ItemType Directory -Path $staging -Force | Out-Null
Expand-Archive -Path $Zip -DestinationPath $staging -Force

# build_app.py makes the app folder the zip's root, so lift it out rather than
# ending up with ...\versions\1.4.2\Breakaway Pit Display\the exe.
$inner = Get-ChildItem -Path $staging -Directory
if ($inner.Count -eq 1 -and -not (Test-Path (Join-Path $staging "$AppName.exe"))) {
    Move-Item -Path $inner[0].FullName -Destination $target
    Remove-Item $staging -Recurse -Force
} else {
    Move-Item -Path $staging -Destination $target
}

$exe = Join-Path $target "$AppName.exe"
if (-not (Test-Path $exe)) { Die "No $AppName.exe in the unpacked build." }
Say "$target"

# ── The link ─────────────────────────────────────────────────────────────────

Step "Pointing current at $Version"
if (Test-Path $Current) {
    $item = Get-Item $Current -Force
    if ($item.LinkType) {
        # rmdir removes the reparse point only. Remove-Item -Recurse on a
        # junction walks into the target and deletes the version behind it.
        cmd /c rmdir "$Current" | Out-Null
    } else {
        Die "$Current is a real folder. Move it aside and re-run: this install predates the versioned layout."
    }
}
$link = cmd /c mklink /J "$Current" "$target" 2>&1
if (-not (Test-Path $Current)) { Die "Could not create the junction: $link" }
Say "current -> versions\$Version"

$previous = ""
if (Test-Path $PointerFile) {
    try { $previous = (Get-Content $PointerFile -Raw | ConvertFrom-Json).current } catch { }
}
@{
    current  = $Version
    previous = $previous
    updated  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
} | ConvertTo-Json | Set-Content -Path $PointerFile -Encoding UTF8

# ── Update settings and the token ────────────────────────────────────────────

Step "Update settings"
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
$settingsFile = Join-Path $DataRoot "update.json"
$settings = @{ channel = $Channel; auto_check = $true; check_interval_hours = 6 }
if (Test-Path $settingsFile) {
    try {
        $existing = Get-Content $settingsFile -Raw | ConvertFrom-Json
        foreach ($p in $existing.PSObject.Properties) { $settings[$p.Name] = $p.Value }
        $settings.channel = $Channel
    } catch { }
}
$settings | ConvertTo-Json | Set-Content -Path $settingsFile -Encoding UTF8
Say "channel $Channel, checking every 6 hours"

if ($Token) {
    # No trailing newline games: the app strips whitespace when it reads this.
    Set-Content -Path (Join-Path $DataRoot "update_token") -Value $Token -Encoding ASCII -NoNewline
    Say "update token saved to $DataRoot\update_token"
} elseif (-not (Test-Path (Join-Path $DataRoot "update_token"))) {
    Say "! no update token on this machine — it will not find future releases."
    Say "  Add one from Control -> Pit Systems -> Software Updates, or re-run with -Token."
}

# ── Shortcuts ────────────────────────────────────────────────────────────────

Step "Shortcuts"
$shell = New-Object -ComObject WScript.Shell
function Make-Shortcut($path) {
    $sc = $shell.CreateShortcut($path)
    # Through `current`, never through a version folder — that is what makes
    # tomorrow's update the thing this shortcut opens.
    $sc.TargetPath       = (Join-Path $Current "$AppName.exe")
    $sc.WorkingDirectory = $Current
    $sc.Description      = "Breakaway 3937 pit display"
    $sc.Save()
    Say (Split-Path $path -Leaf)
}
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
Make-Shortcut (Join-Path $startMenu "$AppName.lnk")
if ($Startup) {
    Make-Shortcut (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\$AppName.lnk")
}

# ── Prove it works ───────────────────────────────────────────────────────────

if (-not $SkipSelfCheck) {
    Step "Self-check"
    & (Join-Path $Current "$AppName.exe") --self-check
    $status = $LASTEXITCODE
    if ($status -ne 0) {
        Write-Host "`nThe install did not pass its self-check (exit $status)." -ForegroundColor Red
        Write-Host "Read the [FAIL] lines above; DEPLOYMENT.md explains each one." -ForegroundColor Red
        exit $status
    }
}

Step "Done"
Say "Version $Version is installed and running clean."
Say "Point it at the music folder: Control -> Music -> Add folder."
Say "Future updates arrive by themselves — Control -> Pit Systems -> Software Updates."
