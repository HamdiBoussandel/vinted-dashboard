# services/supabase_service.py
import math
import unicodedata
from datetime import datetime, timedelta
from database import supabase

class SupabaseService:
    def __init__(self):
        self.db = supabase

        # --- PARAMÈTRES DE STRATÉGIE ---
        self.DELAI_LIQUIDATION = 45
        self.DELAI_SORTIE_FINALE = 90
        self.SCORE_PERF_EXCELLENTE = 15.0
        # NOUVEAU : marge plancher minimum uniforme à x2, quel que soit le prix
        # (avant : x1.6 pour le standard, ce qui passait sous la marge minimum exigée)
        self.COEFF_PLANCHER_STANDARD = 2.0
        self.COEFF_PLANCHER_PETIT_PRIX = 2.0

        # Paramètres Clemz -- boost Vinted réel observé entre 15 et 20 vues/favoris
        # (pas une valeur fixe). Repassé à 20 (le plafond) le 17/09/2026,
        # décision explicite de l'utilisateur -- revient sur le choix du
        # 13/09/2026 (médiane 17), qui avait justement été motivé par le fait
        # que 20 sous-estime v_reel/f_reel dès que le vrai boost est plus
        # proche de 15. Historique : 20 (origine) -> 17 (13/09, médiane) -> 20
        # (17/09, choix délibéré de l'utilisateur).
        self.CLEMZ_BOOST_VUES_MAX = 20
        self.CLEMZ_BOOST_FAVORIS_FIXE = 20
        self.RANG_MAX_BOOST_VUES = 40
        self.RANG_MAX_BOOST_FAVORIS = 50

        # Paramètres Pépite
        self.SEUIL_PEPITE = 15.0
        # 25 était trop strict : sur l'inventaire réel du 13/09/2026, seulement
        # 3-4 pépites détectées alors que 14 articles avaient un score excellent
        # (15-33%) juste sous ce seuil de vues. 18 calé sur un point de rupture
        # naturel dans la distribution réelle (9 pépites à 20, 15 à 18, 26 à 12
        # -- 12 aurait trop dilué le signal).
        self.MIN_V_REEL_PEPITE = 18
        self.DELAI_MIN_PEPITE = 4

        # Nombre minimum de vues réelles avant qu'un score soit considéré fiable --
        # en dessous, get_processed_inventory force score=0.0 par manque
        # d'échantillon (pas une vraie mesure de performance). Doit rester
        # synchronisé avec SEUIL_MIN_VUES_SCORE_FIABLE (frontend, ProductCard.jsx).
        self.SEUIL_MIN_VUES_SCORE_FIABLE = 5

        # Paramètres Mauvaise Performance
        self.SEUIL_LOW_PERF = 5.0
        self.DELAI_MIN_LOW_PERF = 0
        self.DELAI_MIN_LOW_PERF_HIGH = 7
        self.DELAI_FORCE_REPUBLISH = 10
        # Remplace l'ancien plancher "marge_dispo > 3€" (valeur ABSOLUE, donc
        # dérisoire sur un article cher et trop stricte sur un article pas cher
        # -- même défaut que CLEMZ_BOOST_VUES_MAX avant sa correction). Le
        # nouveau seuil compare la baisse RÉELLEMENT applicable (après
        # écrêtage au plancher de marge) au prix de vente actuel : si le
        # plancher est trop proche pour permettre au moins 5% de baisse
        # réelle, la baisse de prix est jugée inutile (imperceptible pour un
        # acheteur), on suggère de changer les photos à la place. Décision du
        # 19/09/2026.
        self.SEUIL_BAISSE_MINIMALE_RELATIVE = 0.05

        # Paramètres Invisibilité
        self.SEUIL_INVISIBLE_LOW = 2
        self.SEUIL_INVISIBLE_HIGH = 10
        self.DELAI_MIN_INVISIBLE = 3       # délai avant shadow-ban — DISTINCT du délai Mauvaise Perf
        self.DELAI_MIN_INVISIBLE_HIGH = 7

        # Seuils de prix
        self.LIMITE_LOW_TICKET = 50.0
        self.DELAI_LOW_TICKET = 15
        self.DELAI_HIGH_TICKET = 21

        # Statut Quo
        self.MIN_V_REEL_STATUT_QUO = 50

    def strip_accents(self, s):
        return ''.join(
            c for c in unicodedata.normalize('NFD', s)
            if unicodedata.category(c) != 'Mn'
        )

    NEGOTIATION_MARGIN = 0.20  # marge pour absorber la négociation acheteur -- donnée réelle
                                # (analyse des remises Vinted, 25/05-31/08/2026) : 47.6% des
                                # ventes se négocient entre 11-20%, une marge de 20% couvre 76%
                                # des cas réels. Le prix affiché intègre cette marge, le vrai
                                # plancher (prix_cible_plancher) reste protégé même après
                                # négociation typique.

    def _get_liquidation_target(self, jours_en_vente, prix_achat, prix_vente):
        """
        Calcule la phase de liquidation et le prix cible pour un article dormant,
        purement à partir de jours_en_vente/prix_vente (pas d'état stocké --
        recalculé à chaque fois, donc jamais désynchronisé). Retourne (None, None)
        si l'article n'est pas encore assez dormant (< DELAI_LIQUIDATION jours).

        Phase 0 (45-89j)   : signal précoce, pas encore une urgence de décision de
                             prix -- -30% vs prix de vente affiché ACTUELLEMENT,
                             jamais sous le prix d'achat. Formule reprise telle
                             quelle de l'ancien label texte "💀 LIQUIDATION"
                             (action_label), désormais exposée en donnée
                             structurée plutôt que noyée dans du texte, pour
                             que la page Liquidation puisse l'afficher (consolidation
                             du 07/09/2026 : l'onglet Liquidation du Dashboard,
                             qui affichait cette même population séparément, est
                             retiré au profit de cette page dédiée).
        Phase 1 (90-109j)  : breakeven (prix_achat)
        Phase 2 (110-129j) : -10% vs achat
        Phase 3 (130j+)    : -20% vs achat au palier 130, puis -10% supplémentaire
                             tous les 10j, plafonné au plancher de la tranche.

        Le prix retourné pour les phases 1-3 intègre NEGOTIATION_MARGIN -- c'est
        le prix à AFFICHER/LISTER, pas le vrai plancher économique. Même après
        une négociation typique (11-20%), le montant réellement encaissé reste
        au-dessus du plancher réel visé par la phase. La phase 0 n'a pas ce
        problème (son prix cible n'est pas dérivé du plancher économique).
        """
        if jours_en_vente < self.DELAI_LIQUIDATION or prix_achat <= 0:
            return None, None

        if jours_en_vente < self.DELAI_SORTIE_FINALE:
            prix_cible_phase0 = max(prix_achat, round(prix_vente * 0.70, 2))
            return 0, prix_cible_phase0

        if prix_achat < 10:
            floor_pct = 0.35
        elif prix_achat < 30:
            floor_pct = 0.25
        else:
            floor_pct = 0.15

        if jours_en_vente < 110:
            phase = 1
            reduction = 0.0
        elif jours_en_vente < 130:
            phase = 2
            reduction = 0.10
        else:
            phase = 3
            paliers_supplementaires = (jours_en_vente - 130) // 10 + 1
            reduction = min(0.10 + paliers_supplementaires * 0.10, floor_pct)

        prix_cible_plancher = round(prix_achat * (1 - reduction), 2)
        prix_cible_affiche = round(prix_cible_plancher * (1 + self.NEGOTIATION_MARGIN), 2)
        return phase, prix_cible_affiche

    @staticmethod
    def calculer_prix_fixe_liquidation(prix_cible: float) -> float:
        """
        Arrondit un prix cible de liquidation vers le haut jusqu'au prochain euro
        entier, puis retranche 10 centimes -- ex: 5.99€ -> 5.90€, 5.34€ -> 5.90€,
        2.00€ (déjà un nombre rond) -> 2.90€ (TOUJOURS vers le haut, même si le
        prix est déjà entier -- pas un simple plafond mathématique, exemples
        validés par l'utilisateur le 03/10/2026). int(prix_cible) tronque déjà
        vers le bas pour tout prix positif, pas besoin de math.floor.

        Seule source de vérité pour ce calcul -- réutilisée à la fois pour
        décider si un article a encore besoin d'une action de liquidation
        (liquidation_needs_action ci-dessus) ET pour regrouper les articles par
        prix fixe avant envoi à Clemz (routes/inventory.py, run_liquidation_now).
        """
        return int(prix_cible) + 0.90

    def _get_taux_baisse_pepite(self, prix_vente, jours_en_ligne):
        """
        Taux de baisse suggéré pour une pépite (💎), par tranche de prix --
        un même pourcentage a un impact en € très différent selon le prix
        (ex: -5% = 0,50€ sur un article à 10€, contre 2,50€ sur un article à
        50€), donc plus le prix est bas, plus le pourcentage doit être élevé
        pour rester perceptible côté acheteur. Bornes calées sur la
        distribution réelle des prix éligibles pépite (>10€) le 10/09/2026 :
        p25≈12€, médiane≈24€, p75≈39€, p90≈52€. Conserve l'escalade existante
        selon jours_en_ligne (>7j = coupe plus profonde, l'article traîne).
        Reste une suggestion manuelle -- les pépites ne sont jamais baissées
        automatiquement (baisse_prix_taux reste None pour elles).

        Retourne (taux, raison) -- la raison est affichée telle quelle sur la
        carte produit pour expliquer pourquoi CE taux précis a été choisi
        (cf. échange du 19/09/2026).
        """
        ancien = jours_en_ligne > 7
        suffixe_anciennete = ", en ligne >7j (coupe plus profonde)" if ancien else ", en ligne ≤7j"
        if prix_vente < 20:
            taux = 0.80 if ancien else 0.85
            raison = f"Pépite, prix < 20€{suffixe_anciennete}"
        elif prix_vente < 40:
            taux = 0.85 if ancien else 0.90
            raison = f"Pépite, prix 20-40€{suffixe_anciennete}"
        else:
            taux = 0.92 if ancien else 0.95
            raison = f"Pépite, prix ≥ 40€{suffixe_anciennete}"
        return taux, raison

    def _get_taux_baisse_low_perf(self, prix_vente, is_new):
        """
        Taux de baisse pour un article en Mauvaise Performance (score <
        SEUIL_LOW_PERF), par tranche de prix -- corrige le même défaut que
        _get_taux_baisse_pepite (un pourcentage fixe a un impact absolu très
        différent selon le prix), qui n'avait jusqu'ici été corrigé QUE pour
        les pépites, pas pour la mauvaise perf (-10% sous 10€, -20% flat
        au-dessus, quel que soit le prix -- décalage relevé le 19/09/2026).

        Bornes réutilisées de LIMITE_LOW_TICKET (50€, déjà la frontière
        LOW/HIGH utilisée ailleurs dans ce fichier) plutôt que d'introduire
        encore un seuil de prix isolé, avec une coupure intermédiaire à 20€
        (même borne basse que les pépites) pour un peu de granularité sur la
        tranche 10-50€ où se trouve l'essentiel de l'inventaire.

        is_new (jamais republié) reste traité à part, à -10% quel que soit le
        prix : un article tout juste sorti mérite un ajustement doux avant
        un vrai jugement sur sa performance, pas une coupe agressive d'emblée.

        Retourne (taux, raison) -- la raison est affichée telle quelle sur la
        carte produit pour expliquer pourquoi CE taux précis a été choisi
        (cf. échange du 19/09/2026).
        """
        if is_new:
            return 0.90, "Mauvaise perf, article neuf → traitement doux"
        if prix_vente < 20:
            return 0.90, "Mauvaise perf, prix < 20€"
        elif prix_vente < self.LIMITE_LOW_TICKET:
            return 0.85, f"Mauvaise perf, prix 20-{int(self.LIMITE_LOW_TICKET)}€"
        else:
            return 0.80, f"Mauvaise perf, prix ≥ {int(self.LIMITE_LOW_TICKET)}€"

    def _get_purchase_prices(self):
        """
        Récupère tous les prix d'achat depuis Supabase.
        Retourne un dict {nom_lower: prix_achat} pour jointure insensible à la casse.
        En cas de doublon de nom (ex: 2 achats du même article à des dates
        différentes), c'est le plus RÉCENT (created_at) qui fait foi.
        """
        response = self.db.table("achats") \
            .select("nom, prix_achat, created_at") \
            .order("created_at", desc=True) \
            .execute()
        price_map = {}
        for row in response.data:
            nom_lower = row["nom"].lower()
            if nom_lower not in price_map:  # premier rencontré = le plus récent (tri desc)
                price_map[nom_lower] = float(row["prix_achat"] or 0)
        return price_map

    async def get_processed_inventory(self):
        """
        Récupère l'inventaire depuis Supabase, applique le scoring Python,
        et retourne la liste des articles enrichis pour le dashboard.
        """
        # 1. Lecture des articles non vendus
        response = self.db.table("articles") \
            .select("*") \
            .eq("est_vendu", False) \
            .execute()
        raw_articles = response.data

        # 2. Lecture des prix d'achat
        purchase_map = self._get_purchase_prices()

        articles = []
        today = datetime.now()

        for item in raw_articles:
            item_name = item.get("nom", "")
            prix_achat = purchase_map.get(item_name.lower(), 0.0)
            prix_vente = float(item.get("prix_vente") or 0)

            # --- CALCUL DU PLANCHER DYNAMIQUE (marge) ---
            coeff = self.COEFF_PLANCHER_PETIT_PRIX if 0 < prix_achat < 5.0 else self.COEFF_PLANCHER_STANDARD
            prix_plancher = round(prix_achat * coeff, 2) if prix_achat > 0 else 0
            prix_optimiste = round(prix_achat * 3.0, 2) if prix_achat > 0 else 0
            marge_dispo = prix_vente - prix_plancher

            # --- NOUVEAU : plancher de sécurité absolu (breakeven, = prix d'achat réel) ---
            # Distinct du plancher de marge ci-dessus : celui-ci protège le capital
            # investi, pas la rentabilité. Utilisé uniquement en phase de liquidation.
            prix_plancher_absolu = prix_achat if prix_achat > 0 else 0

            # --- DATES ---
            date_pub = item.get("date_publication")
            date_repub = item.get("date_republication")
            is_new = date_repub is None

            date_pub_obj = datetime.strptime(date_pub, "%Y-%m-%d") if date_pub else today
            date_repub_obj = datetime.strptime(date_repub, "%Y-%m-%d") if date_repub else date_pub_obj

            jours_en_vente = max(1, (today - date_pub_obj).days)
            jours_en_ligne = max(1, (today - date_repub_obj).days)

            # --- CALCUL DE LA PHASE DE LIQUIDATION (stock dormant 90j+) ---
            liquidation_phase, liquidation_prix_cible = self._get_liquidation_target(jours_en_vente, prix_achat, prix_vente)
            # CORRIGÉ (03/10/2026) : comparé désormais au PRIX FIXE réellement
            # appliqué par le regroupement de liquidation (cf.
            # calculer_prix_fixe_liquidation), pas à la cible brute -- l'arrondi
            # volontairement vers le haut (jamais sous la cible) peut légitimement
            # laisser jusqu'à ~0,99€ d'écart avec la cible, largement au-dessus de
            # l'ancienne tolérance de 0,50€. Sans ce correctif, un article tout
            # juste traité par /liquidation/run-now restait signalé "à traiter"
            # indéfiniment, empêchant la page Liquidation de jamais se vider.
            liquidation_needs_action = (
                liquidation_prix_cible is not None and
                prix_vente > self.calculer_prix_fixe_liquidation(liquidation_prix_cible) + 0.02  # tolérance arrondi flottant
            )
            liquidation_pourcentage_necessaire = None
            if liquidation_needs_action:
                liquidation_pourcentage_necessaire = round((prix_vente - liquidation_prix_cible) / prix_vente * 100, 1)

            # --- STATS ---
            rang  = item.get("rang") or 100
            v_tot = item.get("vues") or 0
            f_tot = item.get("favoris") or 0
            v_reel = max(1, v_tot - self.CLEMZ_BOOST_VUES_MAX)
            f_reel = max(0, f_tot - self.CLEMZ_BOOST_FAVORIS_FIXE)
            score  = round(min(100.0, (f_reel / v_reel) * 100), 2) if v_reel >= self.SEUIL_MIN_VUES_SCORE_FIABLE else 0.0

            # --- SEUILS SELON TICKET ---
            if prix_vente < self.LIMITE_LOW_TICKET:
                delai_observation           = self.DELAI_MIN_LOW_PERF
                delai_observation_invisible = self.DELAI_MIN_INVISIBLE
                seuil_invisible             = self.SEUIL_INVISIBLE_LOW
                delai_max                   = self.DELAI_LOW_TICKET
            else:
                delai_observation           = self.DELAI_MIN_LOW_PERF_HIGH
                delai_observation_invisible = self.DELAI_MIN_INVISIBLE_HIGH
                seuil_invisible             = self.SEUIL_INVISIBLE_HIGH
                delai_max                   = self.DELAI_HIGH_TICKET

            # --- DÉTECTIONS ---
            is_pepite     = (prix_vente > 10.0) and (score >= self.SEUIL_PEPITE) and \
                            (v_reel >= self.MIN_V_REEL_PEPITE) and \
                            (jours_en_ligne >= self.DELAI_MIN_PEPITE)

            is_statut_quo = (prix_vente > 10.0) and (score >= self.SEUIL_LOW_PERF) and \
                            (score < self.SEUIL_PEPITE) and \
                            (v_reel >= self.MIN_V_REEL_STATUT_QUO) and \
                            (jours_en_ligne < 7)  # Bascule en low_perf après 7j

            is_invisible  = (jours_en_ligne >= delai_observation_invisible) and (v_reel < seuil_invisible)

            is_low_perf   = (prix_vente > 10.0) and (jours_en_ligne >= delai_observation) and \
                            (score < self.SEUIL_LOW_PERF) and (not is_invisible) and \
                            (v_reel >= self.SEUIL_MIN_VUES_SCORE_FIABLE)

            # Même raisonnement que is_liquidation ci-dessous : sans prix d'achat
            # réel (article perso/pour un ami, prix_achat=0), aucune urgence de
            # "sortie" n'a de sens -- il n'y a rien à récupérer financièrement.
            is_very_old   = jours_en_vente >= self.DELAI_SORTIE_FINALE and prix_achat > 0
            is_liquidation = jours_en_vente >= self.DELAI_LIQUIDATION and prix_achat > 0
            needs_time_republish  = jours_en_ligne >= delai_max
            should_force_republish = (is_low_perf and
                                      jours_en_ligne >= self.DELAI_FORCE_REPUBLISH and
                                      prix_vente < self.LIMITE_LOW_TICKET)

            # Distinct de is_critical : ce flag isole les VRAIES raisons de republier
            # (fraîcheur expirée, shadow-ban, mauvaise perf forcée) -- exclut
            # délibérément is_very_old, qui est une alerte de décision de prix
            # (vendre à perte ?), pas un signal "il faut republier cet article".
            needs_republish_action = is_invisible or needs_time_republish or should_force_republish

            # Distinct de is_critical : ce flag isole les VRAIES raisons de republier
            # (fraîcheur expirée, shadow-ban, mauvaise perf forcée) -- exclut
            # délibérément is_very_old, qui est une alerte de décision de prix
            # (vendre à perte ?), pas un signal "il faut republier cet article".
            needs_republish_action = is_invisible or needs_time_republish or should_force_republish

            # --- DIAGNOSTIC ---
            if jours_en_ligne >= delai_max:
                if score >= self.SCORE_PERF_EXCELLENTE:
                    diagnostic_cycle = "🔥 CYCLE RÉUSSI (Garder prix)"
                elif score >= self.SEUIL_LOW_PERF:
                    diagnostic_cycle = "🌤️ CYCLE TIÈDE (Baisse légère)"
                else:
                    diagnostic_cycle = "⚠️ CYCLE ÉCHOUÉ (Baisse + Photos)"
            else:
                diagnostic_cycle = f"⏳ Bilan dans {delai_max - jours_en_ligne}j"

            # --- LABELS D'ACTION ---
            display_pepite    = False
            display_low_perf  = False
            display_statut_quo = False
            is_critical       = False

            SEUIL_AUDIT = 3
            nb_repub = item.get("nb_republications_sans_vente", 0)
            is_audit = nb_repub >= SEUIL_AUDIT

            # Champ explicite pour l'automatisme de baisse de prix (10/20/None) --
            # évite de parser action_label (texte + emoji, fragile) pour savoir
            # dans quel lot un article doit partir.
            baisse_prix_taux = None

            # Équivalent pour les pépites (5/8/10/15/20/None, cf.
            # _get_taux_baisse_pepite) -- jamais utilisé pour une baisse
            # automatique (les pépites restent 100% manuelles), mais permet au
            # panier de baisse manuelle du dashboard de pré-remplir le bon taux
            # par article plutôt qu'un taux unique pour tout le panier.
            pepite_taux_suggere = None
            # Explication lisible du taux choisi (tranche de prix, ancienneté...),
            # affichée sur la carte produit à côté du pourcentage -- cf. échange
            # du 19/09/2026 ("indiquer visuellement... pour quelle raison").
            pepite_taux_raison = None
            baisse_prix_raison = None

            if is_audit:
                # Correctif : ce cas doit COURT-CIRCUITER toute la chaîne is_invisible/
                # is_pepite/is_low_perf/... ci-dessous, sinon l'article est quand même
                # évalué normalement et peut ressortir "Mauvaise Perf" malgré le flag
                # d'audit -- exactement le bug qu'on vient de trouver.
                action_label = "À auditer"
                is_critical = False
                display_pepite = display_low_perf = display_statut_quo = False

            elif is_invisible:
                action_label = "♻️ REPUBLIER (Shadow Ban)"
                is_critical  = True

            elif is_very_old:
                # Cas extrême (≥90j) : même au prix d'achat, l'article ne part pas.
                # Vendre en dessous du prix coûtant est une décision de perte délibérée
                # — je ne l'automatise PAS, je la signale pour validation manuelle.
                # C'est précisément ce type d'arbitrage que l'agent IA pourra un jour
                # trancher à la place de Nini.
                if prix_achat > 0:
                    prix_suggere_perte = round(prix_achat * 0.90, 2)
                    action_label = f"🔻 À VALIDER : vendre à perte ? ({prix_suggere_perte}€ vs achat {prix_achat}€)"
                else:
                    action_label = f"🚨 SORTIE : Prix Achat inconnu — vérifier manuellement"
                is_critical  = True
                needs_agent_decision = True

            elif needs_time_republish or should_force_republish:
                is_critical = True
                if is_pepite:
                    taux, pepite_taux_raison = self._get_taux_baisse_pepite(prix_vente, jours_en_ligne)
                    pepite_taux_suggere = int(round((1 - taux) * 100))
                    prix_suggere = max(prix_plancher, round(prix_vente * taux, 2))
                    action_label = f"💎 PÉPITE : Baisse -{pepite_taux_suggere}% ({prix_suggere}€)"
                    display_pepite = True
                elif is_liquidation:
                    action_label = f"♻️ REPUBLIER (Liquidation {prix_vente}€)"
                elif is_statut_quo:
                    action_label = "⚖️ REPUBLIER (Statut Quo : Revoir Photos)"
                    display_statut_quo = True
                # CORRIGÉ : elif au lieu de if (bug écrasement labels)
                elif score < self.SEUIL_LOW_PERF and item.get("est_traite"):
                    # Baisse déjà appliquée et pas encore republié depuis (une
                    # republication remet est_traite à False) : ne pas empiler une
                    # deuxième baisse sur la première, juste republier au prix actuel
                    # (cf. échange du 20/09/2026). Le libellé contient "Même prix"
                    # (sous-filtre Standard) et "baisse déjà appliquée" (badge carte).
                    action_label = "♻️ REPUBLIER (Même prix -- baisse déjà appliquée)"
                    baisse_prix_raison = "Une baisse a déjà été appliquée et l'annonce n'a pas été republiée depuis : pas de nouvelle baisse avant la republication."
                elif score < self.SEUIL_LOW_PERF:
                    prix_suggere = max(prix_plancher, round(prix_vente * 0.80, 2))
                    # Pourcentage RÉEL à saisir pour atteindre prix_suggere (cf. échange
                    # du 20/09/2026) : peut être inférieur à -20% si le plancher de marge
                    # écrête le prix cible -- toujours afficher le vrai écart, pas le
                    # taux théorique. Les consommateurs testent seulement
                    # includes("REPUBLIER avec BAISSE"), inchangé.
                    pct_reel = round((1 - prix_suggere / prix_vente) * 100) if prix_vente > 0 else 0
                    action_label = f"♻️ REPUBLIER avec BAISSE -{pct_reel}% ({prix_suggere}€)"
                    if pct_reel < 20:
                        baisse_prix_raison = f"Republication avec baisse : -20% visé, ramené à -{pct_reel}% par le plancher de marge ({prix_plancher}€)"
                    else:
                        baisse_prix_raison = "Republication avec baisse : score < 5%, -20% pour relancer l'annonce"
                else:
                    action_label = "♻️ REPUBLIER (Même prix)"

            elif is_liquidation:
                # NOUVEAU : en liquidation, priorité à la récupération de trésorerie —
                # le plancher passe du plancher de marge au plancher de sécurité absolu
                # (prix d'achat réel), pour permettre de vraiment se débarrasser du stock
                # dormant plutôt que rester bloqué à un prix qui protège la marge.
                prix_cible_liq = max(prix_plancher_absolu, round(prix_vente * 0.70, 2))
                if prix_vente <= prix_cible_liq:
                    action_label = f"💀 LIQUIDATION : Poste OK ({prix_vente}€)"
                else:
                    action_label = f"💀 LIQUIDATION : Prix Plancher Sécurité ({prix_cible_liq}€)"

            elif is_pepite:
                taux, pepite_taux_raison = self._get_taux_baisse_pepite(prix_vente, jours_en_ligne)
                pepite_taux_suggere = int(round((1 - taux) * 100))
                prix_suggere = max(prix_plancher, round(prix_vente * taux, 2))
                action_label = f"💎 PÉPITE : Baisse -{pepite_taux_suggere}% ({prix_suggere}€)"
                display_pepite = True

            elif is_low_perf:
                display_low_perf = True
                taux, baisse_prix_raison = self._get_taux_baisse_low_perf(prix_vente, is_new)
                taux_pct = int(round((1 - taux) * 100))
                if is_new or prix_vente < 20.0:
                    prix_suggere = max(prix_plancher, round(prix_vente * taux, 2))
                    action_label = f"📉 MAUVAISE PERF : Baisse -{taux_pct}% ({prix_suggere}€)"
                    baisse_prix_taux = taux_pct
                else:
                    prix_suggere_ecrete = max(prix_plancher, round(prix_vente * taux, 2))
                    baisse_reelle_relative = (prix_vente - prix_suggere_ecrete) / prix_vente if prix_vente > 0 else 0
                    if baisse_reelle_relative >= self.SEUIL_BAISSE_MINIMALE_RELATIVE:
                        prix_suggere = prix_suggere_ecrete
                        action_label = f"🚨 MAUVAISE PERF : Baisse -{taux_pct}% ({prix_suggere}€)"
                        baisse_prix_taux = taux_pct
                    else:
                        action_label = "📸 MAUVAISE PERF : Changer Photos (Plancher atteint)"
                        # baisse_prix_taux reste None -- exclu de toute baisse automatique

            elif is_statut_quo:
                action_label = "⚖️ STATUT QUO : Analyse Prix/Photos"
                display_statut_quo = True

            else:
                action_label = "✅ OK"

            # --- CONSTRUCTION DU DICT FINAL ---
            articles.append({
                "id":                   item["id"],
                "nom":                  item_name,
                "rang":                 rang,
                "prix_achat":           prix_achat,
                "prix_vente":           prix_vente,
                "prix_plancher":        prix_plancher,
                "prix_plancher_absolu": prix_plancher_absolu,
                "marge_dispo":          round(marge_dispo, 2),
                "prix_optimiste":       prix_optimiste,
                "needs_agent_decision": locals().get("needs_agent_decision", False),
                "score":                score,
                "photo_url":            item.get("url_image", ""),
                "action_label":         action_label,
                "baisse_prix_taux":     baisse_prix_taux,
                "pepite_taux_suggere":  pepite_taux_suggere,
                "pepite_taux_raison":   pepite_taux_raison,
                "baisse_prix_raison":   baisse_prix_raison,
                "is_audit":             is_audit,
                "is_critical":          is_critical,
                "is_very_old":          is_very_old,
                "needs_republish_action": needs_republish_action,
                "liquidation_phase":                liquidation_phase,
                "liquidation_prix_cible":            liquidation_prix_cible,
                "liquidation_needs_action":           liquidation_needs_action,
                "liquidation_pourcentage_necessaire": liquidation_pourcentage_necessaire,
                "jours_en_vente":       jours_en_vente,
                "jours_en_ligne":       jours_en_ligne,
                "jours_restants":       max(0, delai_max - jours_en_ligne),
                "is_stuck":             display_pepite,
                "is_low_perf":          display_low_perf,
                "is_statut_quo":        display_statut_quo,
                "delai_max":            delai_max,
                "v_tot":                v_tot,
                "f_tot":                f_tot,
                "v_reel":               v_reel,
                "f_reel":               f_reel,
                "is_new":               is_new,
                "is_invisible":         is_invisible,
                "needs_time_republish": needs_time_republish,
                "ticket_type":          "HIGH" if prix_vente >= self.LIMITE_LOW_TICKET else "LOW",
                "dressing":             item.get("dressing", "Inconnu"),
                "is_done":              item.get("est_traite", False),
                "is_vendu":             item.get("est_vendu", False),
                "date_de_publication":  date_pub,
                "date_de_republication": date_repub,
                "erreur_clemz":         item.get("erreur_clemz", False),
                "erreur_clemz_reason":  item.get("erreur_clemz_reason"),
                "exclu_liquidation_saisonnier": item.get("exclu_liquidation_saisonnier", False),
                "est_personnel_ou_ami": item.get("est_personnel_ou_ami", False),
                "report_republication_jusqu_au": item.get("report_republication_jusqu_au"),
            })

        articles.sort(key=lambda x: x["nom"].lower())
        return articles

    # ------------------------------------------------------------------
    # ARTICLES — MISE À JOUR
    # ------------------------------------------------------------------

    def update_article(self, article_id: str, fields: dict):
        """
        Met à jour les champs d'un article dans Supabase.
        fields : dict des colonnes à mettre à jour, ex: {"est_traite": True}
        """
        self.db.table("articles").update(fields).eq("id", article_id).execute()

    def get_articles_baisses_recentes(self, article_ids, jours_cooldown=7):
        """
        Retourne l'ensemble des article_id ayant reçu une baisse de prix dans
        les derniers `jours_cooldown` jours (price_drops) -- sert à exclure ces
        articles d'un nouveau lot de baisse automatique, le temps de laisser au
        marché l'occasion de réagir avant d'éroder encore le prix.
        """
        from datetime import datetime, timedelta
        if not article_ids:
            return set()
        cutoff = (datetime.now() - timedelta(days=jours_cooldown)).isoformat()
        response = self.db.table("price_drops") \
            .select("article_id") \
            .in_("article_id", article_ids) \
            .gte("created_at", cutoff) \
            .execute()
        return {row["article_id"] for row in response.data or [] if row.get("article_id")}
    
    def update_article_by_name(self, nom: str, fields: dict):
        """
        Met à jour un article par son nom (insensible à la casse).
        Utilisé par la logique de republication par nom.
        """
        self.db.table("articles") \
            .update(fields) \
            .ilike("nom", nom) \
            .execute()

    def republish_by_name_logic(self, nom: str):
        """
        Met à jour la date de republication à aujourd'hui et remet est_traite
        à False -- confirmation manuelle (bouton "Republication OK" sur une
        carte) qu'un article a été republié à la main sur Vinted.

        Loggée dans TaskHistory (jusqu'ici cette confirmation ne touchait que
        Supabase, jamais l'historique -- angle mort identique à celui de la
        création de brouillons, cf. échange du 08/09/2026) ET dans
        automation_actions_log (risk_guard.py) -- sinon une republication
        manuelle reste invisible pour le quota du jour et le compteur de
        jours actifs consécutifs, cf. échange du 09/09/2026.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        result = self.db.table("articles") \
            .update({
                "date_republication": today_str,
                "est_traite": False
            }) \
            .ilike("nom", nom) \
            .execute()

        if result.data:
            from services.automation_scheduler import start_task_run, update_task_result, finish_task_run
            from services.risk_guard import log_action

            article = result.data[0]
            task_id = start_task_run("republication", [
                {"id": article.get("id"), "nom": article.get("nom") or nom, "dressing": article.get("dressing"), "taux": None}
            ])
            update_task_result(task_id, article.get("id"), status="success", reason="Confirmation manuelle (bouton \"Republication OK\")")
            finish_task_run(task_id)

            if article.get("dressing"):
                log_action(article["dressing"], "republication", 1)
                from services.planification_republication import rafraichir_pause_si_necessaire
                rafraichir_pause_si_necessaire(article["dressing"])

            return {"status": "success", "updated_date": today_str}
        return {"status": "error", "message": "Article non trouvé"}


    # ------------------------------------------------------------------
    # FILTRES SOURCING
    # ------------------------------------------------------------------

    def fetch_filters(self):
        """Récupère tous les filtres actifs de sourcing depuis Supabase."""
        response = self.db.table("filtres_sourcing") \
            .select("*") \
            .eq("actif", True) \
            .execute()
        return response.data


    def create_filter(self, data: dict):
        """Crée un nouveau filtre de sourcing."""
        response = self.db.table("filtres_sourcing").insert(data).execute()
        return response.data[0] if response.data else None

    def update_filter(self, filter_id: int, data: dict):
        """Met à jour un filtre existant."""
        response = self.db.table("filtres_sourcing") \
            .update(data) \
            .eq("id", filter_id) \
            .execute()
        return response.data[0] if response.data else None

    def delete_filter(self, filter_id: int):
        """Supprime définitivement un filtre."""
        self.db.table("filtres_sourcing").delete().eq("id", filter_id).execute()
        return {"status": "deleted", "id": filter_id}

    # ------------------------------------------------------------------
    # ACHATS
    # ------------------------------------------------------------------

    def create_lot_achat(self, date_achat, frais_protection_acheteur, frais_port,
                          articles, montant_porte_monnaie=0, vendeur_vinted=None, notes=None):
        """
        Crée un lot d'achat avec répartition automatique des frais communs
        (protection acheteur + port) au prorata du prix négocié de chaque pièce.

        :param articles: liste de dicts [{"nom": str, "prix_brut": float}, ...]
        :return: dict {lot_id, total_paye, articles: [...avec prix_achat calculé...]}
        """
        if not articles:
            raise ValueError("Un lot doit contenir au moins un article.")

        total_brut = sum(a["prix_brut"] for a in articles)
        frais_communs = round(frais_protection_acheteur + frais_port, 2)
        total_paye = round(total_brut + frais_communs, 2)

        # --- Insertion du lot ---
        lot_response = self.db.table("lots_achats").insert({
            "date_achat":                str(date_achat),
            "vendeur_vinted":             vendeur_vinted,
            "frais_protection_acheteur":  frais_protection_acheteur,
            "frais_port":                 frais_port,
            "montant_porte_monnaie":      montant_porte_monnaie,
            "total_paye":                 total_paye,
            "notes":                      notes,
        }).execute()
        lot_id = lot_response.data[0]["id"]

        # --- Répartition au prorata + insertion de chaque article ---
        articles_enrichis = []
        for art in articles:
            part = art["prix_brut"] / total_brut if total_brut > 0 else 0
            part_frais = round(part * frais_communs, 2)
            prix_achat_final = round(art["prix_brut"] + part_frais, 2)

            self.db.table("achats").insert({
                "lot_id":      lot_id,
                "nom":         art["nom"],
                "prix_brut":   art["prix_brut"],
                "part_frais":  part_frais,
                "prix_achat":  prix_achat_final,
            }).execute()

            articles_enrichis.append({
                "nom":         art["nom"],
                "prix_brut":   art["prix_brut"],
                "part_frais":  part_frais,
                "prix_achat":  prix_achat_final,
                "photo_url":   self._get_article_images().get(art["nom"].lower()),
            })

        return {
            "lot_id":     lot_id,
            "total_paye": total_paye,
            "articles":   articles_enrichis,
        }

    def _get_article_images(self):
        """
        Récupère les URLs d'image de l'inventaire scrapé, pour associer une photo
        aux achats saisis manuellement via la correspondance de nom (insensible à
        la casse, même logique que _get_purchase_prices()).
        Retourne un dict {nom_lower: url_image}.
        """
        response = self.db.table("articles").select("nom, url_image").execute()
        image_map = {}
        for row in response.data:
            if row.get("nom") and row.get("url_image"):
                image_map[row["nom"].lower()] = row["url_image"]
        return image_map

    def import_ventes_from_csv(self, rows):
        """
        Importe une liste de ventes (déjà parsées depuis un CSV Clemz) dans la
        table "ventes". Pour chaque vente, tente de retrouver le prix d'achat
        correspondant via une correspondance de nom (insensible à la casse)
        sur la table "achats". Si aucune correspondance, prix_achat reste NULL
        — l'article est laissé tel quel, comme demandé.

        :param rows: liste de dicts avec les clés :
            numero_commande, date_achat, date_encaissement, dressing, titre,
            client, pays_acheteur, prix_vente, pourcentage_reduction
        """
        purchase_prices = self._get_purchase_prices()  # {nom_lower: prix_achat}

        payloads = []
        not_matched = []

        for row in rows:
            nom = row["titre"].strip()
            prix_achat = purchase_prices.get(nom.lower())  # None si non trouvé

            if prix_achat is None:
                not_matched.append(nom)

            payloads.append({
                "nom":                    nom,
                "prix_vente":             row["prix_vente"],
                "prix_achat":             prix_achat,
                "date_vente":             row["date_achat"],
                "date_encaissement":      row.get("date_encaissement"),
                "dressing":               row.get("dressing"),
                "numero_commande":        str(row.get("numero_commande")),
                "client":                 row.get("client"),
                "pays_acheteur":          row.get("pays_acheteur"),
                "pourcentage_reduction":  row.get("pourcentage_reduction", 0),
                "source":                 "csv_backfill",
            })

        # Upsert par numero_commande : réimporter le même CSV met à jour plutôt
        # que dupliquer.
        self.db.table("ventes").upsert(payloads, on_conflict="numero_commande").execute()

        return {
            "total_importe":         len(payloads),
            "prix_achat_trouve":     len(payloads) - len(not_matched),
            "prix_achat_non_trouve": not_matched,
        }

    def get_ventes(self):
        """Retourne toutes les ventes, triées de la plus récente à la plus ancienne."""
        response = self.db.table("ventes") \
            .select("*") \
            .order("date_vente", desc=True) \
            .execute()
        return response.data

    def get_lots_achats(self):
        """Retourne tous les lots avec leurs articles (photo incluse si le nom
        correspond à un article de l'inventaire scrapé), triés du plus récent
        au plus ancien."""
        lots_response = self.db.table("lots_achats") \
            .select("*") \
            .order("date_achat", desc=True) \
            .execute()
        lots = lots_response.data
        image_map = self._get_article_images()

        for lot in lots:
            articles_response = self.db.table("achats") \
                .select("nom, prix_brut, part_frais, prix_achat") \
                .eq("lot_id", lot["id"]) \
                .execute()
            articles = articles_response.data
            for art in articles:
                art["photo_url"] = image_map.get(art["nom"].lower())
            lot["articles"] = articles

        return lots

    def upsert_achat(self, nom: str, prix_achat: float):
        """
        Insère ou met à jour un prix d'achat.
        Utilise upsert pour éviter les doublons sur le nom.
        """
        self.db.table("achats").upsert(
            {"nom": nom, "prix_achat": prix_achat},
            on_conflict="nom"
        ).execute()
        return {"status": "success"}

    # ------------------------------------------------------------------
    # UTILITAIRES
    # ------------------------------------------------------------------

    def clear_articles_database(self):
        """
        Supprime tous les articles non vendus (équivalent de clear_scraper_database).
        """
        result = self.db.table("articles") \
            .delete() \
            .eq("est_vendu", False) \
            .execute()
        return {"status": "success", "deleted_count": len(result.data)}

    def capture_score_snapshots(self):
        """
        Prend un snapshot du score de tous les articles actifs.
        À appeler après chaque scraping (14h et 22h).

        Dédup RÉELLE via snapshot_hour + index unique (article_id, snapshot_hour)
        côté Supabase -- un appel en double dans la même heure (ex: /test-extension
        déclenché manuellement près d'un horaire cron réel) écrase l'entrée
        existante au lieu de créer un doublon, via upsert.

        jours_en_ligne calculé EXACTEMENT comme dans get_processed_inventory
        (repli sur date_publication si jamais republié) -- avant ce correctif,
        un article jamais republié était enregistré à "0 jour" en continu ici,
        alors que le dashboard affichait son vrai âge : deux sources incohérentes
        pour le même article, ce qui aurait faussé l'analyse temporelle prévue.
        """
        from datetime import datetime

        try:
            # Récupère tous les articles non vendus
            response = self.db.table("articles") \
                .select("id, vues, favoris, prix_vente, date_publication, date_republication, rang") \
                .eq("est_vendu", False) \
                .execute()

            articles = response.data
            if not articles:
                return 0

            today = datetime.now()
            snapshot_hour = today.strftime("%Y-%m-%d-%H")
            snapshots = []

            for art in articles:
                v_tot = art.get("vues") or 0
                f_tot = art.get("favoris") or 0
                v_reel = max(1, v_tot - self.CLEMZ_BOOST_VUES_MAX)
                f_reel = max(0, f_tot - self.CLEMZ_BOOST_FAVORIS_FIXE)
                score = round(min(100.0, (f_reel / v_reel) * 100), 2) if v_reel >= 5 else 0.0

                # Même logique que get_processed_inventory : repli sur la date de
                # publication si l'article n'a jamais été republié, plutôt que 0.
                date_pub = art.get("date_publication")
                date_repub = art.get("date_republication")
                date_pub_obj = datetime.strptime(date_pub, "%Y-%m-%d") if date_pub else today
                date_repub_obj = datetime.strptime(date_repub, "%Y-%m-%d") if date_repub else date_pub_obj
                jours = max(1, (today - date_repub_obj).days)

                snapshots.append({
                    "article_id": art["id"],
                    "score": score,
                    "vues": v_reel,
                    "favoris": f_reel,
                    "prix": float(art.get("prix_vente") or 0),
                    "jours_en_ligne": jours,
                    "rang": art.get("rang") or 100,
                    "snapshot_hour": snapshot_hour,
                })

            # Upsert (pas insert) : écrase le snapshot existant pour ce couple
            # (article_id, snapshot_hour) au lieu de créer un doublon.
            self.db.table("score_snapshots").upsert(
                snapshots, on_conflict="article_id,snapshot_hour"
            ).execute()
            logger.info(f"📸 {len(snapshots)} snapshots de score enregistrés (heure={snapshot_hour})")
            return len(snapshots)

        except Exception as e:
            logger.error(f"❌ Erreur capture score_snapshots : {e}")
            return 0

    def get_score_trends(self, days=14):
        """
        Retourne l'historique récent de score par article (score_snapshots),
        groupé par article_id -- UN SEUL appel groupé plutôt qu'un par article,
        pour calculer une tendance visuelle (flèche + mini-graphique) côté
        dashboard sans multiplier les requêtes (évite le N+1 côté frontend).
        """
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        response = self.db.table("score_snapshots") \
            .select("article_id, score, captured_at") \
            .gte("captured_at", cutoff) \
            .order("captured_at") \
            .execute()

        trends = {}
        for row in response.data or []:
            aid = row["article_id"]
            trends.setdefault(aid, []).append({"score": row["score"], "captured_at": row["captured_at"]})
        return trends