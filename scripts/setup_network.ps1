# setup_network.ps1
# MUST BE RUN AS ADMINISTRATOR

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "   MESH NETWORK SETUP UTILITY v2.0"       -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "1. Standard Setup (Port Proxy)"            -ForegroundColor Green
Write-Host "   - Works on all Windows 10/11 versions"
Write-Host "   - Forwards specific ports manually"
Write-Host ""
Write-Host "2. Mirrored Mode (Recommended for Win11 22H2+)" -ForegroundColor Green
Write-Host "   - WSL shares your PC's IP address (No forwarding needed!)"
Write-Host "   - Simplifies everything."
Write-Host "   - Requires 'wsl --shutdown' to take effect."
Write-Host "=========================================" -ForegroundColor Cyan

$choice = Read-Host "Select Option (1 or 2)"

if ($choice -eq "2") {
    Write-Host "`n--- Configuring Mirrored Mode ---" -ForegroundColor Yellow
    
    # We are running as Admin (possibly a different user), so we need to know WHERE to put the file.
    # We'll try to guess based on the 'dclark' in the path, or ask.
    $target_user = Read-Host "Enter your Windows Username (the account performing the WSL work)"
    $target_path = "C:\Users\$target_user\.wslconfig"
    
    if (-not (Test-Path "C:\Users\$target_user")) {
        Write-Error "User directory 'C:\Users\$target_user' not found!"
        exit
    }
    
    $config_content = @"
[wsl2]
networkingMode=mirrored
dnsTunneling=true
firewall=true
autoProxy=true
"@

    Set-Content -Path $target_path -Value $config_content
    Write-Host "Created $target_path" -ForegroundColor Green
    
    Write-Host "`nDONE! You MUST run 'wsl --shutdown' in your user terminal for changes to apply." -ForegroundColor Magenta
    exit
}

# --- OPTION 1: STANDARD PORT PROXY ---

# 1. WSL IP Detection
$wsl_ip = $args[0]

if (-not $wsl_ip) {
    Write-Host "Attempting auto-detection..." -ForegroundColor Gray
    try {
        $wsl_output = wsl hostname -I 2>$null
        $wsl_ip = [regex]::match($wsl_output, "\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b").Value
    } catch {
        # Ignore errors, we'll prompt below
    }
}

if (-not $wsl_ip) {
    Write-Warning "Could not automatically detect WSL IP (You might be running as a different Admin user)."
    Write-Host "Please open a NORMAL terminal (not Admin), run 'wsl hostname -I', and paste the IP below." -ForegroundColor Yellow
    $wsl_ip = Read-Host "Enter WSL IP Address"
}

if (-not $wsl_ip) {
    Write-Error "No IP provided. Exiting."
    exit
}
Write-Host "Using Target IP: $wsl_ip" -ForegroundColor Cyan

# 2. Clear old rules
Write-Host "Clearing old PortProxy rules for port 8000..."
netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0 | Out-Null

# 3. Add new rule
Write-Host "Forwarding Windows Port 8000 -> WSL $wsl_ip:8000..."
netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=$wsl_ip

# 4. Firewall Rule
Write-Host "Configuring Windows Firewall..."
Remove-NetFirewallRule -DisplayName "Mesh Server" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "Mesh Server" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow | Out-Null

# 5. Detect External IP (Robust)
Write-Host "`n---------------------------------------------------"
Write-Host "Network Configuration Complete!" -ForegroundColor Green
Write-Host "External devices can connect to ANY of these addresses:" -ForegroundColor Yellow

$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { 
    $_.IPAddress -notlike "127.*" -and 
    $_.IPAddress -notlike "169.254.*" -and 
    $_.InterfaceAlias -notlike "*WSL*" 
}

foreach ($ip in $ips) {
    Write-Host "  $($ip.InterfaceAlias): http://$($ip.IPAddress):8000"
}
Write-Host "---------------------------------------------------"

