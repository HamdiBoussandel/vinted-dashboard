"""
Republication ASSISTÉE via l'auto-complétion Clemz (18/10/2026).

Nouveau processus préconisé par Clemz pour être moins détectable par Vinted
que la republication de masse ("Go Clemz !") : l'annonce est recréée par le
formulaire NORMAL de Vinted (/items/new), rempli par Clemz depuis sa
sauvegarde, et c'est l'UTILISATEUR qui clique lui-même sur le bouton de
publication de Vinted. Clemz détecte ce clic, retrouve la nouvelle annonce
et supprime l'ancienne pendant le processus.

Ce module automatise tout SAUF ce clic (décision explicite de
l'utilisateur, 18/10/2026 -- automatiser aussi la validation annulerait
l'intérêt anti-détection du processus) :

  1. prépare la "liste à traiter" Clemz (vidage + Smart Dressing, mêmes
     méthodes que ClemzAutomation) ;
  2. pour chaque article : ouvre /items/new, onglet "Auto complétion",
     choisit l'article, lance le remplissage et confirme l'import ;
  3. ATTEND que l'utilisateur publie (la page quitte /items/new pour
     /items/<id>), laisse Clemz finaliser, enregistre la republication
     (base + quota) puis passe à l'article suivant, sans autre intervention.

Fenêtre VISIBLE et au premier plan (l'utilisateur relit le formulaire) --
jamais hors écran comme les automatisations de masse. Mêmes garde-fous que
la republication de masse : verrou de profil, préemption du watchdog,
quota risk_guard revérifié avant CHAQUE article, bouton "Arrêter".
"""
import asyncio
import logging
import random
import re
import unicodedata
from urllib.parse import quote

from playwright.async_api import async_playwright

from services.clemz_automation import (
    ClemzAutomation,
    PROGRESSION,
    ARRETS_DEMANDES,
    arret_demande,
    lire_progression,
    _args_et_kwargs_navigateur,
    _close_clemz_promo_tabs,
)
from services.clemz_cdp import (
    snapshot_by_id,
    get_live_props,
    click_element,
    ensure_panel_open,
    is_element_visible,
    query_selector_in,
    get_center_coords,
    real_mouse_click,
    set_select_value,
    set_input_value,
)
from services.session_manager import ensure_session, _send_notification

logger = logging.getLogger(__name__)

URL_NOUVELLE_ANNONCE = "https://www.vinted.fr/items/new"
# Annonce publiée : /items/<id> ou /items/<id>-<slug>, jamais /items/new ni /edit
_URL_ANNONCE_PUBLIEE = re.compile(r"vinted\.fr/items/\d+(?:-[^/?#]*)?/?(?:[?#].*)?$")

ATTENTE_REMPLISSAGE_SECONDES = 15 * 60  # plafond total du remplissage par Clemz (photos comprises)
ATTENTE_SANS_PROGRES_SECONDES = 5 * 60  # abandon si #processStage ne bouge plus pendant ce temps
ATTENTE_VALIDATION_SECONDES = 30 * 60   # temps laissé à l'utilisateur pour relire et publier
ATTENTE_FINALISATION_CLEMZ_SECONDES = 90  # déclaration Clemz après publication (retrouve l'annonce)

# Étape 1 (préparation de la liste) : depuis le dressing SAUVEGARDÉ sur
# clemz.app -- seuls les articles importés de là portent un identifiant
# clemz.app, indispensable à l'auto-complétion (le Smart Dressing ne suffit
# pas : "Absent des annonces importées de clemz.app", essai du 18/10/2026).
URL_CLEMZ_ITEMS = "https://www.clemz.app/dashboard/items"
# Dressing clemz.app de chaque compte Vinted (confirmé par l'utilisateur le 18/10/2026)
WARDROBES_CLEMZ = {"Dressing 1": "27962", "Dressing 2": "34215"}  # @kimi_shiro, @dx_griselda
# ATTENTION : dans la liste "à traiter" de l'extension (mainList), wardrobe_id
# est l'identifiant VINTED du compte, pas celui du dressing clemz.app ci-dessus
# (constaté le 18/10/2026 : entrée #8225671 avec wardrobe_id=287248160).
MEMBRES_VINTED = {"Dressing 1": "287248160", "Dressing 2": "3136979514"}
ATTENTE_IMPORT_SECONDES = 30  # ajout d'un article à la liste "à traiter" via "Ajouter à l'extension"
# Liste clemz.app où Clemz range les articles supprimés (option "Ajouter à une
# liste clemz.app après suppression") -- choisie PAR SON NOM dans la fenêtre
# "Ajouter à une liste sur clemz.app" (demandé le 19/10/2026).
LISTE_CLEMZ_SUPPRESSION = "Liste_test"

# Mode "Validation manuelle" de Clemz (20/10/2026) -- réglages imposés dans la
# fenêtre "Protection" du panneau Republier pendant la série, puis RESTAURÉS
# (Clemz les mémorise : sans restauration, la republication de masse
# resterait en validation manuelle). {id du <select>: valeur}, dans l'ordre.
REGLAGES_VALIDATION_MANUELLE = {
    "pacingModeSelect": "humanLike",        # Rythme naturel
    "pacingHumanPresetSelect": "slower",    # Très lent (~4 min/article)
    "executionModeSelect": "manualValidation",
}
PAUSE_AVANT_AJOUTER_SECONDES = (1, 3)       # pause aléatoire avant le clic sur "Ajouter"
ATTENTE_SANS_PROGRES_VALIDATION_SECONDES = 20 * 60  # Clemz "très lent" ~4 min/article + remplissage
# Option "Prix" de la fenêtre "Modifier à la republication" de Clemz (mise à
# jour de l'extension du 20/10/2026) -- clés mémorisées par Clemz, réglées
# article par article puis l'option DÉSACTIVÉE en fin de série.
CLES_PRIX_REPUBLICATION = ("repostModifyPrice", "repostModifyPriceDirection", "repostModifyPriceValue",
                           "repostModifyPriceType", "repostModifyPriceRound")

# Session en cours par dressing -- lue par la route de statut (dashboard)
SESSIONS = {}


class _PageFermeeAuDemarrage(Exception):
    """La page Vinted s'est fermée avant toute action -- relance possible sans risque."""


class _SerieInterrompue(Exception):
    """Étape 0 (suppression) impossible ou incertaine : la série s'arrête là."""


class _ErreurClemz(Exception):
    """Clemz affiche "XXX ERREUR XXX" (ex. "Action non autorisée", 21/10/2026)."""


# Marqueur d'erreur affiché par Clemz dans sa progression (#articleTitle /
# #progressContent), suivi du motif dans #processStage.
MARQUEUR_ERREUR_CLEMZ = "xxx erreur xxx"


def _normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texte).strip().lower().rstrip("…").rstrip(".").strip()


def _correspond(titre_clemz, nom):
    """Clemz tronque les titres à 42 caractères + '…' dans le sélecteur."""
    t, n = _normaliser(titre_clemz), _normaliser(nom)
    return bool(t) and (t == n or n.startswith(t) or t.startswith(n))


class AutoCompletionSession:
    def __init__(self, dressing, produits, task_id=None, pas_a_pas=False, suppression_prealable=False,
                 remplissage=None, suppression_seule=False, validation_manuelle=False, prix_par_nom=None):
        """produits : liste de noms d'articles (ordre de passage).
        pas_a_pas : pause AVANT chaque action, reprise par le bouton
        "Continuer" du dashboard (cf. _pas) -- mise au point du scénario.
        remplissage : "smart_dressing" ou "clemz_app" = REMPLISSAGE SEUL de la
        liste "à traiter" par cette source (19/10/2026), puis arrêt -- ni
        suppression, ni publication. None = série complète.
        suppression_seule : bouton "Supprimer les articles du dressing"
        (19/10/2026) -- étape 0 seule (Smart Dressing, sauvegarde clemz.app,
        suppression sur Vinted), sans republication ensuite.
        (Le mode "préparation seule" a été retiré le 18/10/2026 : resté coché
        par erreur, il sautait l'étape 0 sans que ça se voie.)"""
        self.dressing = dressing
        self.produits = list(produits)
        self.task_id = task_id
        self.pas_a_pas = pas_a_pas
        if remplissage not in (None, "smart_dressing", "clemz_app"):
            raise ValueError(f"Source de remplissage inconnue : {remplissage}")
        self.remplissage = remplissage
        self.suppression_seule = suppression_seule
        # Mode "Validation manuelle" natif de Clemz (20/10/2026), cf.
        # _serie_validation_manuelle : Clemz enchaîne la liste, le script
        # clique "Ajouter" à chaque passation.
        self.validation_manuelle = validation_manuelle
        # Baisse de prix À LA REPUBLICATION en validation manuelle (20/10/2026) :
        # {nom: nouveau prix} (prix_republication de l'inventaire). Réglée
        # article par article dans l'option "Prix" de Clemz (modifier à la
        # republication), puisque Clemz traite un article par "Go Clemz !".
        self.prix_par_nom = {n: round(float(p), 2) for n, p in (prix_par_nom or {}).items() if p}
        # Étape 0 (18/10/2026) : sauvegarde puis SUPPRESSION des annonces sur
        # Vinted via Clemz, avant leur republication par auto-complétion,
        # dans la MÊME série (profil verrouillé de bout en bout : aucun
        # scraping ne peut les marquer "vendues" entre les deux).
        self.suppression_prealable = suppression_prealable
        self.supprimes = []  # annonces effectivement supprimées de Vinted
        self._continuer = asyncio.Event()
        self.wardrobe_id = WARDROBES_CLEMZ[dressing]
        self.membre_vinted = MEMBRES_VINTED[dressing]
        # Identifiant clemz.app de chaque article, relevé à l'étape 1 -- sert à
        # reconnaître le premier article de la liste (data-clemz-id du
        # sélecteur), plus fiable que son titre tronqué à 42 caractères.
        self.nom_par_clemz_id = {}
        # Réutilise la config de compte, la construction de liste et
        # l'enregistrement (base + quota) de la republication de masse.
        produits_d1 = self.produits if dressing == "Dressing 1" else []
        produits_d2 = self.produits if dressing == "Dressing 2" else []
        self.bot = ClemzAutomation(produits_d1, produits_d2, task_type="republication", task_id=task_id)
        self.acc = next(a for a in self.bot.accounts if a["produits"])
        self.etat = {
            "dressing": dressing,
            "statut": "demarrage",
            "article": None,
            "index": 0,
            "total": len(self.produits),
            "republies": [],
            "ignores": [],
            "message": None,
        }

    def _maj(self, **champs):
        self.etat.update(champs)
        PROGRESSION[self.dressing] = {
            "action": "Republication assistée",
            "compteur": self.etat["index"],
            "total": self.etat["total"],
            "article": self.etat["article"],
            "etape": self.etat["message"],
        }

    async def _pas(self, description, forcer=False):
        """Mode pas à pas : annonce l'action SUIVANTE et attend le bouton
        "Continuer" du dashboard (route /auto-completion/continuer). Sans
        effet hors mode pas à pas, sauf forcer=True. Une demande d'arrêt lève
        aussi la pause (l'appelant voit ensuite arret_demande())."""
        if not (self.pas_a_pas or forcer):
            return
        statut_avant = self.etat["statut"]
        self._continuer.clear()
        self._maj(statut="pause", message=f"⏸️ Prochaine action : {description}")
        logger.info(f"⏸️ [AUTO-COMPLÉTION {self.dressing}] Pas à pas — avant : {description}")
        while not self._continuer.is_set() and not arret_demande(self.dressing):
            try:
                await asyncio.wait_for(self._continuer.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
        self._maj(statut=statut_avant, message=description)

    def continuer(self):
        self._continuer.set()

    def _ignorer(self, nom, raison):
        logger.warning(f"⏭️ [AUTO-COMPLÉTION {self.dressing}] '{nom}' ignoré : {raison}")
        self.etat["ignores"].append({"nom": nom, "raison": raison})
        if self.task_id:
            from services.automation_scheduler import update_task_result_par_nom
            update_task_result_par_nom(self.task_id, nom, "skipped", raison)

    # ------------------------------------------------------------------ #
    # Lecture de l'état interne de Clemz (chrome.storage.local), via son
    # service worker -- None si inaccessible (on retombe alors sur l'URL).
    # ------------------------------------------------------------------ #
    async def _stockage_clemz(self, context, cles):
        for sw in context.service_workers:
            if sw.url.startswith("chrome-extension://") and "clemz_background" in sw.url:
                try:
                    return await sw.evaluate("(cles) => chrome.storage.local.get(cles)", cles)
                except Exception:
                    return None
        return None

    async def _nb_liste_a_traiter(self, context):
        """Nombre d'articles de CE dressing dans la liste "à traiter"
        (mainList), ou None si le stockage de l'extension est illisible."""
        stockage = await self._stockage_clemz(context, ["mainList"])
        if stockage is None:
            return None
        return sum(1 for e in (stockage.get("mainList") or []) if str(e.get("wardrobe_id")) == self.membre_vinted)

    async def _ids_liste_a_traiter(self, context):
        """Identifiants clemz.app présents dans la liste "à traiter" (mainList)
        de CE dressing, ou None si le stockage de l'extension est illisible."""
        stockage = await self._stockage_clemz(context, ["mainList"])
        if stockage is None:
            return None
        return {
            str(e.get("clemz_id"))
            for e in (stockage.get("mainList") or [])
            if e.get("clemz_id") is not None and str(e.get("wardrobe_id")) == self.membre_vinted
        }

    # ------------------------------------------------------------------ #
    # ÉTAPE 1 -- préparation de la liste "à traiter" depuis clemz.app
    # ------------------------------------------------------------------ #
    async def _ouvrir_clemz_app(self, context, cdp, page):
        """Panneau Clemz > onglet "Mon dressing" > bouton "Voir sur Clemz"
        (nouvel onglet clemz.app/dashboard/items). À défaut de bouton,
        ouverture directe de la même page."""
        app_page = None
        by_id = await snapshot_by_id(cdp)
        if "myDressingTabLink" in by_id and "toolBody" in by_id:
            await self._pas("Étape 1 — ouvrir l'onglet « Mon dressing » du panneau Clemz")
            await click_element(cdp, by_id["myDressingTabLink"]["node_id"])
            await asyncio.sleep(1)
            lien = await query_selector_in(cdp, by_id["toolBody"]["node_id"], f'a[href="{URL_CLEMZ_ITEMS}"]')
            if lien:
                await self._pas("Étape 1 — cliquer sur « Voir sur Clemz » (ouvre clemz.app)")
                try:
                    x, y = await get_center_coords(cdp, lien)
                    async with context.expect_page(timeout=10000) as nouvelle:
                        await real_mouse_click(page, x, y)
                    app_page = await nouvelle.value
                except Exception as e:
                    logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Bouton 'Voir sur Clemz' inopérant ({e}) — ouverture directe.")
        if app_page is None:
            app_page = await context.new_page()
            await app_page.goto(URL_CLEMZ_ITEMS, wait_until="domcontentloaded")
        await app_page.wait_for_load_state("domcontentloaded")
        # Jamais de confirmation acceptée automatiquement sur clemz.app (le site
        # en utilise pour la SUPPRESSION de sauvegardes) : refusée et tracée.
        app_page.on("dialog", lambda d: asyncio.create_task(self._refuser_dialogue(d)))
        if "/users/sign_in" in app_page.url:
            raise RuntimeError("Profil non connecté à clemz.app — connecte-toi dans ce profil depuis Maintenance")
        return app_page

    async def _refuser_dialogue(self, dialogue):
        logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Confirmation clemz.app refusée : {dialogue.message[:200]}")
        try:
            await dialogue.dismiss()
        except Exception:
            pass

    async def _filtrer_dressing(self, app_page):
        """Restreint la vue clemz.app au dressing de cette session (filtre
        "Tous les dressings" du site, mémorisé côté clemz.app). Retourne le
        filtre précédent, pour le restaurer en fin d'étape."""
        precedent = await app_page.evaluate(
            "() => [...document.querySelectorAll('.wardrobe-checkbox')].filter(c => c.checked).map(c => c.value)"
        )
        if precedent == [self.wardrobe_id]:
            return precedent
        await self._appliquer_filtre(app_page, [self.wardrobe_id])
        return precedent

    async def _appliquer_filtre(self, app_page, ids):
        async with app_page.expect_navigation(timeout=20000):
            await app_page.evaluate(
                """(ids) => {
                    for (const c of document.querySelectorAll('.wardrobe-checkbox')) c.checked = ids.includes(c.value);
                    document.getElementById('wardrobe-filter-form').requestSubmit();
                }""",
                ids,
            )
        await app_page.wait_for_load_state("domcontentloaded")

    async def _importer_article(self, context, app_page, nom, deja_dans_liste):
        """Recherche `nom` sur clemz.app, coche SA carte et l'ajoute à la liste
        "à traiter" (Actions > "Ajouter à l'extension"). Retourne
        (clemz_id, None) ou (None, raison)."""
        await self._pas(f"Étape 1 — rechercher « {nom} » sur clemz.app")
        await app_page.goto(f"{URL_CLEMZ_ITEMS}?text_search={quote(nom)}", wait_until="domcontentloaded")
        cartes = await app_page.evaluate(
            """() => [...document.querySelectorAll('.item-card')].map(c => ({
                id: c.getAttribute('articleid'),
                titre: (c.querySelector('h6')?.innerText || '').trim(),
            }))"""
        )
        exactes = [c for c in cartes if _normaliser(c["titre"]) == _normaliser(nom)]
        candidats = exactes or [c for c in cartes if _correspond(c["titre"], nom)]
        if not candidats:
            return None, f"Introuvable sur clemz.app ({len(cartes)} résultat(s) pour ce nom dans le dressing)"
        if len(candidats) > 1:
            return None, f"{len(candidats)} sauvegardes clemz.app correspondent à ce nom — choix ambigu, à faire à la main"
        clemz_id = candidats[0]["id"]
        if clemz_id in deja_dans_liste:
            return clemz_id, None

        await self._pas(f"Étape 1 — cocher la sauvegarde clemz.app #{clemz_id} « {candidats[0]['titre'][:60]} »")
        await app_page.check(f"#checkbox-item-{clemz_id}")
        await self._pas("Étape 1 — Actions › « Ajouter à l'extension » (ajout à la liste à traiter)")
        await app_page.click("#actions-menu-button-top")
        await app_page.click("#import-selected-to-extension")

        # Confirmation : l'identifiant apparaît dans la liste "à traiter"
        for _ in range(ATTENTE_IMPORT_SECONDES):
            await asyncio.sleep(1)
            ids = await self._ids_liste_a_traiter(context)
            if ids is None:
                await asyncio.sleep(4)
                logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Liste Clemz illisible — ajout de '{nom}' supposé fait.")
                return clemz_id, None
            if clemz_id in ids:
                return clemz_id, None
        return None, f"Ajout à la liste 'à traiter' non confirmé après {ATTENTE_IMPORT_SECONDES}s"

    async def _preparer_liste(self, context, cdp, page):
        """ÉTAPE 1 : remplit la liste "à traiter" depuis clemz.app avec les
        articles de la session, puis revient sur Vinted. Retourne la liste
        des noms prêts, dans l'ordre reçu du dashboard."""
        # Le vidage de la liste recharge la page Vinted : panneau à rouvrir
        await self._attendre_panneau(cdp, page)
        app_page = await self._ouvrir_clemz_app(context, cdp, page)
        prets = []
        precedent = None
        try:
            await self._pas(f"Étape 1 — filtrer clemz.app sur le dressing {self.dressing} (#{self.wardrobe_id})")
            precedent = await self._filtrer_dressing(app_page)
            deja = await self._ids_liste_a_traiter(context) or set()
            for i, nom in enumerate(self.produits, start=1):
                if arret_demande(self.dressing):
                    for reste in self.produits[i - 1:]:
                        self._ignorer(reste, "Arrêté à ta demande")
                    break
                self._maj(index=i, article=nom, message="Ajout à la liste Clemz depuis clemz.app")
                clemz_id, raison = await self._importer_article(context, app_page, nom, deja)
                if raison:
                    self._ignorer(nom, raison)
                    continue
                deja.add(clemz_id)
                self.nom_par_clemz_id[str(clemz_id)] = nom
                prets.append(nom)
                logger.info(f"📥 [AUTO-COMPLÉTION {self.dressing}] '{nom}' ajouté à la liste à traiter (clemz.app #{clemz_id}).")
        finally:
            await self._pas("Étape 1 — restaurer le filtre clemz.app, fermer l'onglet et revenir sur Vinted")
            if precedent is not None and precedent != [self.wardrobe_id]:
                try:
                    await self._appliquer_filtre(app_page, precedent)
                except Exception as e:
                    logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Filtre clemz.app non restauré : {e}")
            try:
                await app_page.close()
            except Exception:
                pass
            await page.bring_to_front()
        return prets

    # ------------------------------------------------------------------ #
    # ÉTAPE 0 -- sauvegarde puis suppression des annonces sur Vinted
    # ------------------------------------------------------------------ #
    async def _construire_liste_smart_dressing(self, cdp, page, libelle):
        """Liste "à traiter" vidée puis remplie par le Smart Dressing, comme la
        republication classique (méthodes de ClemzAutomation, inchangées).
        Retourne les noms ajoutés ; les autres sont retirés de la série."""
        await self._pas(f"Étape 0 — vider la liste « à traiter » ({libelle})")
        vidage = await self.bot._vider_liste_a_traiter(cdp, page, self.acc)
        if vidage["status"] != "success":
            raise _SerieInterrompue(vidage["reason"])
        await self._pas(f"Étape 0 — remplir la liste par le Smart Dressing ({libelle})")
        selection = await self.bot._build_list(page, cdp, self.acc)
        ajoutes = []
        for r in selection:
            if r["status"] == "success":
                ajoutes.append(r["nom"])
            else:
                self._ignorer(r["nom"], f"Introuvable par le Smart Dressing ({r.get('reason')}) — ni supprimé ni republié")
        return ajoutes

    async def _choisir_liste_clemz(self, cdp):
        """Fenêtre "Ajouter à une liste sur clemz.app" affichée après le clic
        sur "Supprimer les annonce(s)" : choisit LISTE_CLEMZ_SUPPRESSION dans
        #clemzList-select puis "Confirmer". Si la liste n'existe pas :
        "Annuler" et arrêt (rien supprimé). Sans fenêtre au bout de 15 s, on
        continue (Clemz ne la montre peut-être pas à chaque fois)."""
        for _ in range(30):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if "clemzList-select" not in by_id or not await self._visible(cdp, by_id, "clemzList-select"):
                continue
            # Les listes se chargent depuis clemz.app (spinner #clemzList-picker-loading)
            chargement = by_id.get("clemzList-picker-loading")
            if chargement and await is_element_visible(cdp, chargement["node_id"]):
                continue
            select = by_id["clemzList-select"]["node_id"]
            res = await cdp.send("DOM.querySelectorAll", {"nodeId": select, "selector": "option"})
            options = []
            for n in res.get("nodeIds", []):
                props = await get_live_props(cdp, n, props=("value", "innerText"))
                options.append((props.get("value"), (props.get("innerText") or "").strip()))
            valeur = next((v for v, t in options if t == LISTE_CLEMZ_SUPPRESSION), None)
            logger.info(f"📂 [AUTO-COMPLÉTION {self.dressing}] Listes clemz.app proposées : {[t for _, t in options]}")
            if valeur is None:
                if "clemzList-picker-cancel" in by_id:
                    await click_element(cdp, by_id["clemzList-picker-cancel"]["node_id"])
                raise _SerieInterrompue(f"Liste clemz.app « {LISTE_CLEMZ_SUPPRESSION} » introuvable — annulé, rien supprimé")
            await set_select_value(cdp, select, valeur)
            await asyncio.sleep(0.3)
            choisi = (await get_live_props(cdp, select, props=("value",))).get("value")
            if choisi != valeur:
                raise _SerieInterrompue(f"Liste « {LISTE_CLEMZ_SUPPRESSION} » non sélectionnable — rien supprimé")
            await self._pas(f"Étape 0 — confirmer la liste clemz.app « {LISTE_CLEMZ_SUPPRESSION} »")
            by_id = await snapshot_by_id(cdp)
            if "clemzList-picker-confirm" not in by_id:
                raise _SerieInterrompue("Bouton « Confirmer » de la fenêtre de liste introuvable — rien supprimé")
            await click_element(cdp, by_id["clemzList-picker-confirm"]["node_id"])
            logger.info(f"📂 [AUTO-COMPLÉTION {self.dressing}] Liste clemz.app « {LISTE_CLEMZ_SUPPRESSION} » choisie.")
            return
        logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Fenêtre de choix de liste clemz.app non affichée — on continue.")

    async def _confirmer_suppression(self, cdp, page, attendu):
        """Fenêtre Clemz "La suppression d'articles est DÉFINITIVE — => N seront
        supprimés" : confirmée seulement si N == attendu, sinon "Annuler"."""
        for _ in range(60):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if "continueAlertButton" in by_id and await self._visible(cdp, by_id, "continueAlertButton"):
                contenu = by_id.get("alert-content")
                texte = ((await get_live_props(cdp, contenu["node_id"], props=("innerText",))).get("innerText", "")
                         if contenu else "")
                logger.info(f"🔎 [AUTO-COMPLÉTION {self.dressing}] Confirmation de suppression Clemz : "
                            f"{texte.strip().replace(chr(10), ' · ')[:400]}")
                m = re.search(r"=>\s*(\d+)\s+seront\s+supprim", texte)
                if not m or int(m.group(1)) != attendu:
                    if "closeAlertButton" in by_id:
                        await click_element(cdp, by_id["closeAlertButton"]["node_id"])
                    vu = m.group(1) if m else "?"
                    raise _SerieInterrompue(f"Clemz annonçait {vu} suppression(s) au lieu de {attendu} — annulé, rien supprimé")
                await self._pas(f"Étape 0 — CONFIRMER la suppression DÉFINITIVE de {attendu} annonce(s) sur Vinted")
                x, y = await get_center_coords(cdp, by_id["continueAlertButton"]["node_id"])
                await real_mouse_click(page, x, y)
                await asyncio.sleep(1)
                return
        raise _SerieInterrompue("Fenêtre de confirmation de suppression Clemz non affichée — rien supprimé")

    async def _etape_suppression(self, cdp, page):
        """ÉTAPE 0 : Smart Dressing -> sauvegarde clemz.app ("Sauver annonces",
        pour que la republication reparte du dernier état) -> Smart Dressing à
        nouveau (Clemz vide la liste après chaque action) -> onglet Modifier >
        Supprimer, option "Ajouter à une liste clemz.app" activée -> suppression.
        Retourne les noms supprimés ; lève _SerieInterrompue si rien ne doit
        être republié."""
        self._maj(statut="suppression", message="Étape 0 — préparation de la liste (Smart Dressing)")
        a_sauver = await self._construire_liste_smart_dressing(cdp, page, "sauvegarde")
        if not a_sauver:
            raise _SerieInterrompue("Aucun article trouvé par le Smart Dressing")

        self._maj(message=f"Étape 0 — sauvegarde de {len(a_sauver)} annonce(s) sur clemz.app")
        await self._pas(f"Étape 0 — sauvegarder {len(a_sauver)} annonce(s) sur clemz.app (« Sauver annonces »)")
        nav = await self.bot._navigate_to_my_dressing_tab(cdp, page, self.acc)
        if nav["status"] != "success":
            raise _SerieInterrompue(f"Sauvegarde impossible : {nav['reason']} — rien supprimé")
        sauvegarde = await self.bot._trigger_save_backup(page, cdp, self.acc, expected_count=len(a_sauver))
        if sauvegarde["status"] != "success":
            raise _SerieInterrompue(f"Sauvegarde échouée : {sauvegarde.get('reason')} — rien supprimé")

        # La liste construite pour la sauvegarde est RÉUTILISÉE telle quelle
        # pour la suppression (19/10/2026, constaté au 1er test : Clemz ne la
        # vide pas après "Sauver annonces" -- la vider puis la refaire était
        # une étape en trop). Contrôle du contenu avant de supprimer.
        nb_liste = await self._nb_liste_a_traiter(page.context)
        if nb_liste == len(a_sauver):
            a_supprimer = list(a_sauver)
        elif nb_liste == 0:
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Liste vidée par Clemz après la sauvegarde — reconstruction.")
            a_supprimer = await self._construire_liste_smart_dressing(cdp, page, "suppression")
            a_supprimer = [n for n in a_supprimer if n in a_sauver]
        else:
            raise _SerieInterrompue(f"Liste à traiter incohérente après la sauvegarde ({nb_liste} article(s) au lieu de "
                                    f"{len(a_sauver)}) — rien supprimé")
        if not a_supprimer:
            raise _SerieInterrompue("Liste vide après sauvegarde — rien supprimé")
        self._maj(article=None)
        await self._attendre_panneau(cdp, page)

        self._maj(message=f"Étape 0 — suppression de {len(a_supprimer)} annonce(s)")
        await self._pas("Étape 0 — ouvrir l'onglet « Modifier » et choisir « Supprimer »")
        nav = await self.bot._navigate_to_modify_tab(cdp, page, self.acc)
        if nav["status"] != "success":
            raise _SerieInterrompue(f"{nav['reason']} — rien supprimé")
        by_id = await snapshot_by_id(cdp)
        if "modifyTabActionDelete" not in by_id:
            raise _SerieInterrompue("Bouton « Supprimer » de l'onglet Modifier introuvable — rien supprimé")
        await click_element(cdp, by_id["modifyTabActionDelete"]["node_id"])
        await asyncio.sleep(0.5)

        by_id = await snapshot_by_id(cdp)
        if "addToClemzListDelete" in by_id:
            coche = (await get_live_props(cdp, by_id["addToClemzListDelete"]["node_id"], props=("checked",))).get("checked")
            if not coche:
                await click_element(cdp, by_id["addToClemzListDelete"]["node_id"])
                await asyncio.sleep(0.3)
        else:
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Option « Ajouter à une liste clemz.app » introuvable.")
        if not await self._visible(cdp, by_id, "deleteButton"):
            raise _SerieInterrompue("Bouton « Supprimer les annonce(s) » non affiché — rien supprimé")

        await self._pas(f"Étape 0 — cliquer sur « 💣 Supprimer les annonce(s) » ({len(a_supprimer)})")
        await click_element(cdp, by_id["deleteButton"]["node_id"])
        await self._choisir_liste_clemz(cdp)
        await self._confirmer_suppression(cdp, page, len(a_supprimer))

        termines = []

        async def _noter(nom):
            termines.append(nom)

        poll = await self.bot._poll_mass_action(page, cdp, self.acc, len(a_supprimer),
                                                action_label="Suppression", on_article_termine=_noter)
        by_id = await snapshot_by_id(cdp)
        texte_fin = ""
        for cle in ("processStage", "progressContent"):
            if cle in by_id:
                texte_fin += " " + (await get_live_props(cdp, by_id[cle]["node_id"], props=("innerText",))).get("innerText", "")
        non_supprimes = re.search(r"(\d+)\s+articles?\s+non\s+supprim", texte_fin.lower())
        if poll["status"] != "success" or (non_supprimes and int(non_supprimes.group(1)) > 0):
            # État incertain : certaines annonces peuvent déjà être supprimées.
            self.supprimes = termines
            if termines:  # possiblement déjà supprimés : protégés du marquage "vendu"
                from services.articles_supprimes import proteger
                proteger(self.dressing, termines)
            raison = poll.get("reason") or f"{non_supprimes.group(1)} annonce(s) non supprimée(s) par Clemz"
            raise _SerieInterrompue(f"Suppression incomplète : {raison} — vérifie le dressing Vinted")
        if "progressDressingButton" in by_id:
            await click_element(cdp, by_id["progressDressingButton"]["node_id"])
            await asyncio.sleep(0.5)

        self.supprimes = list(a_supprimer)
        # Protégés du marquage "vendu" par le scraping jusqu'à leur
        # republication (cf. services/articles_supprimes.py).
        from services.articles_supprimes import proteger
        proteger(self.dressing, a_supprimer)
        logger.info(f"💣 [AUTO-COMPLÉTION {self.dressing}] {len(a_supprimer)} annonce(s) supprimée(s) de Vinted : {a_supprimer}")

        # Clemz vide lui-même la liste à traiter à la fin de la suppression
        # (constaté le 19/10/2026) : simple contrôle, sans action ni pause.
        reste = await self._nb_liste_a_traiter(page.context)
        if reste:
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] {reste} article(s) encore dans la liste à traiter "
                           f"après la suppression (Clemz ne l'a pas vidée).")
        return a_supprimer

    # ------------------------------------------------------------------ #
    # MODE "VALIDATION MANUELLE" DE CLEMZ (20/10/2026)
    # ------------------------------------------------------------------ #
    async def _ouvrir_reglages_protection(self, cdp):
        by_id = await snapshot_by_id(cdp)
        if "openProtectionSettings" not in by_id:
            raise _SerieInterrompue("Bouton ⚙ « Protection » du panneau Republier introuvable")
        await click_element(cdp, by_id["openProtectionSettings"]["node_id"])
        for _ in range(20):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if "executionModeSelect" in by_id and "protectionSettingsSave" in by_id:
                return by_id
        raise _SerieInterrompue("Fenêtre « Protection » de Clemz non ouverte")

    async def _appliquer_reglages_protection(self, cdp, reglages, libelle):
        """Ouvre la fenêtre Protection, applique {select_id: valeur}, vérifie,
        enregistre. Retourne les valeurs AVANT modification."""
        by_id = await self._ouvrir_reglages_protection(cdp)
        avant = {}
        for select_id, valeur in reglages.items():
            by_id = await snapshot_by_id(cdp)  # les sections s'affichent selon les choix précédents
            if select_id not in by_id:
                raise _SerieInterrompue(f"Réglage « {select_id} » introuvable dans la fenêtre Protection")
            node = by_id[select_id]["node_id"]
            avant[select_id] = (await get_live_props(cdp, node, props=("value",))).get("value")
            await set_select_value(cdp, node, valeur)
            await asyncio.sleep(0.3)
            lu = (await get_live_props(cdp, node, props=("value",))).get("value")
            if lu != valeur:
                raise _SerieInterrompue(f"Réglage « {select_id} » resté sur {lu!r} au lieu de {valeur!r}")
        logger.info(f"🛡️ [AUTO-COMPLÉTION {self.dressing}] Protection Clemz ({libelle}) : {avant} -> {reglages}")
        await self._pas(f"Validation manuelle — enregistrer les réglages Protection ({libelle})")
        by_id = await snapshot_by_id(cdp)
        await click_element(cdp, by_id["protectionSettingsSave"]["node_id"])
        await asyncio.sleep(1)
        return avant

    async def _bouton_ajouter_iframe(self, page):
        """Bouton "Ajouter" du formulaire Vinted rempli par Clemz DANS UNE
        IFRAME de la page (validation manuelle). Retourne (frame, élément) ou
        (None, None)."""
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                bouton = await frame.query_selector('[data-testid="upload-form-save-button"]')
            except Exception:
                continue
            if bouton:
                return frame, bouton
        return None, None

    def _nom_de_la_serie(self, titre, restants):
        """Rapproche le titre affiché par Clemz (#articleTitle) d'un article
        de la série."""
        correspondants = [n for n in restants if _correspond(titre, n)]
        return correspondants[0] if len(correspondants) == 1 else None

    async def _enregistrer_publie(self, nom):
        await self.bot._article_termine(self.acc, nom, "republication")
        from services.articles_supprimes import retirer
        retirer(nom)
        self.etat["republies"].append(nom)
        logger.info(f"✅ [AUTO-COMPLÉTION {self.dressing}] « {nom} » republié (validation manuelle) et enregistré.")

    # --- Baisse de prix à la republication (20/10/2026) --------------------
    # Clemz traitant UN article par "Go Clemz !" en validation manuelle, son
    # option "Prix" (modifier à la republication) est réglée avant chaque Go
    # Clemz pour l'article qui va passer : prix FIXÉ (mode "set", en €) s'il
    # a un prix_republication, option désactivée sinon -- une liste peut
    # mélanger articles à baisser et articles à republier tels quels.

    async def _prochain_article_clemz(self, context, restants):
        """Article que Clemz traitera au prochain "Go Clemz !" : première
        entrée de CE dressing dans la liste à traiter (mainList). Retourne
        (nom dans la série ou None, titre lu)."""
        stockage = await self._stockage_clemz(context, ["mainList"])
        if stockage is None:
            return None, None
        entrees = [e for e in (stockage.get("mainList") or []) if str(e.get("wardrobe_id")) == self.membre_vinted]
        if not entrees:
            return None, None
        titre = entrees[0].get("title") or entrees[0].get("titre") or ""
        if not titre:
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Entrée de liste Clemz sans titre, clés : {sorted(entrees[0])}")
        logger.info(f"📋 [AUTO-COMPLÉTION {self.dressing}] Ordre de la liste Clemz : "
                    f"{[e.get('title') for e in entrees]}")
        return self._nom_de_la_serie(titre, restants), titre

    async def _regler_prix_republication(self, cdp, context, prix):
        """Option "Prix" de la fenêtre "Modifier à la republication" : prix
        fixé à `prix` € sans arrondi, ou désactivée (prix=None). Rien n'est
        touché si Clemz est déjà réglé ainsi ; sinon vérifié dans son
        stockage après enregistrement."""
        actif = prix is not None
        libelle = f"prix fixé à {prix:g} €" if actif else "prix inchangé"

        def conforme(lu):
            return bool(lu.get("repostModifyPrice")) == actif and (not actif or (
                lu.get("repostModifyPriceDirection") == "set" and lu.get("repostModifyPriceType") == "€"
                and abs(float(lu.get("repostModifyPriceValue") or 0) - prix) < 0.005))

        lu = await self._stockage_clemz(context, list(CLES_PRIX_REPUBLICATION))
        if lu is not None and conforme(lu):
            logger.info(f"💶 [AUTO-COMPLÉTION {self.dressing}] Option « Prix » de Clemz déjà réglée ({libelle}).")
            return

        by_id = await snapshot_by_id(cdp)
        if "openModifyOnRepost" not in by_id:
            raise _SerieInterrompue("Bouton « Modifier à la republication » introuvable dans le panneau Republier")
        await self._pas(f"Validation manuelle — ouvrir « Modifier à la republication » ({libelle})")
        await click_element(cdp, by_id["openModifyOnRepost"]["node_id"])
        for _ in range(20):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if "repostModifyPrice" in by_id and "modifyOnRepostSave" in by_id:
                break
        else:
            raise _SerieInterrompue("Fenêtre « Modifier à la republication » de Clemz non ouverte")

        case = by_id["repostModifyPrice"]["node_id"]
        if bool((await get_live_props(cdp, case, props=("checked",))).get("checked")) != actif:
            await click_element(cdp, case)
        if actif:
            await set_select_value(cdp, by_id["repostModifyPriceDirection"]["node_id"], "set")
            await set_input_value(cdp, by_id["repostModifyPriceValue"]["node_id"], f"{prix:g}")
            await set_select_value(cdp, by_id["repostModifyPriceType"]["node_id"], "€")
            await set_select_value(cdp, by_id["repostModifyPriceRound"]["node_id"], "noRound")
        await asyncio.sleep(0.3)
        await self._pas(f"Validation manuelle — enregistrer l'option « Prix » ({libelle})")
        by_id = await snapshot_by_id(cdp)
        await click_element(cdp, by_id["modifyOnRepostSave"]["node_id"])
        await asyncio.sleep(1)

        lu = await self._stockage_clemz(context, list(CLES_PRIX_REPUBLICATION))
        if lu is not None and not conforme(lu):
            # Valeur refusée par Clemz (avertissement) : fenêtre restée ouverte
            raise _SerieInterrompue(f"Option « Prix » de Clemz non enregistrée ({libelle}) — lu : {lu}")
        logger.info(f"💶 [AUTO-COMPLÉTION {self.dressing}] Option « Prix » de Clemz réglée : {libelle}.")

    async def _ecrire_protection_stockage(self, context, reglages):
        """Réglages Protection écrits directement dans le stockage de Clemz,
        sous les clés qu'utilise son bouton "Enregistrer" (clemz.js :
        repostPacingMode / repostPacingHumanPreset / repostExecutionMode)."""
        cles = {"pacingModeSelect": "repostPacingMode", "pacingHumanPresetSelect": "repostPacingHumanPreset",
                "executionModeSelect": "repostExecutionMode"}
        a_ecrire = {cles[k]: v for k, v in reglages.items() if k in cles}
        for sw in context.service_workers:
            if sw.url.startswith("chrome-extension://") and "clemz_background" in sw.url:
                await sw.evaluate("(a) => chrome.storage.local.set(a)", a_ecrire)
                lu = await sw.evaluate("(c) => chrome.storage.local.get(c)", list(a_ecrire))
                if lu != a_ecrire:
                    raise RuntimeError(f"relu {lu} au lieu de {a_ecrire}")
                logger.info(f"🛡️ [AUTO-COMPLÉTION {self.dressing}] Protection Clemz restaurée (stockage) : {a_ecrire}")
                return
        raise RuntimeError("service worker Clemz introuvable")

    async def _desactiver_prix_republication(self, context):
        """Fin de série : option "Prix" TOUJOURS désactivée dans le stockage de
        Clemz. (Jusqu'au 21/10/2026 elle était "restaurée" à son état de
        départ : une série coupée à 16h50 avait laissé "fixer à 14,08 €"
        actif, que les séries suivantes ont pris pour l'état d'origine et
        remis en place -- un prix fixe resté actif s'appliquerait à TOUTE
        republication de masse suivante.)"""
        for sw in context.service_workers:
            if sw.url.startswith("chrome-extension://") and "clemz_background" in sw.url:
                await sw.evaluate("() => chrome.storage.local.set({repostModifyPrice: false})")
                logger.info(f"💶 [AUTO-COMPLÉTION {self.dressing}] Option « Prix » de Clemz désactivée (fin de série).")
                return
        raise RuntimeError("service worker Clemz introuvable")

    @staticmethod
    async def _prix_formulaire(frame):
        """Prix affiché dans le formulaire Vinted (iframe Clemz), ou None."""
        try:
            champ = await frame.query_selector('input[data-testid="price-input--input"]')
            valeur = await champ.input_value() if champ else ""
        except Exception:
            return None
        m = re.search(r"\d+(?:[.,]\d{1,2})?", (valeur or "").replace(" ", "").replace(" ", ""))
        return float(m.group(0).replace(",", ".")) if m else None

    def _enregistrer_baisse(self, nom, prix_apres):
        """Après publication confirmée : nouveau prix dans articles.prix_vente
        (pas de nouvelle baisse proposée avant le prochain scraping) + trace
        price_drops, comme les baisses de l'ancien script."""
        from database import supabase as db_client
        article = self.bot._article_unique(db_client, nom)
        prix_avant = float(article.get("prix_vente") or 0) if article else None
        if article:
            db_client.table("articles").update({"prix_vente": prix_apres}).eq("id", article["id"]).execute()
        try:
            db_client.table("price_drops").insert({
                "article_id": article["id"] if article else None,
                "nom": nom,
                "dressing": self.acc["name"],
                "prix_avant": prix_avant,
                "pourcentage_applique": round((1 - prix_apres / prix_avant) * 100, 1) if prix_avant else None,
                "prix_apres_estime": prix_apres,
            }).execute()
        except Exception as e:
            logger.warning(f"⚠️ Impossible d'enregistrer price_drops pour '{nom}' : {e}")
        logger.info(f"📉 [AUTO-COMPLÉTION {self.dressing}] « {nom} » : {prix_avant} € -> {prix_apres} € "
                    f"(baisse à la republication) enregistré.")

    async def _serie_validation_manuelle(self, cdp, page):
        """Liste remplie par le Smart Dressing -> réglages Protection
        (Rythme naturel très lent + Validation manuelle) -> Republier -> Go
        Clemz -> Confirmer/Continuer -> pour chaque article : formulaire
        rempli par Clemz dans une iframe, pause 1-3 s, clic sur "Ajouter" ->
        Clemz enchaîne lui-même l'article suivant. Réglages d'origine
        restaurés à la fin."""
        self._maj(statut="preparation", message="Validation manuelle — liste par le Smart Dressing")
        prets = await self._construire_liste_smart_dressing(cdp, page, "validation manuelle")
        if not prets:
            raise _SerieInterrompue("Aucun article trouvé par le Smart Dressing")
        restants = list(prets)
        self._maj(total=len(prets))

        # Le Smart Dressing peut recharger la page Vinted : panneau à réinjecter
        # / rouvrir avant de continuer (essai du 20/10/2026 : "Voir mes listes"
        # introuvable juste après le remplissage, série arrêtée).
        await self._attendre_panneau(cdp, page)

        # Clemz republie TOUTE la liste à traiter : elle doit contenir
        # exactement les articles de la série, rien de plus.
        nb_liste = await self._nb_liste_a_traiter(page.context)
        if nb_liste is not None and nb_liste != len(prets):
            raise _SerieInterrompue(f"Liste à traiter incohérente ({nb_liste} article(s) au lieu de {len(prets)}) "
                                    f"— rien republié")

        await self._pas("Validation manuelle — onglet « Republier » du panneau Clemz")
        nav = await self.bot._navigate_to_repost_tab(cdp, page, self.acc)
        if nav["status"] != "success":
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Onglet Republier non atteint ({nav['reason']}) "
                           f"— rechargement de la page et nouvelle tentative.")
            await page.goto(self.acc["url"], wait_until="domcontentloaded")
            if not await self._attendre_panneau(cdp, page):
                raise _SerieInterrompue("Panneau Clemz introuvable après rechargement")
            nav = await self.bot._navigate_to_repost_tab(cdp, page, self.acc)
            if nav["status"] != "success":
                raise _SerieInterrompue(nav["reason"])

        # Option "Prix" à la republication : réglée avant CHAQUE article, même
        # sans baisse prévue (une baisse laissée active dans Clemz
        # s'appliquerait sinon à tous), et désactivée en fin de série.
        prix_depart = await self._stockage_clemz(page.context, list(CLES_PRIX_REPUBLICATION))
        if prix_depart is None:
            raise _SerieInterrompue("Stockage de Clemz illisible — option « Prix » à la republication non maîtrisable")
        if prix_depart.get("repostModifyPrice"):
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Option « Prix » de Clemz trouvée ACTIVE au départ "
                           f"({prix_depart}) — reste d'une série interrompue ? Elle sera réglée article par article "
                           f"puis désactivée en fin de série.")
        reglages_origine = await self._appliquer_reglages_protection(cdp, REGLAGES_VALIDATION_MANUELLE, "validation manuelle")
        try:
            # En validation manuelle, Clemz traite UN article par "Go Clemz !"
            # puis recharge la page (constaté le 20/10/2026) : on relance donc
            # "Go Clemz !" pour chaque article, jusqu'à liste vide.
            premier = True
            while restants:
                if arret_demande(self.dressing):
                    raise _SerieInterrompue("Arrêtée à ta demande")
                if not premier:
                    await self._attendre_panneau(cdp, page)
                    nav = await self.bot._navigate_to_repost_tab(cdp, page, self.acc)
                    if nav["status"] != "success":
                        raise _SerieInterrompue(nav["reason"])
                    # Clemz retire l'article de la liste à la passation : il
                    # doit rester exactement les articles non encore publiés.
                    nb_liste = await self._nb_liste_a_traiter(page.context)
                    if nb_liste is not None and nb_liste != len(restants):
                        raise _SerieInterrompue(f"Liste à traiter incohérente ({nb_liste} article(s) au lieu de "
                                                f"{len(restants)}) — série arrêtée")
                premier = False

                # Prix à la republication réglé pour l'article qui va passer
                nom_prevu, titre_prevu = await self._prochain_article_clemz(page.context, restants)
                if nom_prevu is None:
                    raise _SerieInterrompue(f"Prochain article de la liste Clemz non reconnu (« {titre_prevu} ») — "
                                            f"prix à la republication impossible à régler, série arrêtée")
                prix_regle = self.prix_par_nom.get(nom_prevu)
                await self._regler_prix_republication(cdp, page.context, prix_regle)

                by_id = await snapshot_by_id(cdp)
                if "repostSelectAction" in by_id:
                    await set_select_value(cdp, by_id["repostSelectAction"]["node_id"], "1")  # Republier
                if "repostButton" not in by_id:
                    raise _SerieInterrompue("Bouton « Go Clemz ! » introuvable")
                self._maj(index=len(self.etat["republies"]) + 1)
                await self._pas(f"Validation manuelle — « Go Clemz ! » ({len(restants)} article(s) restant(s))")
                await click_element(cdp, by_id["repostButton"]["node_id"])
                self._maj(statut="en_cours", message="Validation manuelle — Clemz démarre")

                try:
                    titre = await self._un_article_validation_manuelle(cdp, page, restants, prix_regle)
                except _ErreurClemz as e:
                    await self._signaler_erreur_clemz(nom_prevu, str(e))
                    raise _SerieInterrompue(f"Erreur Clemz sur « {nom_prevu} » : {e} — série arrêtée")
                nom = self._nom_de_la_serie(titre, restants)
                if not nom:
                    raise _SerieInterrompue(f"« {titre} » publié par Clemz mais absent de la série — à vérifier, série arrêtée")
                restants.remove(nom)
                await self._enregistrer_publie(nom)
                if prix_regle is not None:
                    await asyncio.to_thread(self._enregistrer_baisse, nom, prix_regle)
                self._maj(article=None, message="Finalisation par Clemz")
                await self._attendre_finalisation_validation_manuelle(page.context)
        finally:
            # Réglages Clemz d'origine remis, quoi qu'il arrive (arrêt, erreur...)
            restaures = False
            a_remettre = {k: reglages_origine[k] for k in REGLAGES_VALIDATION_MANUELLE if reglages_origine.get(k)}
            try:
                if not page.is_closed():
                    await self._attendre_panneau(cdp, page)
                    nav = await self.bot._navigate_to_repost_tab(cdp, page, self.acc)
                    if nav["status"] == "success":
                        await self._appliquer_reglages_protection(cdp, a_remettre, "restauration")
                        restaures = True
                    else:
                        logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Onglet Republier inaccessible pour "
                                       f"restaurer la Protection ({nav['reason']}) — écriture directe dans Clemz.")
            except Exception as e:
                logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Restauration Protection par le panneau "
                               f"impossible ({e}) — écriture directe dans Clemz.")
            if not restaures:
                # Secours (22/10/2026) : après l'erreur Clemz de 22h20, le
                # panneau était perdu et la restauration a été sautée SANS
                # message -- D1 restait en validation manuelle "très lent".
                try:
                    await self._ecrire_protection_stockage(page.context, a_remettre)
                except Exception as e:
                    logger.error(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Réglages Protection NON restaurés ({e}) — "
                                 f"remets le mode « Standard » à la main dans Clemz : {reglages_origine}")
                    await asyncio.to_thread(_send_notification, "⚠️ VintedPro — Clemz à vérifier",
                                            f"{self.dressing} : Protection Clemz restée en validation manuelle.")
            try:
                await self._desactiver_prix_republication(page.context)
            except Exception as e:
                logger.error(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Option « Prix » à la republication NON désactivée "
                             f"({e}) — DÉSACTIVE-LA À LA MAIN dans Clemz (⚙ « Modifier en republiant »), sinon "
                             f"le prix fixé s'appliquera à la prochaine republication de masse.")
                await asyncio.to_thread(_send_notification, "⚠️ VintedPro — Clemz à vérifier",
                                        f"{self.dressing} : option « Prix » de Clemz peut-être encore active.")

    async def _attente_validation_manuelle(self, context):
        """pendingManualValidationDeclare de Clemz : posé à la passation
        (clicked=""), passé à clicked="active" au clic sur "Ajouter", effacé
        une fois la nouvelle annonce retrouvée et liée à la sauvegarde.
        Retourne le dict, {} s'il est absent, None si le stockage est illisible."""
        stockage = await self._stockage_clemz(context, ["pendingManualValidationDeclare"])
        if stockage is None:
            return None
        return stockage.get("pendingManualValidationDeclare") or {}

    async def _un_article_validation_manuelle(self, cdp, page, restants, prix_regle):
        """Après "Go Clemz !" : fenêtres de confirmation, remplissage par Clemz,
        passation ("Enregistre en brouillon ou publie"), pause 1-3 s, clic sur
        "Ajouter" dans l'iframe, puis CONFIRMATION de la publication par le
        stockage de Clemz (clicked="active"). Retourne le titre publié.
        prix_regle : prix fixé dans l'option "Prix" de Clemz (None = inchangé)
        -- "Ajouter" n'est cliqué que si l'article ouvert est bien celui prévu
        pour ce réglage et, en cas de baisse, si le formulaire affiche ce prix."""
        inactif = 0
        dernier_etat = None
        titre_clique = None
        while inactif < ATTENTE_SANS_PROGRES_VALIDATION_SECONDES:
            if page.is_closed():
                raise _SerieInterrompue("Fenêtre fermée")
            if arret_demande(self.dressing) and not titre_clique:
                by_id = await snapshot_by_id(cdp)
                if "progressStopButton" in by_id:
                    x, y = await get_center_coords(cdp, by_id["progressStopButton"]["node_id"])
                    await real_mouse_click(page, x, y)
                raise _SerieInterrompue("Arrêtée à ta demande")

            # Publication confirmée par Clemz (clic "Ajouter" bien pris en compte)
            if titre_clique:
                attente = await self._attente_validation_manuelle(page.context)
                if attente is None or attente.get("clicked") == "active" or attente == {}:
                    logger.info(f"🟢 [AUTO-COMPLÉTION {self.dressing}] Publication confirmée par Clemz : « {titre_clique} » "
                                f"({'stockage illisible' if attente is None else attente.get('clicked') or 'finalisée'}).")
                    return titre_clique

            try:
                by_id = await snapshot_by_id(cdp)
            except Exception:
                await asyncio.sleep(2)  # page en cours de rechargement
                inactif += 2
                continue

            # Fenêtres de confirmation Clemz ("Confirmer", "Continuer", infos...)
            if not titre_clique and "continueAlertButton" in by_id and await self._visible(cdp, by_id, "continueAlertButton"):
                contenu = by_id.get("alert-content")
                texte = ((await get_live_props(cdp, contenu["node_id"], props=("innerText",))).get("innerText", "")
                         if contenu else "").strip()
                logger.info(f"🪟 [AUTO-COMPLÉTION {self.dressing}] Fenêtre Clemz : {texte.replace(chr(10), ' · ')[:300]}")
                await self._pas(f"Validation manuelle — confirmer la fenêtre Clemz : « {texte[:120]} »")
                by_id = await snapshot_by_id(cdp)
                if "continueAlertButton" in by_id:
                    x, y = await get_center_coords(cdp, by_id["continueAlertButton"]["node_id"])
                    await real_mouse_click(page, x, y)
                inactif = 0
                await asyncio.sleep(1)
                continue

            etape = progression = titre = ""
            for cle in ("processStage", "progressContent", "articleTitle"):
                if cle in by_id:
                    valeur = (await get_live_props(cdp, by_id[cle]["node_id"], props=("innerText",))).get("innerText", "").strip()
                    if cle == "processStage":
                        etape = valeur
                    elif cle == "progressContent":
                        progression = valeur
                    else:
                        titre = valeur
            etat = (etape, progression)
            if etat != dernier_etat:
                dernier_etat, inactif = etat, 0
                if progression:
                    logger.info(f"⏳ [AUTO-COMPLÉTION {self.dressing}] Clemz : {progression.replace(chr(10), ' · ')[:200]}")
                self._maj(message=f"Validation manuelle — {etape[:120] or 'en cours'}", article=titre or None)

            # Erreur Clemz ("XXX ERREUR XXX · Action non autorisée", 21/10/2026) :
            # Clemz ne va pas plus loin -- inutile d'attendre la fermeture.
            if not titre_clique and MARQUEUR_ERREUR_CLEMZ in f"{titre} {progression}".lower():
                motif = etape if etape and MARQUEUR_ERREUR_CLEMZ not in etape.lower() else progression
                motif = motif.replace("\n", " · ").strip()[:200] or "erreur sans détail"
                # "Action non autorisée" = refus du SERVEUR clemz.app (création
                # de l'"activité" avant toute action sur Vinted, cf. clemz.js
                # apiCalls_notAuthorized) ; le vrai motif renvoyé par Clemz est
                # dans globalUiMessage (22/10/2026 : non relevé jusqu'ici).
                stockage = await self._stockage_clemz(page.context, ["globalUiMessage"]) or {}
                detail = (stockage.get("globalUiMessage") or {}).get("content") or ""
                detail = re.sub(r"<[^>]+>", " ", str(detail))
                detail = re.sub(r"\s+", " ", detail).strip()
                logger.error(f"🔬 [AUTO-COMPLÉTION {self.dressing}] Message du serveur Clemz : "
                             f"{detail[:400] or '(aucun)'} — brut : {stockage.get('globalUiMessage')}")
                raise _ErreurClemz(f"{motif} — {detail[:150]}" if detail else motif)

            # Passation : formulaire rempli dans l'iframe -> clic sur "Ajouter"
            if not titre_clique and "enregistre en brouillon ou publie" in etape.lower() and titre:
                frame, bouton = await self._bouton_ajouter_iframe(page)
                if bouton:
                    await self._verifier_prix_avant_ajouter(frame, titre, restants, prix_regle)
                    pause = random.uniform(*PAUSE_AVANT_AJOUTER_SECONDES)
                    await asyncio.sleep(pause)
                    await self._pas(f"Validation manuelle — cliquer sur « Ajouter » pour « {titre[:60]} »")
                    await bouton.click()
                    titre_clique = titre
                    inactif = 0
                    logger.info(f"🖱️ [AUTO-COMPLÉTION {self.dressing}] « Ajouter » cliqué pour « {titre} » (après {pause:.1f} s).")
                    self._maj(message="Validation manuelle — publication en cours")

            await asyncio.sleep(2)
            inactif += 2
        raise _SerieInterrompue(f"Aucun progrès de Clemz depuis {ATTENTE_SANS_PROGRES_VALIDATION_SECONDES // 60} min "
                                f"(dernier état : {dernier_etat})")

    async def _signaler_erreur_clemz(self, nom, motif):
        """"XXX ERREUR XXX" de Clemz sur `nom` : l'ancienne annonce a pu être
        supprimée sans que la nouvelle soit créée (3 articles disparus de D1
        le 21/10/2026). Article marqué erreur_clemz en base -- jamais marqué
        "vendu" par le scraping tant qu'il est absent, drapeau effacé tout
        seul quand il réapparaît -- + historique + notification Windows."""
        raison = (f"Erreur Clemz « {motif} » pendant la republication (validation manuelle) — "
                  f"vérifier sur Vinted que l'annonce existe encore, sinon republier depuis clemz.app.")
        logger.error(f"🛑 [AUTO-COMPLÉTION {self.dressing}] « {nom} » : {raison}")

        def _marquer():
            from database import supabase as db_client
            from datetime import datetime as dt
            db_client.table("articles").update({
                "erreur_clemz": True,
                "erreur_clemz_reason": raison,
                "erreur_clemz_date": dt.now().isoformat(),
            }).ilike("nom", nom).execute()
        try:
            await asyncio.to_thread(_marquer)
        except Exception as e:
            logger.error(f"❌ [AUTO-COMPLÉTION {self.dressing}] Échec du marquage erreur Clemz pour « {nom} » : {e}")
        self.etat["ignores"].append({"nom": nom, "raison": raison})
        if self.task_id:
            from services.automation_scheduler import update_task_result_par_nom
            await asyncio.to_thread(update_task_result_par_nom, self.task_id, nom, "failed", raison)
        await asyncio.to_thread(_send_notification, "🛑 VintedPro — Erreur Clemz",
                                f"{self.dressing} : « {nom[:60]} » — {motif[:80]}. Série arrêtée, vérifie l'annonce.")

    async def _verifier_prix_avant_ajouter(self, frame, titre, restants, prix_regle):
        """Garde-fou avant "Ajouter" : jamais de publication avec la baisse
        d'un autre article, ni avec un prix différent de celui prévu. En cas
        d'écart, la série s'arrête SANS cliquer (formulaire laissé ouvert)."""
        nom = self._nom_de_la_serie(titre, restants)
        attendu = self.prix_par_nom.get(nom) if nom else None
        probleme = None
        if nom is None:
            probleme = f"« {titre} » ouvert par Clemz, absent de la série"
        elif attendu != prix_regle:
            probleme = (f"« {nom} » ouvert par Clemz, mais l'option Prix était réglée pour un autre article "
                        f"({'prix inchangé' if prix_regle is None else f'{prix_regle:g} €'} au lieu de "
                        f"{'prix inchangé' if attendu is None else f'{attendu:g} €'})")
        elif prix_regle is not None:
            prix_form = await self._prix_formulaire(frame)
            if prix_form is None or abs(prix_form - prix_regle) > 0.005:
                probleme = f"« {nom} » : le formulaire affiche {prix_form} € au lieu de {prix_regle:g} €"
            else:
                logger.info(f"💶 [AUTO-COMPLÉTION {self.dressing}] « {nom} » : prix du formulaire conforme ({prix_form:g} €).")
        if probleme:
            await asyncio.to_thread(_send_notification, "⚠️ VintedPro — Prix à vérifier",
                                    f"{self.dressing} : « Ajouter » non cliqué. {probleme[:150]}")
            raise _SerieInterrompue(f"{probleme} — « Ajouter » NON cliqué, formulaire laissé ouvert, série arrêtée")

    async def _attendre_finalisation_validation_manuelle(self, context):
        """Clemz retrouve la nouvelle annonce et la lie à la sauvegarde, puis
        efface pendingManualValidationDeclare -- attendu avant de relancer."""
        for _ in range(ATTENTE_FINALISATION_CLEMZ_SECONDES // 2):
            attente = await self._attente_validation_manuelle(context)
            if attente is None:
                await asyncio.sleep(10)
                return
            if not attente:
                return
            await asyncio.sleep(2)
        logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Clemz n'a pas finalisé la publication en "
                       f"{ATTENTE_FINALISATION_CLEMZ_SECONDES}s — on continue.")

    async def _attendre_panneau(self, cdp, page):
        for _ in range(30):
            by_id = await snapshot_by_id(cdp)
            if "miniVinz" in by_id and "toolBody" in by_id:
                break
            await asyncio.sleep(1)
        else:
            return False
        return await ensure_panel_open(cdp, page)

    async def _visible(self, cdp, by_id, element_id):
        return element_id in by_id and await is_element_visible(cdp, by_id[element_id]["node_id"])

    async def _fermer_selecteur(self, cdp, modale):
        fermer = await cdp.send("DOM.querySelector", {"nodeId": modale, "selector": ".close-modal"})
        if fermer.get("nodeId"):
            await click_element(cdp, fermer["nodeId"])

    async def _choisir_premier_article(self, cdp, restants):
        """Ouvre "Choisir dans ma liste à traiter" et choisit le PREMIER
        article de la liste (scénario du 18/10/2026 -- Clemz retire l'article
        de la liste au remplissage, le suivant devient alors le premier).

        restants : noms de la session pas encore traités -- le premier article
        doit en faire partie (sinon c'est un article étranger à la session :
        on ne republie rien). Retourne (nom, None), (None, None) si la liste
        est vide, ou (None, raison)."""
        by_id = await snapshot_by_id(cdp)
        if "autoCompleteChooseItem" not in by_id:
            return None, "Bouton 'Choisir dans ma liste à traiter' introuvable"
        await self._pas("Étape 2 — cliquer sur « Choisir dans ma liste à traiter »")
        await click_element(cdp, by_id["autoCompleteChooseItem"]["node_id"])

        modale = None
        for _ in range(20):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if "autoCompletePickModal" in by_id:
                modale = by_id["autoCompletePickModal"]["node_id"]
                break
        if modale is None:
            return None, "Sélecteur d'articles Clemz non ouvert"

        res = await cdp.send("DOM.querySelectorAll", {"nodeId": modale, "selector": ".autoCompletePickItem"})
        items = res.get("nodeIds", [])
        if not items:
            await self._fermer_selecteur(cdp, modale)
            return None, None  # liste à traiter vide : série terminée
        liste = []
        for node_id in items:
            attrs = (await cdp.send("DOM.getAttributes", {"nodeId": node_id})).get("attributes", [])
            attrs = dict(zip(attrs[::2], attrs[1::2]))
            titre_item = (await get_live_props(cdp, node_id, props=("innerText",))).get("innerText", "").strip()
            liste.append((node_id, attrs.get("data-clemz-id"), titre_item))
        logger.info(f"📋 [AUTO-COMPLÉTION {self.dressing}] Liste à traiter ({len(liste)}) : "
                    f"{[f'#{cid} {t}' for _, cid, t in liste]}")
        premier, clemz_id, titre = liste[0]

        # Identifiant clemz.app d'abord (relevé à l'étape 1), titre tronqué à défaut
        nom = self.nom_par_clemz_id.get(str(clemz_id))
        if nom not in restants:
            correspondants = [n for n in restants if _correspond(titre, n)]
            if len(correspondants) != 1:
                await self._fermer_selecteur(cdp, modale)
                if not correspondants:
                    return None, (f"Premier article de la liste à traiter (#{clemz_id} « {titre} ») absent "
                                  f"de la série lancée depuis le dashboard — rien n'est republié")
                return None, f"« {titre} » correspond à {len(correspondants)} articles de la série — choix ambigu"
            nom = correspondants[0]

        await self._pas(f"Étape 2 — choisir le premier article de la liste : « {titre[:60]} »")
        await click_element(cdp, premier)
        # Choix pris en compte : encart de l'article affiché ET bouton
        # "Remplir le formulaire" activé (désactivé tant que rien n'est choisi).
        for _ in range(20):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if await self._visible(cdp, by_id, "autoCompleteSlot") and "autoCompleteStartButton" in by_id:
                desactive = (await get_live_props(cdp, by_id["autoCompleteStartButton"]["node_id"], props=("disabled",))).get("disabled")
                if not desactive:
                    return nom, None
        return None, f"« {titre} » choisi mais non pris en compte par Clemz (bouton Remplir resté désactivé)"

    async def _titre_fenetre_confirmation(self, cdp, by_id):
        """Titre de l'article affiché dans la fenêtre de confirmation Clemz
        (#alert-content : '<span class="font-weight-bolder text-dark">titre</span>'
        sous la vignette), ou None s'il est illisible."""
        contenu = by_id.get("alert-content")
        if not contenu:
            return None
        noeud = await query_selector_in(cdp, contenu["node_id"], "span.font-weight-bolder")
        if not noeud:
            return None
        return (await get_live_props(cdp, noeud, props=("innerText",))).get("innerText", "").strip() or None

    async def _lancer_remplissage(self, cdp, page, sans_confirmer=False, nom_attendu=None):
        """Option "nouvel article" décochée (republication liée à la sauvegarde),
        clic "Remplir le formulaire" (SANS pause avant, demandé le 18/10/2026),
        confirmation de l'import, puis attente du message "Formulaire rempli".
        sans_confirmer : s'arrête dès que la fenêtre de confirmation Clemz est
        affichée, sans la valider (préparation seule : valider coûte 1
        republication). Retourne None si OK, sinon la raison."""
        by_id = await snapshot_by_id(cdp)
        if "autoCompleteAsNewItem" in by_id:
            coche = (await get_live_props(cdp, by_id["autoCompleteAsNewItem"]["node_id"], props=("checked",))).get("checked")
            if coche:
                await click_element(cdp, by_id["autoCompleteAsNewItem"]["node_id"])
                await asyncio.sleep(0.3)
        if "autoCompleteStartButton" not in by_id:
            return "Bouton 'Remplir le formulaire' introuvable"
        logger.info(f"📝 [AUTO-COMPLÉTION {self.dressing}] Clic sur « Remplir le formulaire ».")
        await click_element(cdp, by_id["autoCompleteStartButton"]["node_id"])

        # Modale de confirmation d'import ("coûte 1 republication") -- on laisse
        # Clemz terminer sa vérification de doublon avant de confirmer
        # (jusqu'à 60 s : la vérification interroge Vinted et peut être lente).
        confirme = False
        for _ in range(120):
            await asyncio.sleep(0.5)
            by_id = await snapshot_by_id(cdp)
            if "continueAlertButton" in by_id and await self._visible(cdp, by_id, "continueAlertButton"):
                zone = by_id.get("autoCompleteDuplicateZone")
                if zone:
                    texte = (await get_live_props(cdp, zone["node_id"], props=("innerText",))).get("innerText", "")
                    if "rification" in texte:  # "Vérification présence doublon" encore en cours
                        continue
                    if texte.strip():
                        logger.info(f"🔎 [AUTO-COMPLÉTION {self.dressing}] Contrôle doublon Clemz : {texte.strip()[:200]}")
                # Contrôle : la fenêtre doit importer L'article attendu -- sinon
                # "Annuler" (aucune republication Clemz consommée).
                titre = await self._titre_fenetre_confirmation(cdp, by_id)
                logger.info(f"🔎 [AUTO-COMPLÉTION {self.dressing}] Fenêtre de confirmation Clemz : « {titre} »")
                if nom_attendu and titre and not _correspond(titre, nom_attendu):
                    if "closeAlertButton" in by_id:
                        await click_element(cdp, by_id["closeAlertButton"]["node_id"])
                    return f"Clemz s'apprêtait à importer « {titre} » au lieu de « {nom_attendu} » — annulé"
                if sans_confirmer:
                    logger.info(f"✋ [AUTO-COMPLÉTION {self.dressing}] Fenêtre de confirmation Clemz affichée — non validée.")
                    return None
                # "Confirmer" cliqué par le script, SANS pause (demandé le
                # 18/10/2026) -- le titre a été contrôlé juste au-dessus.
                logger.info(f"✅ [AUTO-COMPLÉTION {self.dressing}] Clic sur « Confirmer » (1 republication Clemz).")
                await click_element(cdp, by_id["continueAlertButton"]["node_id"])
                confirme = True
                break
        if not confirme:
            return "Confirmation d'import Clemz non affichée"

        # Remplissage : Clemz affiche sa progression dans #processStage
        # ("envoi des photos (4/12)"...) puis "Formulaire rempli. Enregistre ou
        # publie quand tu veux." -- relayée au dashboard. Abandon seulement
        # sans AUCUN progrès pendant ATTENTE_SANS_PROGRES_SECONDES (photos
        # nombreuses + mode lent de Clemz), ou au-delà du plafond total.
        ecoule = sans_progres = 0
        dernier = None
        while ecoule < ATTENTE_REMPLISSAGE_SECONDES and sans_progres < ATTENTE_SANS_PROGRES_SECONDES:
            await asyncio.sleep(2)
            ecoule += 2
            sans_progres += 2
            by_id = await snapshot_by_id(cdp)
            if "processStage" in by_id:
                texte = (await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))).get("innerText", "").strip()
                if "formulaire rempli" in texte.lower():
                    return None
                titre_cl = by_id.get("articleTitle")
                titre_cl = ((await get_live_props(cdp, titre_cl["node_id"], props=("innerText",))).get("innerText", "")
                            if titre_cl else "")
                if MARQUEUR_ERREUR_CLEMZ in f"{titre_cl} {texte}".lower():
                    # "XXX ERREUR XXX" (21/10/2026) : Clemz s'arrête là
                    await asyncio.to_thread(_send_notification, "🛑 VintedPro — Erreur Clemz",
                                            f"{self.dressing} : {texte[:100]}")
                    return f"Erreur Clemz pendant le remplissage : {texte[:200]}"
                if texte and texte != dernier:
                    dernier, sans_progres = texte, 0
                    self._maj(message=f"Remplissage par Clemz : {texte}")
                    logger.info(f"⏳ [AUTO-COMPLÉTION {self.dressing}] Clemz : {texte}")
            if "continueAlertButton" in by_id and await self._visible(cdp, by_id, "continueAlertButton"):
                # Autre fenêtre Clemz pendant le remplissage : son texte est
                # tracé AVANT de confirmer (pause en mode pas à pas).
                contenu = by_id.get("alert-content")
                texte_alerte = ((await get_live_props(cdp, contenu["node_id"], props=("innerText",))).get("innerText", "")
                                if contenu else "").strip()
                logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Fenêtre Clemz pendant le remplissage : {texte_alerte[:300]}")
                await self._pas(f"Étape 2 — confirmer la fenêtre Clemz : « {texte_alerte[:120]} »")
                by_id = await snapshot_by_id(cdp)
                if "continueAlertButton" in by_id:
                    await click_element(cdp, by_id["continueAlertButton"]["node_id"])
                sans_progres = 0
        if sans_progres >= ATTENTE_SANS_PROGRES_SECONDES:
            return f"Remplissage bloqué : aucun progrès de Clemz depuis {ATTENTE_SANS_PROGRES_SECONDES // 60} min (dernier état : {dernier})"
        return f"Formulaire non rempli après {ATTENTE_REMPLISSAGE_SECONDES // 60} min (dernier état : {dernier})"

    async def _fenetre_article_en_vente(self, page):
        """Fenêtre Vinted "Article en vente" affichée après publication
        (boutons data-testid list-promotion-submit-cta "Ajouter un autre
        article" / list-promotion-cancel-cta "Plus tard"). Retourne le texte
        alternatif de la vignette ("Titre, marque: …, état: …, prix"), "" s'il
        est absent, ou None si la fenêtre n'est pas affichée."""
        try:
            return await page.evaluate("""() => {
                const bouton = document.querySelector('[data-testid="list-promotion-submit-cta"], [data-testid="list-promotion-cancel-cta"]');
                if (!bouton) return null;
                const dialogue = bouton.closest('.web_ui__Dialog__content') || document;
                return dialogue.querySelector('img.web_ui__Image__content')?.alt || '';
            }""")
        except Exception:
            return None  # page en cours de navigation : on réessaie au tour suivant

    async def _attendre_publication(self, page):
        """Attend que l'utilisateur publie sur Vinted. Signal principal
        (18/10/2026) : la fenêtre Vinted "Article en vente". Secours : la page
        quitte /items/new pour l'annonce publiée.
        Retourne ("publie", texte_vignette|None), ("arret", None) ou ("delai", None)."""
        ecoule = 0
        while ecoule < ATTENTE_VALIDATION_SECONDES:
            if page.is_closed():
                return "arret", None
            if arret_demande(self.dressing):
                return "arret", None
            vignette = await self._fenetre_article_en_vente(page)
            if vignette is not None:
                return "publie", vignette
            if _URL_ANNONCE_PUBLIEE.search(page.url):
                logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Publication détectée par l'adresse "
                               f"({page.url}) — fenêtre « Article en vente » non vue.")
                return "publie", None
            await asyncio.sleep(1)
            ecoule += 1
        return "delai", None

    async def _fermer_fenetre_article_en_vente(self, page):
        """Ferme la fenêtre "Article en vente" par "Plus tard" (jamais "Ajouter
        un autre article" : le script ouvre lui-même le formulaire suivant)."""
        try:
            bouton = await page.query_selector('[data-testid="list-promotion-cancel-cta"]')
            if bouton:
                await bouton.click()
        except Exception as e:
            logger.debug(f"Fenêtre « Article en vente » non fermée : {e}")

    async def _attendre_finalisation_clemz(self, context):
        """Clemz retrouve la nouvelle annonce et finalise (ancienne annonce
        comprise) puis efface pendingAutoCompleteDeclare. À défaut de pouvoir
        lire son stockage, simple délai de sécurité."""
        for _ in range(ATTENTE_FINALISATION_CLEMZ_SECONDES // 2):
            stockage = await self._stockage_clemz(context, ["pendingAutoCompleteDeclare"])
            if stockage is None:
                await asyncio.sleep(10)
                return
            if not stockage.get("pendingAutoCompleteDeclare"):
                return
            await asyncio.sleep(2)
        logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Clemz n'a pas terminé sa finalisation en "
                       f"{ATTENTE_FINALISATION_CLEMZ_SECONDES}s — on continue quand même.")

    async def _ouvrir_auto_completion(self, cdp, page, libelle):
        """Formulaire Vinted /items/new (navigation DIRECTE, demandée le
        18/10/2026) + onglet "Auto complétion" du panneau Clemz.
        Retourne None si OK, sinon la raison de l'échec."""
        self._maj(message="Ouverture du formulaire Vinted")
        await self._pas(f"Étape 2 — ouvrir le formulaire Vinted /items/new ({libelle})")
        await page.goto(URL_NOUVELLE_ANNONCE, wait_until="domcontentloaded")
        if not await self._attendre_panneau(cdp, page):
            return "Panneau Clemz introuvable sur le formulaire"

        by_id = await snapshot_by_id(cdp)
        if "autoCompleteTabLink" not in by_id:
            return "Onglet 'Auto complétion' introuvable (version de Clemz ?)"
        await self._pas("Étape 2 — ouvrir l'onglet « Auto complétion » du panneau Clemz")
        await click_element(cdp, by_id["autoCompleteTabLink"]["node_id"])
        await asyncio.sleep(1)
        return None

    async def _traiter_article(self, context, page, cdp, restants):
        """Traite le PREMIER article de la liste à traiter.
        Retourne (issue, nom, raison) -- issue parmi "publie", "ignore"
        (cet article seulement), "vide" (liste à traiter vide), "bloque"
        (impossible d'avancer : toute la suite s'arrête), "arret", "delai"."""
        raison = await self._ouvrir_auto_completion(cdp, page, "premier article de la liste à traiter")
        if raison:
            return "bloque", None, raison

        self._maj(message="Choix du premier article de la liste à traiter")
        nom, raison = await self._choisir_premier_article(cdp, restants)
        if raison:
            return "bloque", None, raison
        if nom is None:
            return "vide", None, None
        self._maj(article=nom)

        self._maj(message="Remplissage du formulaire par Clemz")
        raison = await self._lancer_remplissage(cdp, page, nom_attendu=nom)
        if raison:
            self._ignorer(nom, raison)
            return "ignore", nom, raison

        self._maj(statut="attente_validation", message="Formulaire rempli — à toi de vérifier et publier")
        await page.bring_to_front()
        # Notification Windows à CHAQUE article prêt (18/10/2026), pas seulement
        # au premier : on peut faire autre chose entre deux annonces.
        await asyncio.to_thread(
            _send_notification,
            "📝 VintedPro — Annonce à publier",
            f"{self.dressing} · {self.etat['index']}/{self.etat['total']} : {nom[:90]}",
        )
        logger.info(f"✋ [AUTO-COMPLÉTION {self.dressing}] '{nom}' prêt — en attente de ta publication.")
        issue, vignette = await self._attendre_publication(page)
        if issue != "publie":
            return issue, nom, None

        # Fenêtre Vinted "Article en vente" : c'est À CE MOMENT que la
        # republication est enregistrée dans Supabase (décision du 18/10/2026).
        self._maj(statut="en_cours", message="Article en vente sur Vinted — enregistrement dans Supabase")
        titre_publie = (vignette or "").split(", marque:")[0].strip()
        logger.info(f"🟢 [AUTO-COMPLÉTION {self.dressing}] Fenêtre « Article en vente » : « {titre_publie or '?'} ».")
        if titre_publie and not _correspond(titre_publie, nom):
            logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Titre publié « {titre_publie} » différent de "
                           f"l'article attendu « {nom} » — enregistré quand même sous « {nom} », à vérifier.")
        await self._pas(f"Étape 2 — enregistrer la republication de « {nom} » dans Supabase (date + quota)")
        await self.bot._article_termine(self.acc, nom, "republication")
        from services.articles_supprimes import retirer
        retirer(nom)  # republié : plus besoin de le protéger du marquage "vendu"
        self.etat["republies"].append(nom)
        logger.info(f"✅ [AUTO-COMPLÉTION {self.dressing}] '{nom}' republié et enregistré ({page.url}).")

        # Puis : fenêtre fermée ("Plus tard") et laisse à Clemz le temps de
        # finaliser (liaison à la sauvegarde) avant de rouvrir un formulaire.
        await self._fermer_fenetre_article_en_vente(page)
        self._maj(message="Finalisation par Clemz")
        await self._attendre_finalisation_clemz(context)
        return "publie", nom, None

    async def run(self):
        from services.clemz_auto_message import request_preemption, release_preemption, get_profile_lock

        account_key = self.acc["account_key"]
        verrou = get_profile_lock(account_key)
        SESSIONS[self.dressing] = self
        self._maj(statut="attente_profil", message="Attente du profil Clemz")
        await request_preemption(account_key)
        await verrou.acquire()
        try:
            if not await ensure_session(account_key):
                self._maj(statut="echec", message="Session Vinted expirée — reconnexion nécessaire")
                for nom in self.produits:
                    self._ignorer(nom, "Session Vinted expirée")
                return self.etat

            # ensure_session() vient de FERMER son propre navigateur sur ce
            # profil : le rouvrir aussitôt fermait la page ~3 s après le
            # chargement (essais du 18/10/2026 à 02:39). Même pause que le
            # scraping et la republication de masse avant de rouvrir.
            await asyncio.sleep(5)

            async with async_playwright() as p:
                args, extra = _args_et_kwargs_navigateur(self.acc, [
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1280,900",
                    "--window-position=0,0",
                ])
                # Une seconde tentative si la page se ferme AVANT que quoi que
                # ce soit n'ait été fait (aucun article touché à ce stade).
                for tentative in (1, 2):
                    context = await p.chromium.launch_persistent_context(
                        self.acc["session"],
                        executable_path=self.acc["executable"],
                        headless=False,
                        args=args,
                        **extra,
                    )
                    try:
                        return await self._piloter(context)
                    except _PageFermeeAuDemarrage:
                        if tentative == 2:
                            raise RuntimeError("La fenêtre Vinted se ferme dès son ouverture (2 tentatives)")
                        logger.warning(f"⚠️ [AUTO-COMPLÉTION {self.dressing}] Page fermée au démarrage — nouvelle tentative dans 5 s.")
                    finally:
                        # Fermeture PROPRE (cookies écrits sur disque) avant la
                        # sortie d'async_playwright, qui sinon tue le navigateur.
                        try:
                            await context.close()
                        except Exception:
                            pass
                    await asyncio.sleep(5)
        except Exception as e:
            logger.exception(f"❌ [AUTO-COMPLÉTION {self.dressing}] Erreur : {e}")
            self._maj(statut="echec", message=f"Erreur : {str(e)[:150]}")
            # Aucun article ne doit rester "en attente" dans l'historique
            traites = set(self.etat["republies"]) | {i["nom"] for i in self.etat["ignores"]}
            for nom in self.produits:
                if nom not in traites:
                    self._ignorer(nom, f"Session interrompue : {str(e)[:120]}")
            return self.etat
        finally:
            # Filet de sécurité : annonce SUPPRIMÉE de Vinted mais pas
            # republiée (erreur, arrêt, délai) -- elle n'existe plus que dans la
            # sauvegarde clemz.app : à republier à la main.
            perdues = [n for n in self.supprimes if n not in self.etat["republies"]]
            if perdues and not self.suppression_seule:  # suppression seule : voulu, pas une perte
                self.etat["supprimes_non_republies"] = perdues
                logger.error(f"🆘 [AUTO-COMPLÉTION {self.dressing}] SUPPRIMÉ(S) de Vinted mais NON republié(s) : {perdues}")
                try:
                    await asyncio.to_thread(
                        _send_notification,
                        "🆘 VintedPro — Annonces supprimées non republiées",
                        f"{self.dressing} : {len(perdues)} annonce(s) à republier à la main depuis clemz.app.",
                    )
                except Exception:
                    pass
            PROGRESSION.pop(self.dressing, None)
            ARRETS_DEMANDES.discard(self.dressing)
            verrou.release()
            await release_preemption(account_key)
            if self.task_id:
                from services.automation_scheduler import finish_task_run
                finish_task_run(self.task_id)
            from services.planification_republication import rafraichir_pause_si_necessaire
            rafraichir_pause_si_necessaire(self.dressing)

    async def _piloter(self, context):
        from services.risk_guard import get_allowed_quantity

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _close_clemz_promo_tabs(context, page)
            await page.goto(self.acc["url"], wait_until="domcontentloaded")
            await asyncio.sleep(3)
            if page.is_closed():
                raise _PageFermeeAuDemarrage()
            cdp = await context.new_cdp_session(page)
            await cdp.send("DOM.enable")
            panneau_ok = await self._attendre_panneau(cdp, page)
        except _PageFermeeAuDemarrage:
            raise
        except Exception as e:
            if page.is_closed() or "closed" in str(e).lower():
                raise _PageFermeeAuDemarrage() from e
            raise

        if not panneau_ok:
            self._maj(statut="echec", message="Panneau Clemz introuvable (extension chargée ?)")
            for nom in self.produits:
                self._ignorer(nom, "Panneau Clemz introuvable")
            return self.etat

        # VALIDATION MANUELLE (20/10/2026) : Clemz traite TOUTE la liste d'un
        # seul "Go Clemz !" -- le quota du jour est donc appliqué AVANT, en
        # réduisant la série à ce qui reste autorisé.
        if self.validation_manuelle:
            from services.risk_guard import get_allowed_quantity
            autorise, _, raison_quota = get_allowed_quantity(self.dressing, "republication", len(self.produits))
            for reste in self.produits[autorise:]:
                self._ignorer(reste, raison_quota or "Quota anti-détection atteint")
            self.produits = self.produits[:autorise]
            self.acc["produits"] = self.produits
            if not self.produits:
                self._maj(statut="echec", message=raison_quota or "Quota du jour atteint")
                return self.etat
            try:
                await self._serie_validation_manuelle(cdp, page)
            except _SerieInterrompue as e:
                logger.error(f"🛑 [AUTO-COMPLÉTION {self.dressing}] Validation manuelle : {e}")
                traites = set(self.etat["republies"]) | {i["nom"] for i in self.etat["ignores"]}
                for nom in self.produits:
                    if nom not in traites:
                        self._ignorer(nom, f"Validation manuelle : {e}")
                self._maj(statut="echec", message=f"Validation manuelle : {e}")
                return self.etat
            bilan = f"{len(self.etat['republies'])} republié(s), {len(self.etat['ignores'])} ignoré(s)"
            await self._pas(f"Fin de série (validation manuelle) — {bilan}. « Continuer » pour fermer le navigateur")
            self._maj(statut="termine", article=None, message=bilan)
            return self.etat

        # REMPLISSAGE SEUL (19/10/2026) : liste "à traiter" vidée puis remplie
        # par UNE source, et rien d'autre (ni suppression, ni publication).
        if self.remplissage == "smart_dressing":
            self._maj(statut="preparation", message="Remplissage de la liste par le Smart Dressing")
            try:
                ajoutes = await self._construire_liste_smart_dressing(cdp, page, "remplissage Smart Dressing")
            except _SerieInterrompue as e:
                self._maj(statut="echec", message=str(e))
                return self.etat
            self.etat["prets"] = ajoutes
            await self._pas(f"Fin du remplissage — {len(ajoutes)}/{len(self.produits)} article(s) dans la liste. "
                            f"« Continuer » pour fermer le navigateur")
            self._maj(statut="termine", article=None,
                      message=f"Smart Dressing : {len(ajoutes)}/{len(self.produits)} article(s) dans la liste à traiter")
            return self.etat
        if self.remplissage == "clemz_app":
            self._maj(statut="preparation", message="Remplissage de la liste depuis clemz.app")
            await self._pas("Remplissage — vider la liste « à traiter » de Clemz")
            vidage = await self.bot._vider_liste_a_traiter(cdp, page, self.acc)
            if vidage["status"] != "success":
                self._maj(statut="echec", message=vidage["reason"])
                return self.etat
            prets = await self._preparer_liste(context, cdp, page)
            self.etat["prets"] = prets
            await self._pas(f"Fin du remplissage — {len(prets)}/{len(self.produits)} article(s) dans la liste. "
                            f"« Continuer » pour fermer le navigateur")
            self._maj(statut="termine", article=None,
                      message=f"clemz.app : {len(prets)}/{len(self.produits)} article(s) dans la liste à traiter")
            return self.etat

        # SUPPRESSION SEULE (19/10/2026) : étape 0 puis arrêt.
        if self.suppression_seule:
            try:
                supprimes = await self._etape_suppression(cdp, page)
            except _SerieInterrompue as e:
                logger.error(f"🛑 [AUTO-COMPLÉTION {self.dressing}] Suppression : {e}")
                traites = {i["nom"] for i in self.etat["ignores"]} | set(self.supprimes)
                for nom in self.produits:
                    if nom not in traites:
                        self._ignorer(nom, str(e))
                self._maj(statut="echec", message=f"Suppression : {e}")
                return self.etat
            if self.task_id:
                from services.automation_scheduler import update_task_result_par_nom
                for nom in supprimes:
                    update_task_result_par_nom(self.task_id, nom, "success",
                                               "Supprimé de Vinted (sauvegardé sur clemz.app)")
            await self._pas(f"Fin de la suppression — {len(supprimes)} annonce(s) supprimée(s). "
                            f"« Continuer » pour fermer le navigateur")
            self._maj(statut="termine", article=None,
                      message=f"{len(supprimes)}/{len(self.produits)} annonce(s) supprimée(s) de Vinted")
            return self.etat

        # ÉTAPE 0 -- sauvegarde puis suppression sur Vinted. Seuls les
        # articles réellement supprimés passent aux étapes suivantes.
        if self.suppression_prealable:
            try:
                supprimes = await self._etape_suppression(cdp, page)
            except _SerieInterrompue as e:
                logger.error(f"🛑 [AUTO-COMPLÉTION {self.dressing}] Étape 0 : {e}")
                traites = {i["nom"] for i in self.etat["ignores"]}
                for nom in self.produits:
                    if nom not in traites:
                        self._ignorer(nom, f"Étape 0 : {e}")
                self._maj(statut="echec", message=f"Étape 0 : {e}")
                return self.etat
            self.produits = supprimes
            self.acc["produits"] = supprimes
            self._maj(total=len(supprimes))
            await self._attendre_panneau(cdp, page)

        # ÉTAPE 1 -- liste "à traiter" vidée (aucun résidu d'une autre tâche)
        # puis remplie depuis clemz.app avec CES articles : c'est là que
        # l'auto-complétion vient les chercher.
        self._maj(statut="preparation", message="Préparation de la liste Clemz")
        await self._pas("Étape 1 — vider la liste « à traiter » de Clemz (aucun résidu d'une autre tâche)")
        vidage = await self.bot._vider_liste_a_traiter(cdp, page, self.acc)
        if vidage["status"] != "success":
            self._maj(statut="echec", message=vidage["reason"])
            for nom in self.produits:
                self._ignorer(nom, vidage["reason"])
            return self.etat
        prets = await self._preparer_liste(context, cdp, page)

        # ÉTAPE 2 -- boucle sur le PREMIER article de la liste à traiter, tant
        # qu'elle n'est pas vide (Clemz retire chaque article au remplissage).
        # `restants` borne la boucle : au plus un tour par article préparé.
        self._maj(statut="en_cours", index=0, article=None)
        restants = list(prets)
        tour = 0
        while restants:
            if arret_demande(self.dressing):
                for reste in restants:
                    self._ignorer(reste, "Arrêté à ta demande")
                break
            # Quota revérifié avant CHAQUE article (compté au fil de l'eau)
            autorise, _, raison_quota = get_allowed_quantity(self.dressing, "republication", 1)
            if autorise < 1:
                for reste in restants:
                    self._ignorer(reste, raison_quota or "Quota anti-détection atteint")
                break

            tour += 1
            self._maj(index=tour, article=None)
            try:
                issue, nom, raison = await self._traiter_article(context, page, cdp, restants)
            except Exception as e:
                nom = None
                raison = f"Erreur inattendue : {str(e)[:150]}"
                issue = "arret" if page.is_closed() else "bloque"
            if nom in restants:
                restants.remove(nom)

            if issue == "vide":
                for reste in restants:
                    self._ignorer(reste, "Plus dans la liste à traiter au moment de son tour")
                break
            if issue == "bloque":
                logger.error(f"🛑 [AUTO-COMPLÉTION {self.dressing}] Série arrêtée : {raison}")
                for reste in restants:
                    self._ignorer(reste, raison)
                break
            if issue == "arret":
                for reste in ([nom] if nom else []) + restants:
                    self._ignorer(reste, "Arrêté (fenêtre fermée ou arrêt demandé)")
                break
            if issue == "delai":
                for reste in ([nom] if nom else []) + restants:
                    self._ignorer(reste, f"Pas de publication en {ATTENTE_VALIDATION_SECONDES // 60} min — session arrêtée")
                break

        bilan = f"{len(self.etat['republies'])} republié(s), {len(self.etat['ignores'])} ignoré(s)"
        if self.etat["ignores"]:
            bilan += f" — dernier motif : {self.etat['ignores'][-1]['raison'][:150]}"
        # Pas à pas : fenêtre gardée ouverte en fin de série (succès comme
        # échec) pour constater l'état de la page avant fermeture.
        await self._pas(f"Fin de série — {bilan}. « Continuer » pour fermer le navigateur")
        self._maj(statut="termine", article=None, message=bilan)
        return self.etat
