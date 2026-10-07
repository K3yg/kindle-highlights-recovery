<#
Talk to a Kindle connected over USB (MTP) on Windows. Output is JSON.

  -Action status                      Is a Kindle connected and readable?
  -Action pull-db   -Dest <dir>       Copy ksdk_annotation_v*.db (+ -wal/-shm) into <dir>.
  -Action list-books                  List book files and .sdr folders under documents/.
  -Action pull-file -Path <rel> -Dest <dir>
                                      Copy one file (path relative to documents/) into <dir>.

The Kindle must be plugged in, switched on and UNLOCKED: MTP hides the
storage while the lock screen is showing.
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('status', 'pull-db', 'list-books', 'pull-file')]
  [string]$Action,
  [string]$Dest,
  [string]$Path
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Out-Json($obj) { $obj | ConvertTo-Json -Depth 5 -Compress }

function Fail($msg) { Out-Json @{ ok = $false; error = $msg }; exit 1 }

function Get-Storage {
  $shell = New-Object -ComObject Shell.Application
  $device = $shell.Namespace(0x11).Items() |
    Where-Object { $_.Name -match 'Kindle' } | Select-Object -First 1
  if (-not $device) { return $null }
  $root = $device.GetFolder
  if (-not $root) { return $null }
  # Usually the device shows one "Internal Storage" folder holding documents/
  # and system/; some expose documents/ at the top level directly.
  $top = @($root.Items() | Where-Object { $_.IsFolder })
  if ($top | Where-Object { $_.Name -eq 'documents' }) { return $root }
  foreach ($f in $top) {
    $sub = $f.GetFolder
    if ($sub -and ($sub.Items() | Where-Object { $_.IsFolder -and $_.Name -eq 'documents' })) { return $sub }
  }
  return $null
}

function Get-Child($folder, $name) {
  # Folders only, exact name first: a file such as
  # FILE_SYSTEM_ACCESSIBILITY_FLAG must never be mistaken for "system".
  $folders = @($folder.Items() | Where-Object { $_.IsFolder })
  $hit = $folders | Where-Object { $_.Name -eq $name } | Select-Object -First 1
  if (-not $hit) { $hit = $folders | Where-Object { $_.Name -like "$name*" } | Select-Object -First 1 }
  if ($hit) { return $hit.GetFolder }
  return $null
}

function Copy-Item-Mtp($item, $destDir) {
  # Copy into a fresh staging folder, then move: waiting for "a new file" in a
  # folder that already holds one with the same name would never finish.
  $stage = Join-Path ([IO.Path]::GetTempPath()) ("khr-" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $stage | Out-Null
  try {
    $expected = 0
    try { $expected = [int64]$item.ExtendedProperty('Size') } catch {}
    (New-Object -ComObject Shell.Application).Namespace($stage).CopyHere($item, 16 + 4 + 1024)
    $file = $null
    for ($i = 0; $i -lt 600; $i++) {
      Start-Sleep -Milliseconds 250
      $file = Get-ChildItem $stage -File -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($file -and ($expected -le 0 -or $file.Length -ge $expected)) {
        Start-Sleep -Milliseconds 250   # let the shell release the handle
        break
      }
      $file = $null
    }
    if (-not $file) { throw "Timed out copying $($item.Name)" }
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    $target = Join-Path $destDir $file.Name
    Move-Item -LiteralPath $file.FullName -Destination $target -Force
    return Get-Item -LiteralPath $target
  } finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Walk($folder, $prefix, $depth, $acc) {
  foreach ($it in $folder.Items()) {
    $rel = if ($prefix) { "$prefix/$($it.Name)" } else { $it.Name }
    if ($it.IsFolder) {
      if ($it.Name -like '*.sdr') {
        $acc.Add(@{ path = $rel; folder = $true; type = 'sdr' }) | Out-Null
      } elseif ($depth -lt 3 -and $it.Name -notmatch '^(dictionaries|\.cache)$') {
        Walk $it.GetFolder $rel ($depth + 1) $acc
      }
    } else {
      $size = 0
      try { $size = [int64]$it.ExtendedProperty('Size') } catch {}
      $acc.Add(@{ path = $rel; folder = $false; type = [string]$it.Type; size = $size }) | Out-Null
    }
  }
}

$storage = Get-Storage
if (-not $storage) { Fail 'No Kindle found. Plug it in, switch it on and unlock it.' }

switch ($Action) {
  'status' {
    $docs = Get-Child $storage 'documents'
    Out-Json @{ ok = [bool]$docs; error = $(if ($docs) { $null } else { 'Kindle found but storage is not readable. Unlock it.' }) }
  }
  'pull-db' {
    if (-not $Dest) { Fail '-Dest is required' }
    $folder = $storage
    foreach ($name in 'system', 'ksdk', '.annotations') {
      $folder = Get-Child $folder $name
      if (-not $folder) { Fail "Annotation folder not found (missing '$name'). Is the Kindle unlocked?" }
    }
    $account = @($folder.Items() | Where-Object { $_.IsFolder -and $_.Name -like 'amzn1.account*' })
    if (-not $account) { Fail 'No amzn1.account.* folder under system/ksdk/.annotations' }
    $copied = @()
    foreach ($acc in $account) {
      foreach ($it in $acc.GetFolder.Items()) {
        if (-not $it.IsFolder -and $it.Name -like 'ksdk_annotation*') {
          $f = Copy-Item-Mtp $it $Dest
          $copied += @{ name = $f.Name; size = $f.Length; account = $acc.Name }
        }
      }
    }
    if (-not $copied) { Fail 'No ksdk_annotation database found on the Kindle.' }
    Out-Json @{ ok = $true; files = $copied }
  }
  'list-books' {
    $docs = Get-Child $storage 'documents'
    if (-not $docs) { Fail 'documents folder not found. Is the Kindle unlocked?' }
    $acc = New-Object System.Collections.ArrayList
    Walk $docs '' 0 $acc
    Out-Json @{ ok = $true; items = @($acc) }
  }
  'pull-file' {
    if (-not $Dest -or -not $Path) { Fail '-Path and -Dest are required' }
    $folder = Get-Child $storage 'documents'
    $parts = $Path -split '/'
    for ($i = 0; $i -lt $parts.Count - 1; $i++) {
      $folder = Get-Child $folder $parts[$i]
      if (-not $folder) { Fail "Folder not found: $($parts[$i])" }
    }
    $leaf = $parts[-1]
    $item = $folder.Items() | Where-Object { -not $_.IsFolder -and $_.Name -eq $leaf } | Select-Object -First 1
    if (-not $item) { Fail "File not found on the Kindle: $Path" }
    $f = Copy-Item-Mtp $item $Dest
    Out-Json @{ ok = $true; name = $f.Name; size = $f.Length; path = $f.FullName }
  }
}
