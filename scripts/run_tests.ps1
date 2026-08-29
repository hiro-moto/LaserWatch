$ErrorActionPreference = "Stop"

$tests = @(
    "test_icon_static.py",
    "test_uvc_math.py",
    "test_analysis.py",
    "test_v04.py",
    "test_v05.py",
    "test_v06.py",
    "test_v07.py",
    "test_v08.py",
    "test_v081_layout.py",
    "test_v082.py",
    "test_v082_static.py",
    "test_v083.py",
    "test_v084.py",
    "test_v086.py"
)

foreach ($test in $tests) {
    Write-Host "==> $test"
    uv run python $test
    if ($LASTEXITCODE -ne 0) {
        throw "Test failed: $test"
    }
}

Write-Host "All LaserWatch regression tests passed."
