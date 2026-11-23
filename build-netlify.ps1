$ErrorActionPreference = 'Stop'
& node (Join-Path $PSScriptRoot 'build-netlify.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Netlify paketi hazırlanamadı.' }
