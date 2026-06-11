# Deployment Script
# Automatically pushes local changes to GitHub and deploys them to the remote Oracle VPS

Write-Host "1. Committing and pushing local changes to GitHub..." -ForegroundColor Cyan
git add .
git commit -m "Auto-deploy update"
git push

Write-Host "2. Connecting to remote server to pull and restart..." -ForegroundColor Cyan
$sshKey = "C:\Users\palla\Downloads\ssh-key-2026-06-09.key"
$remoteIp = "158.180.54.178"
$remoteUser = "ubuntu"

ssh -i $sshKey -o StrictHostKeyChecking=no ${remoteUser}@${remoteIp} "cd tozsde-figyelo && git pull && sudo systemctl restart tozsde.service"

Write-Host "Deploy complete!" -ForegroundColor Green
