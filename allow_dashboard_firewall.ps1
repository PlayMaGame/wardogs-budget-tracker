# Allow the Wardogs cash dashboard through Windows Firewall on Private networks.
# Run this once in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\allow_dashboard_firewall.ps1
#
# To limit access to one PC:
#   .\allow_dashboard_firewall.ps1 -RemoteAddress 192.168.1.50

param(
    [int]$Port = 8765,
    [string]$RemoteAddress = ""
)

$ruleName = "Wardogs Cash Dashboard"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Error "Run this script as Administrator."
    exit 1
}

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

$params = @{
    DisplayName = $ruleName
    Direction   = "Inbound"
    Protocol    = "TCP"
    LocalPort   = $Port
    Action      = "Allow"
    Profile     = "Private"
}
if ($RemoteAddress) {
    $params.RemoteAddress = $RemoteAddress
}

New-NetFirewallRule @params | Out-Null

Write-Host "Allowed TCP $Port on Private networks."
if ($RemoteAddress) {
    Write-Host "Only $RemoteAddress can connect."
}

$ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.PrefixOrigin -ne "WellKnown"
    } |
    Select-Object -First 1 -ExpandProperty IPAddress

if ($ip) {
    Write-Host "Open from another PC: http://${ip}:$Port/"
}
