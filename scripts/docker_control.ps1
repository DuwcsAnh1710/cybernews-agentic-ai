# CyberPulse AI - Docker Management Helper Script (PowerShell)
# Usage: .\scripts\docker_control.ps1 [up | down | run-pipeline | logs | status]

param (
    [Parameter(Position=0)]
    [ValidateSet("up", "down", "run-pipeline", "logs", "status", "build", "export-portal")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"

Write-Host "[CYBERPULSE INTEL] Docker Management Controller: Action='$Action'" -ForegroundColor Cyan

switch ($Action) {
    "up" {
        Write-Host "[*] Khoi chay he thong CyberPulse AI Containers (UI: 8501, Portal: 8080)..."
        docker compose up -d
        Write-Host "[+] Containers da duoc khoi chay thanh cong!" -ForegroundColor Green
        Write-Host "  * Editorial Studio : http://localhost:8501"
        Write-Host "  * Reader Portal    : http://localhost:8080"
    }
    "down" {
        Write-Host "[*] Dung toan bo he thong CyberPulse AI Containers..."
        docker compose down
        Write-Host "[+] He thong da dung thanh cong." -ForegroundColor Yellow
    }
    "build" {
        Write-Host "[*] Bien dich lai Docker image cho CyberPulse AI..."
        docker compose build --no-cache
        Write-Host "[+] Bien dich hoan tat!" -ForegroundColor Green
    }
    "run-pipeline" {
        Write-Host "[*] Kich hoat Automated Intelligence Pipeline trong Docker Container..."
        docker compose run --rm cyberpulse-runner
    }
    "export-portal" {
        Write-Host "[*] Bien dich Reader Portal tinh vao dist..."
        docker compose run --rm cyberpulse-ui cyberpulse export portal --output-dir output --dist-dir dist
    }
    "logs" {
        docker compose logs -f --tail=100
    }
    "status" {
        docker compose ps
    }
}
