# surveiller_backend.ps1 -- appele par demarrer_backend.bat (09/10/2026).
#
# Affiche EN DIRECT le journal du serveur (logs\demarrage.log, ecrit par
# Python) dans la fenetre de demarrage, qui restait jusqu'ici un ecran noir
# (sortie entierement redirigee vers le fichier). Le titre de la fenetre
# indique en permanence l'etat du backend, verifie toutes les 30 s par un
# appel a http://127.0.0.1:8000/ -- EN LIGNE / HORS LIGNE + heure du controle.
#
# Lecture seule : ce script ne demarre ni n'arrete rien. Fermer la fenetre
# arrete en revanche le serveur (meme console que Python), comme avant.

param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [int]$Port = 8000,
    [int]$IntervalleControleSecondes = 30
)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "VintedPro Backend - demarrage..."

while (-not (Test-Path $LogPath)) { Start-Sleep -Milliseconds 500 }

# Partage ReadWrite+Delete : Python garde le fichier ouvert en ecriture.
$flux = [IO.File]::Open($LogPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]'ReadWrite, Delete')
$lecteur = New-Object IO.StreamReader($flux, [Text.Encoding]::UTF8)
$dernierControle = [datetime]::MinValue
$dernierEtat = $null

while ($true) {
    $ligne = $lecteur.ReadLine()
    if ($null -ne $ligne) {
        Write-Host $ligne
        continue
    }

    if (((Get-Date) - $dernierControle).TotalSeconds -ge $IntervalleControleSecondes) {
        $dernierControle = Get-Date
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/" -TimeoutSec 5 | Out-Null
            $etat = "EN LIGNE"
        } catch {
            $etat = "HORS LIGNE"
        }
        $Host.UI.RawUI.WindowTitle = "VintedPro Backend - $etat (controle $($dernierControle.ToString('HH:mm:ss')))"
        if ($etat -ne $dernierEtat) {
            $couleur = if ($etat -eq "EN LIGNE") { "Green" } else { "Red" }
            Write-Host "===== [$($dernierControle.ToString('HH:mm:ss'))] Backend $etat =====" -ForegroundColor $couleur
            $dernierEtat = $etat
        }
    }

    Start-Sleep -Milliseconds 500
}
