# PharmaTech health check: services, listeners and non-sensitive health endpoints only.
$ok = $true
Get-Service PharmaTech-API, PharmaTech-UI, PharmaTech-Nginx | ForEach-Object {
    "{0,-18} {1}" -f $_.Name, $_.Status; if ($_.Status -ne 'Running') { $ok = $false } }
foreach ($u in 'http://127.0.0.1:8000/health', 'http://127.0.0.1:8501/_stcore/health', 'http://127.0.0.1/_stcore/health') {
    try { $r = Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 5; "{0,-40} {1} {2}" -f $u, $r.StatusCode, $r.Content }
    catch { "{0,-40} FAILED" -f $u; $ok = $false } }
"Listeners:"
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 80, 443, 8000, 8501 } |
    ForEach-Object { "  {0}:{1}" -f $_.LocalAddress, $_.LocalPort; if ($_.LocalPort -in 8000, 8501 -and $_.LocalAddress -ne '127.0.0.1') { $ok = $false } }
if ($ok) { "RESULT: OK" } else { "RESULT: PROBLEM"; exit 1 }
