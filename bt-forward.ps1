# Windows COM<->TCP forwarder (dumb pipe). Runs on the Windows laptop.
# Connects OUT to the WSL mux, so there is NO inbound Windows Firewall prompt and
# no admin needed. WSL2 forwards the mux's listening port to Windows' loopback,
# so we just dial 127.0.0.1. Native .NET only - no install.
# Reconnect loop: if Bluetooth drops, it reopens the COM port and redials WSL.
#
# Start the WSL side first (python3 bt-bridge-wsl.py), then run this.
#
#   .\bt-forward.ps1 -COM COM5            # or it will prompt
#   .\bt-forward.ps1 -COM COM5 -Port 20000
#
# If execution policy blocks this file, paste its body into a PowerShell window
# instead (execution policy does not apply to interactively typed/pasted code).
param(
  [string]$COM  = $(Read-Host "Bluetooth COM port (e.g. COM5)"),
  [int]   $Port = 20000
)

while ($true) {
  $sp = $null; $client = $null; $ns = $null
  try {
    $sp = New-Object System.IO.Ports.SerialPort($COM, 115200); $sp.Open()   # baud nominal for BT
    $client = New-Object System.Net.Sockets.TcpClient; $client.Connect("127.0.0.1", $Port)
    $ns = $client.GetStream()
    Write-Host "linked: COM $COM <-> tcp/$Port  (Ctrl-C to stop)"
    $a = $sp.BaseStream.CopyToAsync($ns)     # COM -> WSL
    $b = $ns.CopyToAsync($sp.BaseStream)     # WSL -> COM
    [System.Threading.Tasks.Task]::WaitAny(@($a, $b)) | Out-Null
  } catch {
    Write-Host "link error: $($_.Exception.Message)"
  } finally {
    if ($ns)     { $ns.Dispose() }
    if ($client) { $client.Close() }
    if ($sp -and $sp.IsOpen) { $sp.Close() }
  }
  Write-Host "link dropped; reconnecting in 2s..."
  Start-Sleep -Seconds 2
}
