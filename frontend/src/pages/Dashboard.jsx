import React, { useState, useEffect } from 'react'; // Retrait du 'use' inutile
import { inventoryService, salesService } from '../services/api';
import toast, { Toaster } from 'react-hot-toast';
import { motion } from 'framer-motion';

// Tes composants isolés
import StatsOverview from '../components/StatsOverview';
import NavigationFilters from '../components/NavigationFilters';
import ProductCard from '../components/ProductCard';
import SystemControlBar from '../components/SystemControlBar';
import AutomationCart from '../components/AutomationCart';

// Variable persistante qui survit au changement d'onglet mais pas au F5
let isInitialLoad = true;

let cachedInventory = null;
let cachedStats = null;
let cachedSales = null;

const extrairePrixSuggere = (label) => {
    const match = label?.match(/\((\d+(\.\d+)?)/); 
    return match ? parseFloat(match[1]) : 0;
};

export default function Dashboard() {
    const [inventory, setInventory] = useState(cachedInventory || []);
    const [republishFilter, setRepublishFilter] = useState('Tous');
    const [isScraping, setIsScraping] = useState(false);
    const [isLoading, setIsLoading] = useState(!cachedInventory);
    const [sales, setSales] = useState(cachedSales || { current: null, last: null });
    const [stats, setStats] = useState(cachedStats || {
        ca_global_format: "0,00 €",
        investissement_total_format: "0,00 €",
        benefice_format: "0,00 €"
    });

    const [activeSegment, setActiveSegment] = useState('Tous');
    const [processingIds, setProcessingIds] = useState(new Set());
    const [searchTerm, setSearchTerm] = useState('');
    const [isAutomating, setIsAutomating] = useState(false);
    const [selectedForAutomation, setSelectedForAutomation] = useState([]);

    const [sysState, setSysState] = useState({
        last_cron_14h: "---",
        last_cron_22h: "---",
        last_manual_scrap: "---",
        last_clemz_sync: "---",
        is_clemz_running: false,
        status_TEST: "En attente"
    });

    useEffect(() => {
        if (selectedForAutomation.length > 0) {
            console.log("🛒 PANIER AUTOMATION MIS À JOUR :");
            console.log(`Nombre d'articles : ${selectedForAutomation.length}`);
            // Affiche la liste des noms pour vérification rapide
            console.log("Articles :", selectedForAutomation.map(a => a.nom).join(' | '));
        } else {
            console.log("🗑️ Panier vidé.");
        }
    }, [selectedForAutomation]);

    // Initialisation
    useEffect(() => {
        // Si on a déjà du cache, on considère que ce n'est plus l'initialisation load
        if (cachedInventory) {
            isInitialLoad = false;
            setIsLoading(false);
        }

        if (isInitialLoad) {
            fetchData();
            isInitialLoad = false;
        }
    }, [isScraping]);

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
            const [inventoryData, salesData, statsData] = await Promise.all([
                inventoryService.getArticles(),
                salesService.getSales(),
                inventoryService.getGlobalStats() // Utilise maintenant le service
            ]);

            cachedInventory = inventoryData;
            cachedStats = statsData;
            cachedSales = salesData;

            setInventory(inventoryData);
            setSales(salesData);
            setStats(statsData); // Plus besoin de .data car fetch renvoie directement le JSON
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
                // Surveillance active toutes les 2s
                const monitorInterval = setInterval(async () => {
                    const currentState = await fetchSystemState();
                    if (currentState && (currentState.status_TEST === "Synchronisation Réussie" || currentState.status_TEST === "Erreur Système")) {
                        clearInterval(monitorInterval);
                        setIsScraping(false);
                        if (currentState.status_TEST === "Synchronisation Réussie") {
                            toast.success("Synchronisation terminée !", { id: 'scrap-toast' });
                            new Audio('https://audio-previews.elements.envatousercontent.com/files/660379476/preview.mp3').play().catch(() => { });
                            await fetchData();
                        } else {
                            toast.error("Le processus a échoué.", { id: 'scrap-toast' });
                        }
                    }
                }, 2500);
            }
        } catch (err) {
            setIsScraping(false);
            toast.error("Impossible de contacter le serveur.", { id: 'scrap-toast' });
        }
    };

    const handleLaunchPilotage = async () => {
        if (selectedForAutomation.length === 0) return;

        // 📊 AFFICHAGE DU TABLEAU DANS LA CONSOLE DU NAVIGATEUR
        console.log("🚀 ENVOI DES DONNÉES À L'AUTOMATION :");
        console.table(selectedForAutomation.map(p => ({
            ID: p.id,
            Nom: p.nom,
            Prix: p.prix_vente,
            Dressing: p.dressing,
            Action: p.action_label
        })));

        setIsAutomating(true);
        try {
            // Remplacer axios par un fetch cohérent avec le reste
            const response = await fetch('http://localhost:8000/api/automation/baisse-prix', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    produits: selectedForAutomation.map(p => ({
                        nom: p.nom,
                        dressing: p.dressing
                    }))
                })
            });

            if (response.ok) {
                toast.success(`Pilotage lancé pour ${selectedForAutomation.length} articles`);
                setSelectedForAutomation([]);
                // Le son ou une notification discrète est préférable à l'alert()
            }
        } catch (error) {
            toast.error("Erreur lors du lancement du pilotage");
            console.error("Erreur pilotage:", error);
        } finally {
            setIsAutomating(false);
        }
    };

    // Fonctions de traitement Notion
    const handleActionTraitement = async (id) => {
        try {
            const response = await fetch(`http://localhost:8000/api/treat/${id}`, { method: 'PATCH' });
            if (response.ok) {
                setInventory(prev => prev.map(art => art.id === id ? { ...art, is_done: true } : art));
            }
        } catch (err) { console.error(err); }
    };

    const handleRepublish = async (nom, id) => {
        setProcessingIds(prev => new Set(prev).add(id));
        try {
            const response = await fetch('http://localhost:8000/api/republish-by-name', {
                method: 'PATCH', // Assure-toi que c'est POST comme vu précédemment
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ nom })
            });

            if (response.ok) {
                // ✅ SUCCÈS : Notification positive
                toast.success(`${nom} : Date mise à jour dans Notion !`, {
                    style: { background: '#10B981', color: '#fff', fontWeight: 'bold' }
                });

                // ✅ ON RETIRE l'article de la liste (il disparaît du Dashboard)
                setInventory(prev => prev.filter(art => art.id !== id));
            } else {
                throw new Error(data.detail || 'Erreur lors de la mise à jour');
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

    const handleClearDatabase = async () => {
        if (!window.confirm("Vider TOUTE la base Notion ?")) return;
        try {
            const response = await fetch('http://localhost:8000/api/clear-database', { method: 'DELETE' });
            if (response.ok) fetchData();
        } catch (err) { console.error(err); }
    };

    // Filtrage
    // Filtrage intelligent et ultra-sécurisé
    const filteredArticles = inventory.filter(art => {

        if (art.is_vendu) return false;
        // 1. Recherche par texte (prioritaire)
        if (searchTerm.trim() !== '') return art.nom.toLowerCase().includes(searchTerm.toLowerCase());

        // 2. Onglet "Tous" : On affiche tout sans exception
        if (activeSegment === 'Tous') return true;
    
        if (activeSegment === 'Liquidation') {
            return art.action_label?.includes("LIQUIDATION") || 
                art.action_label?.includes("SORTIE") || 
                art.is_very_old;
        }

        // 3. Onglet "Republications" : On affiche les urgences, MÊME SI l'article a déjà eu une baisse de prix
        if (activeSegment === 'Republications' || activeSegment === 'Republish') {

            const isBaseRepublish = art.is_invisible || art.needs_time_republish || art.is_critical;
            if (!isBaseRepublish) return false;

            // Application du sous-filtre par tag
            if (republishFilter === 'Invisible') return art.is_invisible;
            if (republishFilter === 'Baisse') return art.action_label?.includes("BAISSE");
            if (republishFilter === 'Standard') return art.action_label?.includes("Même prix");
            if (republishFilter === 'Urgent') return art.is_critical && (art.is_very_old || art.action_label?.includes("LIQUIDATION"));

            return true; // 'Tous'
        }

        // 4. Pour le reste : On cache formellement si c'est déjà traité
        if (art.is_done) return false;
        // 5. Distribution stricte dans les autres onglets
        if (activeSegment === 'Pépites') return art.is_stuck;
        if (activeSegment === 'Mauvaise Performance') return art.is_low_perf;
        if (activeSegment === 'Statut Quo') return art.is_statut_quo;

        // SÉCURITÉ MAXIMALE : On cache tout ce qui ne rentre pas dans les cases au-dessus
        return false;

    }).sort((a, b) => {
        // --- NOUVEAU : LOGIQUE DE TRI ---
        if (activeSegment === 'Republications' || activeSegment === 'Republish') {
            // Trie par "jours_en_ligne" du plus grand au plus petit
            // Les articles en ligne depuis le plus longtemps (ex: 22j) seront tout en haut !
            return b.jours_en_ligne - a.jours_en_ligne;
        }

        // Tri pour la Liquidation : Du plus petit score d'attirance au plus grand
        if (activeSegment === 'Liquidation') {
            // Tri par prix suggéré (du plus petit au plus grand)
            return extrairePrixSuggere(a.action_label) - extrairePrixSuggere(b.action_label);
        }

        // Pour les autres onglets, on conserve l'ordre alphabétique par défaut de Notion
        return a.nom.localeCompare(b.nom);
    });

    // Dans Dashboard.jsx, ajoute cette fonction :
    const handleBulkTraitement = async (articlesToTreat) => {
        const ids = articlesToTreat.map(a => a.id);
        if (ids.length === 0) return;

        await toast.promise(
            inventoryService.markMultipleAsTreated(ids),
            {
                loading: 'Traitement des articles en cours...',
                success: () => {
                    // Mise à jour locale de l'état
                    setInventory(prev => prev.map(art =>
                        ids.includes(art.id) ? { ...art, is_done: true } : art
                    ));
                    return `${ids.length} articles marqués comme traités ! ✅`;
                },
                error: (err) => {
                    console.error("Erreur lors du traitement groupé:", err);
                    return "Échec du traitement sur Notion. ❌";
                },
            },
            {
                style: { minWidth: '250px', fontWeight: 'bold' },
                success: { duration: 4000 },
            }
        );
    };

    const handleBulkRepublish = async (articlesToRepublish) => {
        const ids = articlesToRepublish.map(a => a.id);
        if (ids.length === 0) return;

        try {
            await inventoryService.markMultipleAsRepublished(ids);

            // On retire les articles de la vue (car ils n'ont plus à être republiés aujourd'hui)
            setInventory(prev => prev.filter(art => !ids.includes(art.id)));
            toast.success(`${ids.length} articles marqués comme republiés !`);
        } catch (err) {
            console.error("Erreur lors de la republication groupée:", err);
            toast.error("Erreur lors de la mise à jour");
        }
    };

    const handleSelectAllVisible = () => {
        // 1. On récupère les IDs de tous les articles actuellement affichés dans l'onglet
        const visibleIds = filteredArticles.map(art => art.id);

        // 2. On vérifie si TOUS ces articles sont déjà présents dans ta sélection
        const areAllSelected = visibleIds.every(id =>
            selectedForAutomation.some(item => item.id === id)
        );

        if (areAllSelected) {
            // 🔄 DÉCOCHER TOUT : On retire de la sélection tous les articles visibles
            setSelectedForAutomation(prev =>
                prev.filter(item => !visibleIds.includes(item.id))
            );
        } else {
            // ✅ COCHER TOUT : On ajoute ceux qui ne sont pas encore dans la liste
            setSelectedForAutomation(prev => {
                const toAdd = filteredArticles.filter(art =>
                    !prev.some(p => p.id === art.id)
                );
                return [...prev, ...toAdd];
            });
        }
    };

    const toggleArticleSelection = (article) => {
        setSelectedForAutomation(prev => {
            const isAlreadySelected = prev.some(a => a.id === article.id);

            if (isAlreadySelected) {
                const newList = prev.filter(a => a.id !== article.id);
                console.log(`➖ Retrait : ${article.nom} (Reste : ${newList.length})`);
                return newList;
            } else {
                const newList = [...prev, article];
                console.log(`➕ Ajout : ${article.nom} (Total : ${newList.length})`);
                console.table(newList.map(item => ({ Nom: item.nom, Dressing: item.dressing })));
                return newList;
            }
        });
    };

    const segments = [
        { id: 'Tous', label: `📂 Tous (${inventory.filter(a => !a.is_vendu).length})`, count: inventory.filter(a => !a.is_vendu).length },
        { id: 'Mauvaise Performance', label: `📉 Mauvaise Perf (${inventory.filter(a => a.is_low_perf && !a.is_done && !a.is_vendu).length})`, count: inventory.filter(a => a.is_low_perf && !a.is_done && !a.is_vendu).length },
        {
            id: 'Liquidation',
            label: `💀 Liquidation (${inventory.filter(a => (a.action_label?.includes("LIQUIDATION") || a.is_very_old) && !a.is_vendu).length})`,
            count: inventory.filter(a => (a.action_label?.includes("LIQUIDATION") || a.is_very_old) && !a.is_vendu).length
        },
        {
            id: 'Republications',
            label: `♻️ Republish (${inventory.filter(a => (a.is_invisible || a.needs_time_republish || a.is_critical) && !a.is_vendu).length})`,
            count: inventory.filter(a => (a.is_invisible || a.needs_time_republish || a.is_critical) && !a.is_vendu).length
        },
        { id: 'Pépites', label: `💎 Pépites (${inventory.filter(a => a.is_stuck && !a.is_done && !a.is_vendu).length})`, count: inventory.filter(a => a.is_stuck && !a.is_done && !a.is_vendu).length },
        { id: 'Statut Quo', label: `❓ Statut Quo (${inventory.filter(a => a.is_statut_quo && !a.is_done && !a.is_vendu).length})`, count: inventory.filter(a => a.is_statut_quo && !a.is_done && !a.is_vendu).length }
    ];

    if (isLoading) return (
        <div className="flex flex-col items-center justify-center min-h-screen w-full bg-white">
            <motion.div
                initial={{ opacity: 0.3 }}
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{
                    duration: 2,
                    repeat: Infinity,
                    ease: "easeInOut"
                }}
                className="flex flex-col items-center gap-2"
            >
                <span className="text-slate-400 font-black uppercase tracking-[0.4em] text-[12px]">
                    Analyse en cours...
                </span>
                {/* Ligne décorative fine optionnelle pour souligner l'aspect "scan" */}
                <div className="w-12 h-[1px] bg-cyan-400/50" />
            </motion.div>
        </div>
    );

    return (
        <div className="p-6 max-w-8xl mx-auto">
            <Toaster position="bottom-right" /> {/* <--- AJOUT ICI */}
            <header className="mb-10">
                <h1 className="text-3xl font-semibold tracking-tighter mb-6">Pilotage Stock</h1>

                {/* 1. Barre Système unifiée (Bouton Scrap + Recherche + Vider Base) */}
                <SystemControlBar
                    sysState={sysState}
                    isScraping={isScraping}
                    handleRunScraper={handleRunScraper}
                    searchTerm={searchTerm}
                    setSearchTerm={setSearchTerm}
                    onClearDatabase={handleClearDatabase}
                />

                {/* 2. Stats (isolées) */}
                <StatsOverview stats={stats} sales={sales} isLoading={isLoading} />

                {/* 3. Navigation (segments seuls) */}
                <NavigationFilters
                    segments={segments}
                    activeSegment={activeSegment}
                    setActiveSegment={setActiveSegment}
                    searchTerm={searchTerm}
                    setSearchTerm={setSearchTerm}
                    inventory={inventory}
                    filteredArticles={filteredArticles}
                    onStageSection={(items) => setSelectedForAutomation(prev => [...prev, ...items.filter(i => !prev.some(p => p.id === i.id))])}
                    onBulkTreat={handleBulkTraitement}
                    onBulkRepublish={handleBulkRepublish}
                />
            </header>

            {/* 🟢 AJOUT : Barre de sous-filtres par tags cliquables (Uniquement en mode Republications) */}
            {activeSegment === 'Republications' && (
                <div className="flex items-center gap-2 mb-6 bg-slate-50 p-3 rounded-2xl border border-slate-100 shadow-sm animate-in fade-in slide-in-from-top-2 duration-500">
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest ml-2 mr-4">Filtrer par urgence :</span>
                    {[
                        { id: 'Tous', label: 'Tout', color: 'bg-slate-200 text-slate-700' },
                        { id: 'Invisible', label: '👻 Invisible', color: 'bg-purple-100 text-purple-700 border-purple-200' },
                        { id: 'Baisse', label: '📉 Republication + Baisse', color: 'bg-orange-100 text-orange-700 border-orange-200' },
                        { id: 'Standard', label: '♻️ Republication Simple', color: 'bg-blue-100 text-blue-700 border-blue-200' },
                        { id: 'Urgent', label: '🚨 Liquidation / Sortie', color: 'bg-red-100 text-red-700 border-red-200' }
                    ].map(tag => (
                        <button
                            key={tag.id}
                            onClick={() => setRepublishFilter(tag.id)}
                            className={`px-4 py-2 rounded-xl text-[11px] font-black border transition-all active:scale-95 ${republishFilter === tag.id
                                    ? `${tag.color} ring-2 ring-offset-2 ring-slate-200 shadow-sm`
                                    : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                                }`}
                        >
                            {tag.label}
                        </button>
                    ))}
                </div>
            )}

            {/* Liste des produits */}
            <div className="space-y-1">
                <div className="flex justify-start mb-3">
                    <button
                        onClick={handleSelectAllVisible}
                        className="flex items-center gap-2 px-4 py-2 bg-indigo-50 text-indigo-600 rounded-lg font-bold text-xs hover:bg-indigo-600 hover:text-white transition-all border border-indigo-100 shadow-sm active:scale-95"
                    >
                        {/* Changement d'icône si tout est déjà sélectionné */}
                        {filteredArticles.length > 0 && filteredArticles.every(art => selectedForAutomation.some(s => s.id === art.id)) ? (
                            <>
                                <span className="text-sm">✕</span>
                                DÉCOCHER LA SECTION ({filteredArticles.length})
                            </>
                        ) : (
                            <>
                                <span className="text-lg">+</span>
                                PRÉPARER LA SECTION ({filteredArticles.length})
                            </>
                        )}
                    </button>
                </div>
                {filteredArticles.map((art) => (
                    <ProductCard
                        key={art.id}
                        art={art}
                        activeSegment={activeSegment}
                        processingIds={processingIds}
                        onTreat={handleActionTraitement}
                        onRepublish={handleRepublish}
                        isSelected={selectedForAutomation.some(a => a.id === art.id)}
                        onToggleSelection={() => toggleArticleSelection(art)}
                    />
                ))}
            </div>

            {/* Panier Flottant */}
            <AutomationCart
                selectedItems={selectedForAutomation}
                isAutomating={isAutomating}
                onLaunch={handleLaunchPilotage}
                onClear={() => setSelectedForAutomation([])}
            />
        </div>
    );
}