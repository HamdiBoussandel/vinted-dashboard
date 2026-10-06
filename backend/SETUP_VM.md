# Préparation du VM dédié (Dell XPS 15) — guide pas à pas

Contexte (cf. échanges du 03-04/10/2026) : ce PC sert exclusivement à faire
tourner le backend VintedPro, sans surveillance quotidienne, avec une
contrainte d'extinction nocturne (23h-10h). À suivre dans l'ordre sur la
machine elle-même (pas sur le PC de dev).

## 1. Connexion automatique (PowerShell, en administrateur)

Sans ça, le PC redémarre à 10h mais reste bloqué sur l'écran de connexion --
personne n'est là pour taper le mot de passe.

**Méthode recommandée** : l'outil `Autologon.exe` de Sysinternals (Microsoft)
plutôt qu'un réglage manuel du registre -- il stocke le mot de passe de façon
chiffrée (LSA Secrets), pas en clair comme le ferait un `reg add` direct sur
`DefaultPassword`.

1. Télécharger : https://learn.microsoft.com/sysinternals/downloads/autologon
2. Lancer `Autologon.exe`, remplir domaine (vide si compte local) /
   utilisateur / mot de passe, cliquer *Enable*.

## 2. Fuseau horaire

Tous les crons du backend (minuit, 14h, 22h, fenêtre 11h-19h) utilisent
l'heure locale -- un décalage silencieux casserait tout.

```powershell
tzutil /s "Romance Standard Time"
```

## 3. Alimentation -- jamais de veille pendant les heures actives

Le PC doit rester allumé et actif de 10h à 23h (l'extinction elle-même est
gérée par `arret_nocturne.bat`, pas par un minuteur de veille). À lancer en
administrateur :

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 15
```

(L'écran peut s'éteindre après 15 min, ça n'affecte pas Chrome -- seule la
VEILLE SYSTÈME doit être désactivée.)

## 4. Heures actives Windows Update

Empêche Windows de redémarrer tout seul en pleine tâche pour une mise à jour.

```powershell
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v ActiveHoursStart /t REG_DWORD /d 10 /f
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v ActiveHoursEnd /t REG_DWORD /d 23 /f
```

## 5. Python + dépendances

Installer **Python 3.14** (version exacte utilisée actuellement, cf. `venv`
du poste de dev) depuis python.org, puis dans `backend/` :

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m playwright install
```

Note : `requirements.txt` est encodé en UTF-16 -- ne pas le re-sauvegarder en
UTF-8 sans vérifier que `pip` le lit toujours correctement (déjà signalé dans
`CLAUDE.md`).

## 6. Vrai Chrome (pas seulement le Chromium de Playwright)

Installer Chrome depuis google.com/chrome -- les profils `clemz_session_*`
utilisent `channel="chrome"`, pas le Chromium embarqué.

## 7. `.env`

À recréer à la main dans `backend/.env` (jamais commité) avec les vraies clés :
`SUPABASE_URL`, `SUPABASE_KEY`, clé(s) Gemini, et surtout **`GMAIL_ADDRESS`,
`GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL_TO`** -- indispensables maintenant que
les alertes de reconnexion de session et de captcha en dépendent (cf.
échanges du 02-03/10/2026).

## 8. Exclusions Windows Defender (PowerShell, administrateur)

Évite les faux positifs / ralentissements sur un navigateur piloté par CDP.

```powershell
Add-MpPreference -ExclusionPath "C:\chemin\vers\vinted-dashboard"
Add-MpPreference -ExclusionProcess "chrome.exe"
Add-MpPreference -ExclusionProcess "python.exe"
```

## 9. Accès à distance

Installer un outil pour se connecter depuis le mobile/un autre PC (cf.
échange du 02/10/2026) : **Tailscale** + Microsoft Remote Desktop, ou
**AnyDesk** en solution plus rapide à mettre en place. Jamais exposer RDP
directement sur Internet.

## 10. Tâches planifiées

Déjà préparées côté code (`demarrer_backend.bat`, `arret_nocturne.bat`,
endpoint `/api/maintenance/preparer-arret`) -- à enregistrer une fois tout ce
qui précède en place :

```powershell
schtasks /create /tn "VintedPro Backend" /tr "\"C:\chemin\vers\vinted-dashboard\backend\demarrer_backend.bat\"" /sc onlogon /rl limited /f

schtasks /create /tn "VintedPro Extinction nocturne" /tr "\"C:\chemin\vers\vinted-dashboard\backend\arret_nocturne.bat\"" /sc daily /st 23:00 /rl limited /f
```

## 11. Profils Chrome Clemz (le plus délicat)

Deux options, à tester dans cet ordre :

1. **Copier** les dossiers `clemz_session_chrome_d1_reel/` et
   `clemz_session_chrome_reel/` depuis l'ancien poste, puis vérifier que les
   sessions Vinted restent valides (lancer un scraping test).
2. Si la copie ne fonctionne pas (sessions invalides) : repartir de zéro --
   installer Chrome, charger l'extension Clemz manuellement
   (`chrome://extensions` → *Charger l'extension non empaquetée*), se
   reconnecter aux deux comptes Vinted.

## 12. Réveil matinal (en attente -- cf. échange du 04/10/2026)

À vérifier dans le BIOS (F2 au démarrage → *Power Management* → *Auto On
Time*). Si disponible : programmer 10h00, tous les jours, PC branché
secteur en permanence. Si indisponible : revoir le choix extinction complète
vs veille (S3), cf. discussion -- **pas encore tranché**.
