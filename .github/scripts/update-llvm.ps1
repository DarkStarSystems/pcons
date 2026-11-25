# SPDX-License-Identifier: MIT
# Install LLVM from its GitHub release over the Windows runner's older one,
# which MSVC 14.52's STL refuses (it needs Clang 22 or newer). The version
# matches the flang job's. Fails unless clang then reports that version, so a
# failed install shows here, not as a compiler error in some later step.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # The progress bar slows downloads.

$version = '23.1.3'
$sha256 = '5153f848ac87553118340f16f8d3cd4351d729bcba6e4785c6a4a9af29676de9'

$url = "https://github.com/llvm/llvm-project/releases/download/llvmorg-$version/LLVM-$version-win64.msi"
$msi = Join-Path $env:RUNNER_TEMP "LLVM-$version-win64.msi"
Invoke-WebRequest $url -OutFile $msi -MaximumRetryCount 5 -RetryIntervalSec 15
if ((Get-FileHash $msi -Algorithm SHA256).Hash -ne $sha256) {
    throw "$url doesn't match its SHA-256"
}

$install = Start-Process msiexec.exe -Wait -PassThru `
    -ArgumentList '/i', "`"$msi`"", '/qn', '/norestart'
if ($install.ExitCode -notin 0, 3010) {
    throw "msiexec exited with $($install.ExitCode)"
}

$clang = & "$env:ProgramFiles\LLVM\bin\clang.exe" --version | Select-Object -First 1
if ($clang -notmatch "clang version $([regex]::Escape($version))\b") {
    throw "LLVM $version didn't install; clang says: $clang"
}
Write-Output $clang
