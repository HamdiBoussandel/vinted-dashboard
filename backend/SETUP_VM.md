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

## 5. Cloner le dépôt

Le code (backend + frontend) est maintenant sur GitHub -- plus besoin de
copier des fichiers à la main depuis l'ancien poste pour ça.

1. Installer **Git pour Windows** : https://git-scm.com/download/win
   (juste l'exécutable en ligne de commande -- pas besoin de VS Code ni
   d'aucun IDE sur ce PC, qui ne sert qu'à FAIRE TOURNER le backend, jamais
   à le modifier).
2. Cloner le dépôt à l'endroit choisi (ex: `C:\vinted-dashboard`) :

   ```powershell
   cd C:\
   git clone https://github.com/HamdiBoussandel/vinted-dashboard.git
   ```

3. Vérifier que le clone a bien pris : `backend/`, `frontend/`, `CLAUDE.md`
   doivent être présents à la racine.

**Ce que le clone apporte déjà** (donc à ne PAS recopier séparément depuis
l'ancien poste) : tout le code, `requirements.txt` à jour, et même les
dossiers de profils Chrome `clemz_session_chrome_d1_reel/` /
`clemz_session_chrome_reel/` (commités le 06/10/2026 pour ce transfert --
voir vérification de validité à l'étape 13).

**Ce que le clone n'apporte PAS** (volontairement, jamais commité) :
`backend/.env` (étape 8) et le `venv` Python (étape 6) -- à recréer sur
place à chaque fois.

Pour les mises à jour futures (après la première installation), un simple
`git pull` dans le dossier cloné suffit -- pas besoin de recloner.

## 6. Python + dépendances

Installer **Python 3.14** (version exacte utilisée actuellement, cf. `venv`
du poste de dev) depuis python.org, puis dans `backend/` :

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m playwright install
```

Note (corrigée le 06/10/2026) : `requirements.txt` est maintenant en UTF-8
standard (regénéré via `pip freeze`, l'ancien fichier UTF-16 était cassé) et
contient une ligne `--extra-index-url` pointant vers l'index PyTorch CPU
(download.pytorch.org/whl/cpu) en tête -- nécessaire pour que `pip` trouve
les wheels CPU-only de `torch`/`torchvision` (utilisés par `open_clip_torch`,
CLIP local pour le regroupement de photos). Une seule commande
`pip install -r requirements.txt` suffit, pas d'étape séparée pour torch.

## 7. Frontend (dashboard React) -- pour consulter/piloter depuis ce PC

Permet d'ouvrir le dashboard directement sur le VM (en local, ou à distance
via l'outil de l'étape 11) plutôt que de passer par les scripts de test en
ligne de commande -- utile aussi bien pour vérifier que les tâches
d'automatisation tournent correctement que pour continuer à développer de
nouvelles fonctionnalités depuis ce PC.

1. Installer **Node.js** (version LTS récente -- le poste de dev tourne
   actuellement en v22.15.1/npm 11.9.0, à viser comme référence) depuis
   nodejs.org.
2. Dans `frontend/` :

   ```powershell
   npm install
   npm run dev
   ```

   Lance le serveur de développement Vite sur localhost, port 5173 par
   défaut.
3. Le **backend doit tourner en même temps** (étape 6 + `run.py`, ou la
   tâche planifiée "VintedPro Backend" une fois en place à l'étape 12) :
   le frontend appelle `127.0.0.1:8000/api` en dur (pas de variable
   d'environnement pour changer l'URL, cf. `CLAUDE.md`) -- donc frontend ET
   backend doivent obligatoirement tourner sur la MÊME machine. Impossible
   d'ouvrir le dashboard depuis le PC de dev en pointant vers le backend du
   VM sans modifier ce code.
4. Ouvrir `localhost:5173` dans un navigateur **sur le VM lui-même** (ou via
   l'outil d'accès à distance de l'étape 11, qui affiche l'écran du VM --
   pas un simple partage réseau du port).

## 8. Vrai Chrome (pas seulement le Chromium de Playwright)

Installer Chrome depuis google.com/chrome -- les profils `clemz_session_*`
utilisent `channel="chrome"`, pas le Chromium embarqué.

## 9. `.env`

À recréer à la main dans `backend/.env` (jamais commité) avec les vraies clés :
`SUPABASE_URL`, `SUPABASE_KEY`, clé(s) Gemini, et surtout **`GMAIL_ADDRESS`,
`GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL_TO`** -- indispensables maintenant que
les alertes de reconnexion de session et de captcha en dépendent (cf.
échanges du 02-03/10/2026).

## 10. Exclusions Windows Defender (PowerShell, administrateur)

Évite les faux positifs / ralentissements sur un navigateur piloté par CDP.

```powershell
Add-MpPreference -ExclusionPath "C:\chemin\vers\vinted-dashboard"
Add-MpPreference -ExclusionProcess "chrome.exe"
Add-MpPreference -ExclusionProcess "python.exe"
```

## 11. Accès à distance

Installer un outil pour se connecter depuis le mobile/un autre PC (cf.
échange du 02/10/2026) : **Tailscale** + Microsoft Remote Desktop, ou
**AnyDesk** en solution plus rapide à mettre en place. Jamais exposer RDP
directement sur Internet.

## 12. Tâches planifiées

Déjà préparées côté code (`demarrer_backend.bat`, `arret_nocturne.bat`,
endpoint `/api/maintenance/preparer-arret`) -- à enregistrer une fois tout ce
qui précède en place :

```powershell
schtasks /create /tn "VintedPro Backend" /tr "\"C:\chemin\vers\vinted-dashboard\backend\demarrer_backend.bat\"" /sc onlogon /rl limited /f

schtasks /create /tn "VintedPro Extinction nocturne" /tr "\"C:\chemin\vers\vinted-dashboard\backend\arret_nocturne.bat\"" /sc daily /st 23:00 /rl limited /f
```

**En cas d'échec au démarrage** (cf. échange du 07/10/2026) : `demarrer_backend.bat`
redirige désormais toute sa sortie (y compris les erreurs) vers
`backend\logs\demarrage.log`, écrasé à chaque lancement -- ni un double-clic
(fenêtre fermée instantanément) ni la tâche planifiée (aucune fenêtre du
tout, déclenchée sans personne devant l'écran) ne permettent de lire une
erreur autrement. Ouvrir ce fichier après un échec, ou le coller tel quel si
besoin d'aide pour l'interpréter.

## 13. Profils Chrome Clemz (le plus délicat)

Depuis le 06/10/2026, les dossiers `clemz_session_chrome_d1_reel/` et
`clemz_session_chrome_reel/` arrivent déjà remplis via le `git clone` de
l'étape 5 -- plus besoin de copie manuelle séparée. Reste à **vérifier**
qu'ils fonctionnent toujours sur ce PC :

1. Avec le backend démarré, vérifier les 2 sessions en une requête
   (mode invisible, aucune reconnexion déclenchée) :

   ```
   curl http://127.0.0.1:8000/api/check-all-sessions
   ```

   Réponse attendue : `{"session_status": {"chrome_clemz": true, "edge_clemz": true}}`
   (`chrome_clemz` = Dressing 1, `edge_clemz` = Dressing 2). `false` = session
   expirée -- une empreinte machine différente peut parfois invalider une
   session malgré des cookies copiés intacts.
2. Si invalide : repartir de zéro -- installer Chrome, charger l'extension
   Clemz manuellement (`chrome://extensions` → *Charger l'extension non
   empaquetée*, dossier `backend/clemz_extension/`), se reconnecter aux deux
   comptes Vinted.

## 14. Réveil matinal (en attente -- cf. échange du 04/10/2026)

À vérifier dans le BIOS (F2 au démarrage → *Power Management* → *Auto On
Time*). Si disponible : programmer 10h00, tous les jours, PC branché
secteur en permanence. Si indisponible : revoir le choix extinction complète
vs veille (S3), cf. discussion -- **pas encore tranché**.
