# configurer_serveur.ps1 -- a lancer UNE FOIS sur le PC portable serveur, dans un
# PowerShell "Executer en tant qu'administrateur". Relancable sans danger.
#
#   powershell -ExecutionPolicy Bypass -File .\configurer_serveur.ps1
#   powershell -ExecutionPolicy Bypass -File .\configurer_serveur.ps1 -ArretNocturne
#
# -ArretNocturne planifie aussi arret_nocturne.bat a 23h. Sans rallumage automatique
# (Auto On dans le BIOS) ni ouverture de session automatique, le backend ne
# repart pas le lendemain : a n'activer qu'une fois ces deux points regles.
#
# Ne touche qu'au secteur (AC) : sur batterie, la mise en veille reste active, ce
# qui protege les profils navigateur si la coupure de courant dure.

param([switch]$ArretNocturne)

$ErrorActionPreference = 'Stop'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "A lancer dans un PowerShell administrateur." }

$dossier = Split-Path -Parent $MyInvocation.MyCommand.Path
$utilisateur = "$env:USERDOMAIN\$env:USERNAME"

# --- Alimentation : jamais de veille sur secteur, capot ferme sans effet ---
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
Write-Host "OK  Alimentation : pas de veille sur secteur, capot sans effet."

# --- Windows Update : pas de redemarrage automatique pendant les heures d'activite ---
$cleUpdate = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
Set-ItemProperty -Path $cleUpdate -Name SmartActiveHoursState -Value 2 -Type DWord
Set-ItemProperty -Path $cleUpdate -Name ActiveHoursStart -Value 8 -Type DWord
Set-ItemProperty -Path $cleUpdate -Name ActiveHoursEnd -Value 23 -Type DWord
Write-Host "OK  Windows Update : heures d'activite 8h-23h."

# --- Backend : au demarrage de la session, sans condition de batterie ni duree limite ---
$reglages = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $utilisateur -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'VintedPro Backend' -Force `
    -Action (New-ScheduledTaskAction -Execute (Join-Path $dossier 'demarrer_backend.bat') -WorkingDirectory $dossier) `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $utilisateur) `
    -Settings $reglages -Principal $principal | Out-Null
Write-Host "OK  Tache 'VintedPro Backend' : demarrage a l'ouverture de session."

if ($ArretNocturne) {
    $principalArret = New-ScheduledTaskPrincipal -UserId $utilisateur -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName 'VintedPro Arret Nocturne' -Force `
        -Action (New-ScheduledTaskAction -Execute (Join-Path $dossier 'arret_nocturne.bat') -WorkingDirectory $dossier) `
        -Trigger (New-ScheduledTaskTrigger -Daily -At '23:00') `
        -Settings $reglages -Principal $principalArret | Out-Null
    Write-Host "OK  Tache 'VintedPro Arret Nocturne' : chaque jour a 23h."
}

Write-Host ""
Write-Host "A faire a la main :"
Write-Host " - Ouverture de session automatique (le navigateur a besoin d'une session ouverte)."
Write-Host " - Cable Ethernet si possible, et chargeur branche en permanence."
Write-Host " - backend\.env : PAS de PLAN_SOURCE_URL ici (serveur = machine de reference)."
