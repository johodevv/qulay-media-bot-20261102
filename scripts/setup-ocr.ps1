$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskTools = Join-Path $taskRoot 'tools'
$taskDownloads = Join-Path $taskTools 'downloads'
$taskExtractor = Join-Path $taskTools '7zip'
$taskOcr = Join-Path $taskTools 'tesseract'
New-Item -ItemType Directory -Path $taskDownloads -Force | Out-Null

Write-Output 'Tesseract va 7-Zip distributivlari hash bilan tekshirib olinadi...'
if (-not (Get-ChildItem -LiteralPath $taskDownloads -Filter 'Tesseract*.exe')) {
    winget download --id UB-Mannheim.TesseractOCR --exact --download-directory $taskDownloads --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw 'Tesseract yuklanmadi.' }
}
if (-not (Get-ChildItem -LiteralPath $taskDownloads -Filter '7-Zip*.msi')) {
    winget download --id 7zip.7zip --exact --download-directory $taskDownloads --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw '7-Zip yuklanmadi.' }
}
$taskMsi = Get-ChildItem -LiteralPath $taskDownloads -Filter '7-Zip*.msi' | Select-Object -First 1
if (-not $taskMsi) { throw '7-Zip MSI distributivi topilmadi.' }

# /a faqat distributiv fayllarini ochadi; tizimga dasturni o'rnatmaydi.
Write-Output '7-Zip loyiha ichiga ochilyapti...'
$taskMsiProcess = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/a', ('"{0}"' -f $taskMsi.FullName), '/qn', ('TARGETDIR="{0}"' -f $taskExtractor)) -WindowStyle Hidden -Wait -PassThru
if ($taskMsiProcess.ExitCode -notin @(0, 3010)) {
    throw "7-Zip fayllari ochilmadi: $($taskMsiProcess.ExitCode)"
}
$taskSevenZip = Get-ChildItem -LiteralPath $taskExtractor -Filter '7z.exe' -Recurse | Select-Object -First 1
if (-not $taskSevenZip) { throw '7z.exe topilmadi.' }
$taskInstaller = Get-ChildItem -LiteralPath $taskDownloads -Filter 'Tesseract*.exe' | Select-Object -First 1
& $taskSevenZip.FullName x $taskInstaller.FullName "-o$taskOcr" -y -bso0
if ($LASTEXITCODE -ne 0) { throw 'Tesseract fayllari ochilmadi.' }

$taskTessdata = Join-Path $taskOcr 'tessdata'
New-Item -ItemType Directory -Path $taskTessdata -Force | Out-Null
foreach ($taskLanguage in @('uzb', 'uzb_cyrl', 'rus', 'eng')) {
    Write-Output "Til modeli: $taskLanguage"
    $taskModel = Join-Path $taskTessdata "$taskLanguage.traineddata"
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$taskLanguage.traineddata" -OutFile $taskModel -TimeoutSec 120
}
& (Join-Path $taskOcr 'tesseract.exe') --list-langs
if ($LASTEXITCODE -ne 0) { throw 'Tesseract ishga tushmadi.' }
Write-Output 'OCR tayyor. Botni qayta ishga tushiring.'
