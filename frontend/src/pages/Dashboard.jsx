import React, { useState, useEffect } from 'react';
import { inventoryService, riskGuardService } from '../services/api';
import toast, { Toaster } from 'react-hot-toast';
import { motion } from 'framer-motion';
import NavigationFilters from '../components/NavigationFilters';
import ProductCard from '../components/ProductCard';
import SystemControlBar from '../components/SystemControlBar';

import RepublishConfirmModal from '../components/RepublishConfirmModal';
import PepitesBaisseConfirmModal from '../components/PepitesBaisseConfirmModal';
import ManualBaisseCart from '../components/ManualBaisseCart'; // NOUVEAU
import RoutineTunnel from '../components/RoutineTunnel';
import { Sparkles, Search, XCircle, PlayCircle, Heart } from 'lucide-react';

// Variable persistante qui survit au changement d'onglet mais pas au F5
let isInitialLoad = true;

let cachedInventory = null;
let cachedStats = null;

export default function Dashboard() {
    const [inventory, setInventory] = useState(cachedInventory || []);
    const [scoreTrends, setScoreTrends] = useState({});
    const [republishFilter, setRepublishFilter] = useState('Tous');
    const [republishDressingFilter, setRepublishDressingFilter] = useState('Tous');
    const [isScraping, setIsScraping] = useState(false);
    const [isSharingViews, setIsSharingViews] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [activeSegment, setActiveSegment] = useState('Tous');
    const [processingIds, setProcessingIds] = useState(new Set());
    const [searchTerm, setSearchTerm] = useState('');
    const [showRoutine, setShowRoutine] = useState(false);
    
    const [stats, setStats] = useState(cachedStats || {});
    const [risqueStatut, setRisqueStatut] = useState({});


    const [sysState, setSysState] = useState({
        last_cron_14h: "---",
        last_cron_22h: "---",
        last_manual_scrap: "---",
        last_clemz_sync: "---",
        is_clemz_running: false,
        status_TEST: "En attente",
        has_error: false,
        session_status: { dressing1: null, dressing2: null },
        reconnect_status: { dressing1: null, dressing2: null }
    });

    const [excludedFromBaisse, setExcludedFromBaisse] = useState(new Set());
    const [excludedFromRepublish, setExcludedFromRepublish] = useState(new Set());
    const [excludedFromPepitesBaisse, setExcludedFromPepitesBaisse] = useState(new Set());
    // Override manuel du repos Niveau 2 pour la republication -- test du
    // 15/09/2026 (nouvelle extension Clemz). Jamais persistant, jamais activé
    // par défaut : coché explicitement à chaque session si besoin.
    const [forcerNiveau2, setForcerNiveau2] = useState(false);

    

    // Plafond de la liste "à republier" par dressing -- reflète le plan du jour
    // (niveau convalescence/repos forcé/calendrier ayant tranché, cf. backend
    // planification_republication.py) plutôt qu'une constante fixe : un
    // dressing au repos aujourd'hui (actif=false) n'a AUCUN article proposé,
    // peu importe combien seraient éligibles par ailleurs.
    const calculerVolumeCible = (dressingStatut, fallback = Infinity) => {
        const plan = dressingStatut?.plan_du_jour;
        if (!plan) return fallback; // pas encore généré aujourd'hui -- aucune limite artificielle, on affiche tout
        // Override manuel et délibéré du repos Niveau 2 (test extension Clemz,
        // cf. échange du 15/09/2026) -- ne s'applique JAMAIS à une convalescence
        // (niveau "convalescence", vraie suspension constatée), seulement au
        // repos statistique "repos_force". Le vrai plafond (quota brut) reste
        // appliqué côté backend quoi qu'il arrive.
        if (!plan.actif && plan.niveau === 'repos_force' && forcerNiveau2) return fallback;
        if (!plan.actif) return 0;
        // plan.volume_cible est la cible FIXE tirée à minuit, jamais décrémentée
        // au fil de la journée -- quota_republication_restant retranche déjà ce
        // qui a été fait aujourd'hui (y compris de petites republications
        // individuelles hors de cet onglet). Sans ce dernier, l'onglet
        // Republications proposait encore jusqu'à volume_cible articles alors
        // qu'une partie du quota du jour était déjà consommée, laissant croire
        // à une marge plus grande que la réelle (cf. échange du 23/09/2026,
        // envoi de 9 bloqué à 5 en cours de route).
        return dressingStatut?.quota_republication_restant ?? plan.volume_cible;
    };
    const MAX_REPUBLISH_D1 = calculerVolumeCible(risqueStatut?.dressing1);
    const MAX_REPUBLISH_D2 = calculerVolumeCible(risqueStatut?.dressing2);

    const articlesToRepublish = (() => {
        const todayISO = new Date().toISOString().slice(0, 10);
        const eligibles = inventory.filter(a => {
            const reportee = a.report_republication_jusqu_au && a.report_republication_jusqu_au > todayISO;
            return a.needs_republish_action &&
                !a.is_vendu &&
                !excludedFromRepublish.has(a.id) &&
                !reportee;
        });

        const d1 = eligibles
            .filter(a => a.dressing === 'Dressing 1')
            .sort((a, b) => b.jours_en_ligne - a.jours_en_ligne)
            .slice(0, MAX_REPUBLISH_D1);
        const d2 = eligibles
            .filter(a => a.dressing === 'Dressing 2')
            .sort((a, b) => b.jours_en_ligne - a.jours_en_ligne)
            .slice(0, MAX_REPUBLISH_D2);

        return [...d1, ...d2];
    })();

    // Pour vérifier rapidement l'appartenance à la file plafonnée, ailleurs
    // (filteredArticles) -- notamment dans l'onglet Republications lui-même.
    const idsRepublishAutorises = new Set(articlesToRepublish.map(a => a.id));

    const articlesToBaisse = inventory.filter(a =>
        a.is_low_perf && a.baisse_prix_taux != null && !a.is_vendu && !a.is_done &&
        !excludedFromBaisse.has(a.id)
    );

    // Mêmes conditions que les branches 'Pépites'/'Audit' de filteredArticles
    // ci-dessous, juste nommées -- réutilisées par la Routine du jour pour ne
    // pas dupliquer les critères.
    const articlesPepites = inventory.filter(a => a.is_stuck && !a.is_done && !a.is_vendu && !excludedFromPepitesBaisse.has(a.id));
    const articlesAudit = inventory.filter(a => a.is_audit && !a.is_vendu);

    // Initialisation
    useEffect(() => {
        if (cachedInventory) {
            isInitialLoad = false;
            setIsLoading(false);
        }
        if (isInitialLoad) {
            fetchData();
            isInitialLoad = false;
        }
        fetchRisqueStatut();
    }, [isScraping]);

    // Plan du jour par dressing (convalescence/repos forcé/calendrier) -- utilisé
    // pour plafonner la liste "à republier" (MAX_REPUBLISH_D1/D2 ci-dessus).
    const fetchRisqueStatut = async () => {
        try {
            const statut = await riskGuardService.getStatut();
            setRisqueStatut(statut);
        } catch {
            // Silencieux -- purement informatif, même logique que fetchScheduledTask
        }
    };

    

    // FETCH SYSTEM STATE (Modifiée pour retourner la donnée)
    const fetchSystemState = async () => {
        try {
            const data = await inventoryService.getSystemState();
            if (data) setSysState(data); // On ne met à jour que si on a reçu une réponse
            return data;
        } catch (err) {
            // On évite le console.error bruyant toutes les 10s si le serveur est off
            return null;
        }
    };

    // Utilisation exclusive des services importés
    const fetchData = async (isSilent = false) => {
        try {
            if (!isSilent && !cachedInventory) setIsLoading(true);
            const [inventoryData, trendsData] = await Promise.all([
                inventoryService.getArticles(),
                inventoryService.getScoreTrends(14).catch(() => ({})), // tendance non bloquante si ça échoue
            ]);

            cachedInventory = inventoryData;
            setInventory(inventoryData);
            setScoreTrends(trendsData);
        } catch (err) {
            console.error("Erreur API :", err);
        } finally {
            setIsLoading(false);
        }
    };

    const handleRunScraper = async () => {
        setIsScraping(true);
        toast.loading("Démarrage du scanner Vinted...", { id: 'scrap-toast' });
        try {
            const response = await fetch('http://localhost:8000/api/test-extension', { method: 'POST' });
            if (response.ok) {
                const monitorInterval = setInterval(async () => {
                    const currentState = await fetchSystemState();
                    const isTerminal = currentState && (
                        currentState.status_TEST === "Synchronisation Réussie" ||
                        currentState.status_TEST?.startsWith("Erreur")
                    );
                    if (isTerminal) {
                        clearInterval(monitorInterval);
                        setIsScraping(false);
                        if (currentState.status_TEST === "Synchronisation Réussie") {
                            toast.success("Synchronisation terminée !", { id: 'scrap-toast' });
                            new Audio('https://audio-previews.elements.envatousercontent.com/files/660379476/preview.mp3').play().catch(() => { });
                            await fetchData();
                        } else {
                            // Le message contient déjà le détail : "Erreur : session expirée (Dressing 1)"
                            toast.error(currentState.status_TEST, { id: 'scrap-toast', duration: 6000 });
                        }
                    }
                }, 2500);
            }
        } catch (err) {
            setIsScraping(false);
            toast.error("Impossible de contacter le serveur.", { id: 'scrap-toast' });
        }
    };

    // dressingKey attendu : "d1" ou "d2"
    const handleReconnect = async (dressingKey) => {
        const label = dressingKey === 'd1' ? 'Dressing 1' : 'Dressing 2';
        toast.loading(`Ouverture du navigateur pour ${label}...`, { id: `reco-${dressingKey}` });
        try {
            const response = await fetch(`http://localhost:8000/api/reconnect-session/${dressingKey}`, { method: 'POST' });
            if (!response.ok) throw new Error('Erreur serveur');

            const stateKey = dressingKey === 'd1' ? 'dressing1' : 'dressing2';
            const monitorInterval = setInterval(async () => {
                const currentState = await fetchSystemState();
                const status = currentState?.reconnect_status?.[stateKey];
                if (status && status !== 'en_cours') {
                    clearInterval(monitorInterval);
                    if (status === 'succes') {
                        toast.success(`${label} reconnecté !`, { id: `reco-${dressingKey}` });
                    } else {
                        toast.error(`Échec de reconnexion pour ${label}.`, { id: `reco-${dressingKey}` });
                    }
                }
            }, 3000);
        } catch (err) {
            toast.error(`Impossible de contacter le serveur pour ${label}.`, { id: `reco-${dressingKey}` });
        }
    };

    const handleRunPartageVuesFavoris = async (dressing) => {
        setIsSharingViews(true);
        toast.loading(`Lancement du partage vues/favoris (${dressing})...`, { id: 'partage-toast' });
        try {
            const response = await fetch('http://localhost:8000/api/test-partage-vues-favoris', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dressing }),
            });
            if (response.ok) {
                toast.success(`Partage vues/favoris (${dressing}) lancé en arrière-plan !`, { id: 'partage-toast' });
            } else {
                throw new Error("Erreur serveur");
            }
        } catch (err) {
            toast.error("Impossible de contacter le serveur.", { id: 'partage-toast' });
        } finally {
            setIsSharingViews(false);
        }
    };

    


    const [isForcingRepublish, setIsForcingRepublish] = useState(false);
    const [isForcingBaisse, setIsForcingBaisse] = useState(false);
    const [isForcingPepites, setIsForcingPepites] = useState(false);
    const [showRepublishModal, setShowRepublishModal] = useState(false);
    const [modalItems, setModalItems] = useState([]);
    const [showPepitesModal, setShowPepitesModal] = useState(false);
    const [pepitesModalItems, setPepitesModalItems] = useState([]);
    const [selectedForManualBaisse, setSelectedForManualBaisse] = useState(new Set());
    // Taux par article (id -> pourcentage), pas un taux unique pour tout le
    // panier -- pré-rempli à l'ajout avec la suggestion de l'article (tranche
    // pépite ou mauvaise perf), toujours modifiable ensuite dans le panier
    // (cf. échange du 10/09/2026).
    const [manualBaissePourcentages, setManualBaissePourcentages] = useState({});
    const [isLaunchingManualBaisse, setIsLaunchingManualBaisse] = useState(false);
    const handleForceRunNow = async (taskType, itemsOverride = null) => {
        const items = itemsOverride || (taskType === 'republication' ? articlesToRepublish : articlesToBaisse);
        if (items.length === 0) {
            toast.error("Aucun article disponible pour ce test.");
            return;
        }
        const setBusy = taskType === 'republication' ? setIsForcingRepublish : setIsForcingBaisse;
        setBusy(true);
        try {
            const response = await fetch('http://localhost:8000/api/automation/run-now', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_type: taskType,
                    produits: items.map(p => ({ id: p.id, nom: p.nom, dressing: p.dressing })),
                    // Étendu à baisse_prix le 26/09/2026 -- override manuel et délibéré,
                    // exceptionnel, demandé explicitement par l'utilisateur (jusqu'ici
                    // réservé à la republication depuis le test du 15/09/2026).
                    ...(forcerNiveau2 ? { forcer_niveau2: true } : {}),
                })
            });
            if (response.ok) {
                toast.success(`Test "${taskType}" lancé immédiatement pour ${items.length} article(s)`);
            } else {
                const data = await response.json();
                throw new Error(data.detail || 'Erreur lors du lancement immédiat');
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`);
        } finally {
            setBusy(false);
        }
    };

    // Baisse de prix forcée pour l'onglet Pépites -- contrairement à
    // "Forcer baisse de prix" (articlesToBaisse, is_low_perf), les pépites
    // n'ont pas de baisse_prix_taux (jamais automatisées via run_baisse_prix_auto)
    // et chacune porte son propre pourcentage suggéré (tranche de prix,
    // cf. pepite_taux_suggere) -- on l'envoie explicitement par article pour
    // que le backend respecte bien ces taux différents plutôt qu'un taux
    // unique pour tout le lot (cf. échange du 13/09/2026). Passe par une
    // modale de confirmation (comme la republication) pour pouvoir retirer
    // les articles qu'on ne veut pas traiter avant le lancement.
    const handleOpenPepitesModal = () => {
        const items = articlesPepites.filter(a => a.pepite_taux_suggere != null);
        if (items.length === 0) {
            toast.error("Aucune pépite avec un taux suggéré disponible.");
            return;
        }
        setPepitesModalItems(items);
        setShowPepitesModal(true);
    };

    const handleRemoveFromPepitesModal = (id) => {
        setPepitesModalItems(prev => prev.filter(a => a.id !== id));
        // Persiste l'exclusion au-delà de cette seule session de modal --
        // sinon l'article réapparaît à la prochaine ouverture (même logique
        // que la republication, cf. handleRemoveFromModal).
        setExcludedFromPepitesBaisse(prev => new Set(prev).add(id));
    };

    const handleConfirmPepitesModal = async () => {
        const items = pepitesModalItems;
        if (items.length === 0) {
            setShowPepitesModal(false);
            return;
        }
        setIsForcingPepites(true);
        try {
            const response = await fetch('http://localhost:8000/api/automation/run-now', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_type: 'baisse_prix',
                    produits: items.map(p => ({ id: p.id, nom: p.nom, dressing: p.dressing, pourcentage: p.pepite_taux_suggere })),
                    // Même correctif que le panier manuel (cf. échange du 27/09/2026).
                    ...(forcerNiveau2 ? { forcer_niveau2: true } : {}),
                })
            });
            if (response.ok) {
                toast.success(`Baisse de prix pépites lancée pour ${items.length} article(s), chacun à son taux.`);
                setExcludedFromPepitesBaisse(prev => {
                    const next = new Set(prev);
                    items.forEach(p => next.add(p.id));
                    return next;
                });
                setShowPepitesModal(false);
            } else {
                const data = await response.json();
                throw new Error(data.detail || 'Erreur lors du lancement immédiat');
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`);
        } finally {
            setIsForcingPepites(false);
        }
    };

    const handleOpenRepublishModal = () => {
        if (articlesToRepublish.length === 0) {
            toast.error("Aucun article disponible pour ce test.");
            return;
        }
        setModalItems(articlesToRepublish);
        setShowRepublishModal(true);
    };

    const toggleManualBaisse = (id) => {
        setSelectedForManualBaisse(prev => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
                // Pré-remplit avec le taux suggéré pour CET article (mauvaise
                // perf 10/20%, ou tranche de prix pépite) -- sinon le défaut
                // historique de 20%. Reste modifiable dans le panier.
                const art = inventory.find(a => a.id === id);
                const suggestion = art?.baisse_prix_taux ?? art?.pepite_taux_suggere ?? 20;
                setManualBaissePourcentages(p => ({ ...p, [id]: suggestion }));
            }
            return next;
        });
    };

    const setManualBaisseItemPourcentage = (id, pourcentage) => {
        setManualBaissePourcentages(prev => ({ ...prev, [id]: pourcentage }));
    };

    const handleLaunchManualBaisse = async () => {
        const items = inventory.filter(a => selectedForManualBaisse.has(a.id));
        if (items.length === 0) return;
        setIsLaunchingManualBaisse(true);
        try {
            const response = await fetch('http://localhost:8000/api/automation/run-now', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_type: 'baisse_prix',
                    produits: items.map(p => ({
                        id: p.id,
                        nom: p.nom,
                        dressing: p.dressing,
                        pourcentage: manualBaissePourcentages[p.id] ?? 20,
                    })),
                    // Manquait ici (cf. échange du 27/09/2026) -- seul le bouton "Test
                    // immédiat" transmettait ce drapeau, pas le panier rempli via le "+"
                    // sur chaque carte, qui appelle ce handler séparé.
                    ...(forcerNiveau2 ? { forcer_niveau2: true } : {}),
                })
            });
            if (response.ok) {
                toast.success(`Baisse de prix lancée pour ${items.length} article(s).`);
                setSelectedForManualBaisse(new Set());
                setManualBaissePourcentages({});
            } else {
                const data = await response.json();
                throw new Error(data.detail || 'Erreur lors du lancement');
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`);
        } finally {
            setIsLaunchingManualBaisse(false);
        }
    };

    const handleRemoveFromModal = (id) => {
        setModalItems(prev => prev.filter(a => a.id !== id));
        // Persiste l'exclusion au-delà de cette seule session de modal -- sinon
        // l'article réapparaît à la prochaine ouverture, articlesToRepublish
        // étant recalculé depuis inventory à chaque fois.
        setExcludedFromRepublish(prev => new Set(prev).add(id));
    };

    const handleConfirmRepublishModal = async () => {
        const idsEnvoyes = modalItems.map(a => a.id);
        await handleForceRunNow('republication', modalItems);
        // Les articles envoyés ne peuvent être retirés de la liste qu'au
        // prochain vrai scraping (needs_republish_action recalculé côté
        // backend) -- en attendant, on les exclut localement pour éviter
        // qu'ils réapparaissent identiques à la prochaine ouverture.
        setExcludedFromRepublish(prev => {
            const next = new Set(prev);
            idsEnvoyes.forEach(id => next.add(id));
            return next;
        });
        setShowRepublishModal(false);
    };

    // Fonctions de traitement
    const handleActionTraitement = async (id) => {
        try {
            const response = await fetch(`http://localhost:8000/api/treat/${id}`, { method: 'PATCH' });
            if (response.ok) {
                setInventory(prev => prev.map(art => art.id === id ? { ...art, is_done: true } : art));
            }
        } catch (err) { console.error(err); }
    };

    // handleToggleExclusionSaisonniere / handleToggleMarquerPersonnel déplacés
    // vers la page Liquidation.jsx dédiée (consolidation du 07/09/2026) --
    // l'onglet Liquidation de ce Dashboard n'existe plus.

    const handleReporterRepublication = async (id) => {
        try {
            const response = await fetch(`http://localhost:8000/api/reporter-republication/${id}`, { method: 'PATCH' });
            if (response.ok) {
                // Calculée côté frontend plutôt que lue depuis la réponse backend --
                // évite toute dépendance au format exact renvoyé par
                // supabase_svc.update_article() (constaté : la carte ne disparaissait
                // pas sans refresh, la valeur lue restait undefined).
                const demain = new Date();
                demain.setDate(demain.getDate() + 1);
                const demainISO = demain.toISOString().slice(0, 10);
                setInventory(prev => prev.map(art => art.id === id ? { ...art, report_republication_jusqu_au: demainISO } : art));
                toast.success("Republication reportée à demain.");
            } else {
                throw new Error("Échec du report de republication");
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`);
        }
    };

    const handleRepublish = async (nom, id) => {
        setProcessingIds(prev => new Set(prev).add(id));
        try {
            const response = await fetch('http://localhost:8000/api/republish-by-name', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ nom })
            });

            if (response.ok) {
                // ✅ SUCCÈS : Notification positive
                toast.success(`${nom} : Date de republication mise à jour !`, {
                    style: { background: '#10B981', color: '#fff', fontWeight: 'bold' }
                });

                // ✅ ON RETIRE l'article de la liste (il disparaît du Dashboard)
                setInventory(prev => prev.filter(art => art.id !== id));
            } else {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || 'Erreur lors de la mise à jour');
            }
        } catch (err) {
            // ❌ ÉCHEC : Notification d'erreur
            toast.error(`Échec : ${err.message}`);
        } finally {
            setProcessingIds(prev => {
                const next = new Set(prev);
                next.delete(id);
                return next;
            });
        }
    };

    // Filtrage
    // Filtrage intelligent et ultra-sécurisé
    const filteredArticles = inventory.filter(art => {

        if (art.is_vendu) return false;
        // 1. Recherche par texte (prioritaire)
        if (searchTerm.trim() !== '') return art.nom.toLowerCase().includes(searchTerm.toLowerCase());

        // 2. Onglet "Tous" : On affiche tout sans exception
        if (activeSegment === 'Tous') return true;

        if (activeSegment === 'Audit') {
            return art.is_audit;
        }

        // 3. Onglet "Republications" : On affiche les urgences, MÊME SI l'article a déjà eu une baisse de prix
        if (activeSegment === 'Republications' || activeSegment === 'Republish') {

            if (excludedFromRepublish.has(art.id)) return false; // exclusion manuelle -- retiré de la liste, pas juste du compteur

            const todayISO = new Date().toISOString().slice(0, 10);
            if (art.report_republication_jusqu_au && art.report_republication_jusqu_au > todayISO) return false;

            // Permissif : laisse passer les vrais candidats à republication ET les
            // articles is_very_old/liquidation -- ces derniers ne seront affichés
            // que via le sous-filtre "Urgent" ci-dessous, pas dans "Tous".
            const isBaseRepublish = idsRepublishAutorises.has(art.id);
            if (!isBaseRepublish) return false;

            // Filtre dressing, cumulable avec le sous-filtre par tag ci-dessous
            if (republishDressingFilter !== 'Tous' && art.dressing !== republishDressingFilter) return false;

            // Application du sous-filtre par tag
            if (republishFilter === 'Invisible') return art.is_invisible;
            if (republishFilter === 'Baisse') return art.action_label?.includes("BAISSE");
            if (republishFilter === 'Standard') return art.action_label?.includes("Même prix");

            return true; // 'Tous'
        }

        // 4. Pour le reste : On cache formellement si c'est déjà traité
        if (art.is_done) return false;
        // 5. Distribution stricte dans les autres onglets
        if (activeSegment === 'Pépites') return art.is_stuck && !excludedFromPepitesBaisse.has(art.id);
        if (activeSegment === 'Baisse de prix') return art.is_low_perf || art.action_label?.includes("REPUBLIER avec BAISSE");

        // SÉCURITÉ MAXIMALE : On cache tout ce qui ne rentre pas dans les cases au-dessus
        return false;

    }).sort((a, b) => {
        // --- NOUVEAU : LOGIQUE DE TRI ---
        if (activeSegment === 'Republications' || activeSegment === 'Republish') {
            // Trie par "jours_en_ligne" du plus grand au plus petit
            // Les articles en ligne depuis le plus longtemps (ex: 22j) seront tout en haut !
            return b.jours_en_ligne - a.jours_en_ligne;
        }

        // Pour les autres onglets, tri alphabétique par défaut
        return a.nom.localeCompare(b.nom);
    });

    const toggleExcludeBaisse = (id) => {
        setExcludedFromBaisse(prev => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            return next;
        });
    };

    const toggleExcludeRepublish = (id) => {
        setExcludedFromRepublish(prev => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            return next;
        });
    };

    const segments = [
        { id: 'Tous', label: `📂 Tous (${inventory.filter(a => !a.is_vendu).length})`, count: inventory.filter(a => !a.is_vendu).length },
        // Regroupe TOUT ce qui nécessite une baisse de prix -- Mauvaise Performance
        // pure, et les articles qui doivent aussi être republiés/partagés (routine :
        // baisse de prix systématique avant republication/partage vues-favoris).
        // Même critère qu'avant (is_low_perf), seul le libellé change -- la
        // séparation visuelle en deux sous-sections se fait à l'affichage, pas ici.
        { id: 'Baisse de prix', label: `📉 Baisse de prix (${inventory.filter(a => (a.is_low_perf || a.action_label?.includes("REPUBLIER avec BAISSE")) && !a.is_done && !a.is_vendu).length})`, count: inventory.filter(a => (a.is_low_perf || a.action_label?.includes("REPUBLIER avec BAISSE")) && !a.is_done && !a.is_vendu).length },
        {
            id: 'Republications',
            label: `♻️ Republish (${articlesToRepublish.length})`,
            count: inventory.filter(a => a.needs_republish_action && !a.is_vendu).length
        },
        { id: 'Pépites', label: `💎 Pépites (${articlesPepites.length})`, count: articlesPepites.length },
        { id: 'Audit', label: `🔍 À auditer (${articlesAudit.length})`, count: articlesAudit.length }
    ];

    // Étapes de la Routine du jour -- même 3 listes que les onglets ci-dessus,
    // aucune logique de filtrage dupliquée.
    const etapesRoutine = [
        { id: 'Baisse de prix', titre: '📉 Baisses de prix à faire', articles: articlesToBaisse, emptyLabel: 'Aucune baisse de prix en attente 🎉' },
        { id: 'Pépites', titre: '💎 Pépites à analyser', articles: articlesPepites, emptyLabel: "Aucune pépite à analyser pour l'instant." },
        { id: 'Audit', titre: '🔍 Produits à auditer', articles: articlesAudit, emptyLabel: 'Rien à auditer -- tout est propre !' },
    ];

    // Carte produit réutilisée PARTOUT (liste normale, split Baisse de prix,
    // Routine du jour) -- segment permet de forcer un activeSegment différent
    // de l'onglet actuellement affiché (nécessaire pour la Routine, qui reste
    // montée par-dessus le Dashboard peu importe l'onglet sélectionné derrière).
    const renderCard = (art, segment = activeSegment) => (
        <ProductCard
            key={art.id}
            art={art}
            activeSegment={segment}
            processingIds={processingIds}
            onTreat={handleActionTraitement}
            onRepublish={handleRepublish}
            isExcludedBaisse={excludedFromBaisse.has(art.id)}
            onToggleExcludeBaisse={() => toggleExcludeBaisse(art.id)}
            isSelectedManualBaisse={selectedForManualBaisse.has(art.id)}
            onToggleManualBaisse={toggleManualBaisse}
            isExcludedRepublish={excludedFromRepublish.has(art.id)}
            onToggleExcludeRepublish={() => toggleExcludeRepublish(art.id)}
            onReporterRepublication={handleReporterRepublication}
            trend={scoreTrends[art.id]}
        />
    );


    return (
        <div className="p-6 max-w-8xl mx-auto">
            <Toaster position="bottom-right" /> {/* <--- AJOUT ICI */}
            <header className="mb-10">
                <div className="flex items-center justify-between mb-6 gap-6">
                    <div className="flex items-center gap-6 flex-1 min-w-0">
                        <h1 className="text-2xl font-black tracking-tight shrink-0">Pilotage Stock</h1>

                        {!showRoutine && (
                            <div className="relative flex-1 max-w-md">
                                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                    <Search size={16} className="text-slate-400" />
                                </div>
                                <input
                                    type="text"
                                    placeholder="Rechercher un produit dans l'inventaire..."
                                    value={searchTerm}
                                    onChange={(e) => setSearchTerm(e.target.value)}
                                    className="block w-full pl-10 pr-10 py-2.5 border border-slate-100 bg-slate-50 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-brand-400 transition-all"
                                />
                                {searchTerm && (
                                    <button
                                        onClick={() => setSearchTerm('')}
                                        className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-red-500"
                                    >
                                        <XCircle size={14} />
                                    </button>
                                )}
                            </div>
                        )}
                    </div>

                    {!showRoutine && (
                        <div className="flex items-center gap-3 shrink-0">
                            {/* BOUTON DE LANCEMENT */}
                            <button
                                onClick={handleRunScraper}
                                disabled={isScraping}
                                className={`flex items-center gap-2 px-6 py-2 rounded-lg transition-all ${isScraping
                                        ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                                        : 'bg-emerald-500 text-white hover:scale-105 shadow-lg shadow-emerald-100'
                                    }`}
                            >
                                {isScraping ? (
                                    <>
                                        <div className="animate-spin h-4 w-4 border-2 border-slate-400 border-t-transparent rounded-full" />
                                        <span className="text-[10px] uppercase font-black">Action en cours...</span>
                                    </>
                                ) : (
                                    <>
                                        <PlayCircle size={16} />
                                        <span className="text-xs font-black tracking-wide">Lancer Scraping</span>
                                    </>
                                )}
                            </button>

                            {/* BOUTON TEST : PARTAGE VUES/FAVORIS */}
                            <button
                                onClick={() => handleRunPartageVuesFavoris('Dressing 1')}
                                disabled={isSharingViews}
                                title="Lance le partage de vues/favoris Clemz pour Dressing 1 (peut durer jusqu'à ~1h)"
                                className={`flex items-center gap-2 px-5 py-2 rounded-lg transition-all ${isSharingViews
                                        ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                                        : 'bg-pink-500 text-white hover:scale-105 shadow-lg shadow-pink-100'
                                    }`}
                            >
                                {isSharingViews ? (
                                    <div className="animate-spin h-4 w-4 border-2 border-slate-400 border-t-transparent rounded-full" />
                                ) : (
                                    <Heart size={16} />
                                )}
                                <span className="text-xs font-black tracking-wide">Vues/Favoris D1</span>
                            </button>

                            <button
                                onClick={() => handleRunPartageVuesFavoris('Dressing 2')}
                                disabled={isSharingViews}
                                title="Lance le partage de vues/favoris Clemz pour Dressing 2 (peut durer jusqu'à ~1h)"
                                className={`flex items-center gap-2 px-5 py-2 rounded-lg transition-all ${isSharingViews
                                        ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                                        : 'bg-pink-500 text-white hover:scale-105 shadow-lg shadow-pink-100'
                                    }`}
                            >
                                {isSharingViews ? (
                                    <div className="animate-spin h-4 w-4 border-2 border-slate-400 border-t-transparent rounded-full" />
                                ) : (
                                    <Heart size={16} />
                                )}
                                <span className="text-xs font-black tracking-wide">Vues/Favoris D2</span>
                            </button>

                            <button
                                onClick={() => setShowRoutine(true)}
                                className="flex items-center gap-2 px-5 py-2 bg-gradient-to-r from-brand-500 to-brand-600 text-white rounded-lg font-black uppercase text-xs hover:scale-105 transition-all shadow-soft-lg active:scale-95"
                            >
                                <Sparkles size={16} /> Routine du jour
                            </button>
                        </div>
                    )}
                </div>

                {showRoutine && (
                    <RoutineTunnel
                        etapes={etapesRoutine}
                        renderCard={renderCard}
                        onClose={() => setShowRoutine(false)}
                    />
                )}

                {!showRoutine && (
                <>
                {/* 1. Barre Système unifiée (Bouton Scrap + Recherche + Vider Base) */}
                <SystemControlBar
                    sysState={sysState}
                    handleReconnect={handleReconnect}
                />

                {/* 🧪 ZONE DE TEST — déclenchement immédiat, hors programmation créneau */}
                <div className="flex items-center gap-3 mt-3 mb-3 p-3 bg-slate-50 border border-dashed border-slate-300 rounded-xl">
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-wider">🧪 Test immédiat :</span>
                    <button
                        onClick={handleOpenRepublishModal}
                        disabled={isForcingRepublish || articlesToRepublish.length === 0}
                        className="px-4 py-2 bg-blue-500 text-white rounded-lg text-xs font-black uppercase disabled:opacity-40"
                    >
                        {isForcingRepublish ? '...' : `♻️ Forcer republication (${articlesToRepublish.length})`}
                    </button>
                    {/* Étendu le 26/09/2026 (cf. échange) : ne vérifiait QUE le niveau du
                        plan de republication -- une fois celui-ci réactivé en "continuite",
                        la case disparaissait complètement, empêchant tout override pour la
                        baisse de prix (bloquée séparément, jamais reflétée dans ce niveau). */}
                    {[risqueStatut?.dressing1, risqueStatut?.dressing2].some(
                        d => (d?.plan_du_jour?.niveau === 'repos_force' && !d?.plan_du_jour?.actif)
                            || d?.quota_baisse_prix_restant === 0
                    ) && (
                        <label
                            title="Outrepasse le repos Niveau 2 (coupure sur volume glissant) -- jamais la convalescence. Test du 15/09/2026 avec la nouvelle extension Clemz (republication), étendu à la baisse de prix le 26/09/2026 sur demande explicite. Le quota brut reste appliqué."
                            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-[10px] font-black uppercase cursor-pointer border transition-all ${forcerNiveau2
                                ? 'bg-amber-100 text-amber-700 border-amber-300'
                                : 'bg-white text-slate-400 border-slate-200 hover:border-amber-300'
                                }`}
                        >
                            <input
                                type="checkbox"
                                checked={forcerNiveau2}
                                onChange={e => setForcerNiveau2(e.target.checked)}
                                className="accent-amber-600"
                            />
                            ⚠️ Forcer malgré repos (Niveau 2)
                        </label>
                    )}
                    <button
                        onClick={() => handleForceRunNow('baisse_prix')}
                        disabled={isForcingBaisse || articlesToBaisse.length === 0}
                        className="px-4 py-2 bg-orange-500 text-white rounded-lg text-xs font-black uppercase disabled:opacity-40"
                    >
                        {isForcingBaisse ? '...' : `📉 Forcer baisse de prix (${articlesToBaisse.length})`}
                    </button>
                    {/* Lancement limité à un seul dressing (08/10/2026) -- le backend
                        (run_baisse_prix_auto) ne traite que les IDs envoyés, l'autre
                        compte n'est donc jamais ouvert. */}
                    {['Dressing 1', 'Dressing 2'].map(dressing => {
                        const items = articlesToBaisse.filter(a => a.dressing === dressing);
                        return (
                            <button
                                key={dressing}
                                onClick={() => handleForceRunNow('baisse_prix', items)}
                                disabled={isForcingBaisse || items.length === 0}
                                title={`Baisse de prix uniquement sur ${dressing}`}
                                className="px-3 py-2 bg-white text-orange-600 border border-orange-200 rounded-lg text-xs font-black uppercase hover:bg-orange-50 disabled:opacity-40"
                            >
                                {isForcingBaisse ? '...' : `📉 ${dressing.replace('Dressing ', 'D')} (${items.length})`}
                            </button>
                        );
                    })}
                    {activeSegment === 'Pépites' && (
                        <button
                            onClick={handleOpenPepitesModal}
                            disabled={isForcingPepites || articlesPepites.length === 0}
                            title="Applique à chaque pépite son propre pourcentage suggéré (tranche de prix), pas un taux unique pour tout le lot"
                            className="px-4 py-2 bg-cyan-600 text-white rounded-lg text-xs font-black uppercase disabled:opacity-40"
                        >
                            {isForcingPepites ? '...' : `💎 Forcer baisse (pépites) (${articlesPepites.length})`}
                        </button>
                    )}
                </div>

                {/* 3. Navigation (segments seuls) */}
                <NavigationFilters
                    segments={segments}
                    activeSegment={activeSegment}
                    setActiveSegment={setActiveSegment}
                    searchTerm={searchTerm}
                    setSearchTerm={setSearchTerm}
                />
                </>
                )}
            </header>

            {!showRoutine && (
            <>
            {/* 🟢 AJOUT : Barre de sous-filtres par tags cliquables (Uniquement en mode Republications) */}
            {activeSegment === 'Republications' && (
                <div className="flex items-center gap-2 mb-6 bg-slate-50 p-3 rounded-2xl border border-slate-100 shadow-sm animate-in fade-in slide-in-from-top-2 duration-500">
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest ml-2 mr-4">Filtrer par urgence :</span>
                    {[
                        { id: 'Tous', label: 'Tout', color: 'bg-slate-200 text-slate-700' },
                        { id: 'Invisible', label: '👻 Invisible', color: 'bg-purple-100 text-purple-700 border-purple-200' },
                        { id: 'Baisse', label: '📉 Republication + Baisse', color: 'bg-orange-100 text-orange-700 border-orange-200' },
                        { id: 'Standard', label: '♻️ Republication Simple', color: 'bg-blue-100 text-blue-700 border-blue-200' },
                    ].map(tag => (
                        <button
                            key={tag.id}
                            onClick={() => setRepublishFilter(tag.id)}
                            className={`px-4 py-2 rounded-lg text-[11px] font-black border transition-all active:scale-95 ${republishFilter === tag.id
                                ? `${tag.color} ring-2 ring-offset-2 ring-slate-200 shadow-sm`
                                : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                                }`}
                        >
                            {tag.label}
                        </button>
                    ))}

                    <div className="w-px self-stretch bg-slate-200 mx-2" />

                    {[
                        { id: 'Dressing 1', label: 'Dressing 1', color: 'bg-blue-100 text-blue-700 border-blue-200' },
                        { id: 'Dressing 2', label: 'Dressing 2', color: 'bg-purple-100 text-purple-700 border-purple-200' },
                    ].map(tag => (
                        <button
                            key={tag.id}
                            onClick={() => setRepublishDressingFilter(prev => prev === tag.id ? 'Tous' : tag.id)}
                            className={`px-4 py-2 rounded-lg text-[11px] font-black border transition-all active:scale-95 ${republishDressingFilter === tag.id
                                ? `${tag.color} ring-2 ring-offset-2 ring-slate-200 shadow-sm`
                                : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                                }`}
                        >
                            {tag.label}
                        </button>
                    ))}
                </div>
            )}

            {/* Encart : répartition par dressing. Masqué sur "Tous" (pas de sens
                pour une vue qui n'est pas centrée sur la republication). Dans
                l'onglet Republications, reflète le sous-filtre actif
                (Invisible/Baisse/Standard/Urgent) en s'appuyant sur filteredArticles
                (qui applique déjà republishFilter + l'exclusion manuelle). Dans les
                autres onglets (hors Tous), reste sur le total articlesToRepublish
                (ensemble complet des articles à republier, peu importe l'onglet
                affiché). */}
            {activeSegment !== 'Tous' && (() => {
                // Toujours basé sur articlesToRepublish (le vrai total actionnable,
                // celui réellement envoyé au clic sur "Forcer republication") --
                // plus jamais sur filteredArticles, qui peut afficher des articles
                // is_very_old/liquidation n'ayant AUCUN besoin réel de republication
                // (sous-filtre "Urgent"). L'encart ne doit jamais suggérer qu'un
                // article sera republié s'il ne le sera pas.
                return (
                    <div className="flex items-center gap-4 mb-4 px-4 py-2.5 bg-slate-50 rounded-xl border border-slate-100 w-fit">
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">
                            À republier par dressing
                        </span>
                        <span className="flex items-center gap-1.5 text-xs font-bold text-blue-600">
                            <span className="w-2 h-2 rounded-full bg-blue-500" />
                            Dressing 1 : {articlesToRepublish.filter(a => a.dressing === 'Dressing 1').length}
                        </span>
                        <span className="flex items-center gap-1.5 text-xs font-bold text-purple-600">
                            <span className="w-2 h-2 rounded-full bg-purple-500" />
                            Dressing 2 : {articlesToRepublish.filter(a => a.dressing === 'Dressing 2').length}
                        </span>
                    </div>
                );
            })()}

            {/* Liste des produits */}
            <div className="space-y-1">
                {activeSegment === 'Baisse de prix' ? (() => {
                    // Routine : avant toute republication/partage vues-favoris, on
                    // baisse d'abord le prix. "Avant Republication" cible précisément
                    // les articles dont l'action_label contient "REPUBLIER avec BAISSE"
                    // -- PAS art.is_low_perf : ces articles passent par la branche
                    // needs_time_republish/should_force_republish du backend, qui
                    // court-circuite la branche is_low_perf (chaîne elif) avant même
                    // de l'atteindre. baisse_prix_taux reste donc None pour eux --
                    // trou connu, laissé volontairement manuel pour l'instant (pas de
                    // bouton "Baisse" ni d'automatisation possible tant que ce n'est
                    // pas comblé côté backend). Confirmé le 27/08/2026 : ce sont les
                    // mêmes articles que l'onglet "Republier" retrouve via le filtre
                    // 'Baisse' (republishFilter), qui utilise le même critère.
                    const estRepublicationAvecBaisse = (art) => art.action_label?.includes("REPUBLIER avec BAISSE");
                    const mauvaisePerf = filteredArticles.filter((art) => !estRepublicationAvecBaisse(art));
                    const avantRepublication = filteredArticles.filter(estRepublicationAvecBaisse);

                    return (
                        <>
                            <div className="flex items-center gap-3 mb-3">
                                <span className="text-[11px] font-black text-slate-500 uppercase tracking-wide whitespace-nowrap">
                                    📉 Mauvaise Performance ({mauvaisePerf.length})
                                </span>
                                <div className="flex-1 h-px bg-slate-200" />
                            </div>
                            {mauvaisePerf.length > 0
                                ? mauvaisePerf.map((art) => renderCard(art))
                                : <p className="text-xs text-slate-400 mb-4">Aucun article ici.</p>}

                            <div className="flex items-center gap-3 mb-3 mt-6">
                                <span className="text-[11px] font-black text-slate-500 uppercase tracking-wide whitespace-nowrap">
                                    🔄 Avant Republication ({avantRepublication.length})
                                </span>
                                <div className="flex-1 h-px bg-slate-200" />
                            </div>
                            {avantRepublication.length > 0
                                ? avantRepublication.map((art) => renderCard(art))
                                : <p className="text-xs text-slate-400">Aucun article ici.</p>}
                        </>
                    );
                })() : (
                    filteredArticles.map((art) => renderCard(art))
                )}
            </div>
            </>
            )}

            {/* Panier Flottant */}
            

            {showRepublishModal && (
                <RepublishConfirmModal
                    items={modalItems}
                    onRemove={handleRemoveFromModal}
                    onConfirm={handleConfirmRepublishModal}
                    onCancel={() => setShowRepublishModal(false)}
                    isSubmitting={isForcingRepublish}
                />
            )}

            {showPepitesModal && (
                <PepitesBaisseConfirmModal
                    items={pepitesModalItems}
                    onRemove={handleRemoveFromPepitesModal}
                    onConfirm={handleConfirmPepitesModal}
                    onCancel={() => setShowPepitesModal(false)}
                    isSubmitting={isForcingPepites}
                />
            )}

            <ManualBaisseCart
                selectedItems={inventory.filter(a => selectedForManualBaisse.has(a.id))}
                pourcentages={manualBaissePourcentages}
                onChangeItemPourcentage={setManualBaisseItemPourcentage}
                isLaunching={isLaunchingManualBaisse}
                onLaunch={handleLaunchManualBaisse}
            />
        </div>
    );
}