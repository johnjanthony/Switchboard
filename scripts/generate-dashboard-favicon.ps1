param(
    [string]$SourcePath = "android/app/src/main/ic_launcher-playstore.png",
    [string]$OutputDir = "dashboard"
)

Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $SourcePath)) {
    Write-Error "Source image not found: $SourcePath"
    exit 1
}

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$src = [System.Drawing.Bitmap]::FromFile($SourcePath)

# 1. Copy/save 512x512 PNG as favicon.png
$src.Save("$OutputDir/favicon.png", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Created $OutputDir/favicon.png (512x512)"

# Helper function to resize bitmap using high-quality bicubic interpolation
function Resize-Bitmap ($source, $width, $height) {
    $dest = New-Object System.Drawing.Bitmap($width, $height)
    $g = [System.Drawing.Graphics]::FromImage($dest)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $g.DrawImage($source, 0, 0, $width, $height)
    $g.Dispose()
    return $dest
}

# 2. Save favicon-32x32.png
$bmp32 = Resize-Bitmap $src 32 32
$bmp32.Save("$OutputDir/favicon-32x32.png", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Created $OutputDir/favicon-32x32.png (32x32)"

# 3. Save favicon-16x16.png
$bmp16 = Resize-Bitmap $src 16 16
$bmp16.Save("$OutputDir/favicon-16x16.png", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Created $OutputDir/favicon-16x16.png (16x16)"

# 4. Save apple-touch-icon.png (180x180)
$bmp180 = Resize-Bitmap $src 180 180
$bmp180.Save("$OutputDir/apple-touch-icon.png", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Created $OutputDir/apple-touch-icon.png (180x180)"

# 5. Create multi-resolution ICO file (containing 16x16, 32x32, 48x48 PNG frames)
$icoPath = "$OutputDir/favicon.ico"
$fs = [System.IO.File]::Create($icoPath)
$bw = New-Object System.IO.BinaryWriter($fs)

$sizes = @(16, 32, 48)
$pngBytesList = @()

foreach ($sz in $sizes) {
    $b = Resize-Bitmap $src $sz $sz
    $ms = New-Object System.IO.MemoryStream
    $b.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngBytesList += ,$ms.ToArray()
    $ms.Dispose()
    $b.Dispose()
}

$bw.Write([UInt16]0) # Reserved
$bw.Write([UInt16]1) # Type = ICO
$bw.Write([UInt16]$sizes.Count) # Count

$offset = 6 + (16 * $sizes.Count)

for ($i = 0; $i -lt $sizes.Count; $i++) {
    $sz = $sizes[$i]
    $bytes = $pngBytesList[$i]
    $bw.Write([byte]$sz) # Width
    $bw.Write([byte]$sz) # Height
    $bw.Write([byte]0)   # Color count
    $bw.Write([byte]0)   # Reserved
    $bw.Write([UInt16]1) # Color planes
    $bw.Write([UInt16]32)# Bits per pixel
    $bw.Write([UInt32]$bytes.Length) # Image size
    $bw.Write([UInt32]$offset)       # Offset
    $offset += $bytes.Length
}

foreach ($bytes in $pngBytesList) {
    $bw.Write($bytes)
}

$bw.Close()
$fs.Close()

Write-Host "Created $OutputDir/favicon.ico ($($sizes.Count) sizes)"

$bmp16.Dispose()
$bmp32.Dispose()
$bmp180.Dispose()
$src.Dispose()
