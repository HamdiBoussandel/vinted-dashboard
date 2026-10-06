import React, { useState, useEffect } from 'react';
import { inventoryService } from '../services/api';
import toast, { Toaster } from 'react-hot-toast';
import { Flame, Loader, CheckCircle2, Clock } from 'lucide-react';

const PHASE_LABELS = {
    0: { label: 'Phase 0 — Sous surveillance', color: 'bg-blue-100 text-blue-700 border-blue-200' },
    1: { label: 'Phase 1 — Récupération capital', color: 'bg-amber-100 text-amber-700 border-amber-200' },
    2: { label: 'Phase 2 — Perte mesurée', color: 'bg-orange-100 text-orange-700 border-orange-200' },
    3: { label: 'Phase 3 — Sortie forcée', color: 'bg-red-100 text-red-700 border-red-200' },
};

// Doit rester aligné avec MIN_ARTICLES_PAR_GROUPE_LIQUIDATION côté backend
// (inventory.py) -- purement pour l'affichage ici, le backend retranche de
// toute façon les groupes trop petits de son côté, indépendamment de ce chiffre.
const MIN_ARTICLES_PAR_GROUPE = 3;

export default function Liquidation() {
    const [inventory, setInventory] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [excludedIds, setExcludedIds] = useState(new Set());
    const [isRunning, setIsRunning] = useState(false);

    useEffect(() => {
        fetchData();
    }, []);

    const fetchData = async () => {
        setIsLoading(true);
        try {
            const data = await inventoryService.getArticles();
            setInventory(data);
        } catch (err) {
            toast.error("Impossible de charger l'inventaire.");
        } finally {
            setIsLoading(false);
        }
    };

    // Articles dormants nécessitant une baisse de prix pour leur phase actuelle --
    // exclut les exclusions persistées (saisonnier / perso-ami), consolidées ici
    // depuis l'ancien onglet Liquidation du Dashboard (07/09/2026).
    const articlesDormants = inventory
        .filter(a =>
            a.liquidation_needs_action &&
            !excludedIds.has(a.id) &&
            !a.exclu_liquidation_saisonnier &&
            !a.est_personnel_ou_ami
        )
        .sort((a, b) => b.jours_en_vente - a.jours_en_vente);

    // Articles exclus (saisonnier/perso-ami) mais qui redeviendraient dormants
    // sinon -- besoin d'un moyen de les revoir/réintégrer, sans quoi une
    // exclusion persistée est irréversible en pratique.
    const articlesExclus = inventory
        .filter(a => a.liquidation_needs_action && (a.exclu_liquidation_saisonnier || a.est_personnel_ou_ami))
        .sort((a, b) => b.jours_en_vente - a.jours_en_vente);

    // Regroupement Option 3 (prix fixe, remplace l'Option 2 par pourcentage le
    // 03/10/2026) -- DOIT rester identique à calculer_prix_fixe_liquidation()
    // côté backend (routes/inventory.py) : arrondit la cible vers le PROCHAIN
    // euro entier (même si déjà rond), puis retranche 10 centimes. Regroupe des
    // cibles différentes mais proches sur un même prix "rond", pour former des
    // groupes de taille suffisante avec le mode "prix fixe" natif de Clemz --
    // les pourcentages nécessaires étaient trop dispersés pour ça (la plupart
    // des groupes Phase 3 restaient < 5 articles).
    const calculerPrixFixeLiquidation = (prixCible) => Math.floor(prixCible) + 0.90;

    const groupesParPrixFixe = {};
    articlesDormants.forEach(a => {
        const prixFixe = calculerPrixFixeLiquidation(a.liquidation_prix_cible);
        if (!groupesParPrixFixe[prixFixe]) groupesParPrixFixe[prixFixe] = [];
        groupesParPrixFixe[prixFixe].push(a);
    });

    // Distinction groupes exécutables (>= seuil) vs reportés (< seuil) -- reflète
    // exactement ce que le backend fera : les groupes reportés ne partiront pas
    // dans ce lancement, ils seront réévalués naturellement au prochain clic.
    const idsExecutables = new Set();
    let nbGroupesExecutables = 0;
    let nbGroupesReportes = 0;
    let nbArticlesReportes = 0;
    Object.values(groupesParPrixFixe).forEach(items => {
        if (items.length >= MIN_ARTICLES_PAR_GROUPE) {
            nbGroupesExecutables += 1;
            items.forEach(a => idsExecutables.add(a.id));
        } else {
            nbGroupesReportes += 1;
            nbArticlesReportes += items.length;
        }
    });
    const nbArticlesExecutables = idsExecutables.size;

    const toggleExclude = (id) => {
        setExcludedIds(prev => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            return next;
        });
    };

    // Exclusions PERSISTÉES (contrairement à toggleExclude ci-dessus, qui ne
    // vit qu'en mémoire pour ce lancement) -- reprises telles quelles de
    // l'ancien onglet Liquidation du Dashboard.
    const handleToggleExclusionSaisonniere = async (id, exclu) => {
        try {
            const response = await fetch(`http://localhost:8000/api/exclusion-saisonniere/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ exclu }),
            });
            if (response.ok) {
                setInventory(prev => prev.map(art => art.id === id ? { ...art, exclu_liquidation_saisonnier: exclu } : art));
                toast.success(exclu ? "Article gardé pour la saison." : "Article réintégré en Liquidation.");
            } else {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || "Erreur lors du basculement de l'exclusion saisonnière");
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`);
        }
    };

    const handleToggleMarquerPersonnel = async (id, estPersonnel) => {
        try {
            const response = await fetch(`http://localhost:8000/api/marquer-personnel/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ est_personnel: estPersonnel }),
            });
            if (response.ok) {
                setInventory(prev => prev.map(art => art.id === id ? { ...art, est_personnel_ou_ami: estPersonnel } : art));
                toast.success(estPersonnel ? "Article marqué perso/ami." : "Article réintégré en Liquidation.");
            } else {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || "Erreur lors du marquage perso/ami");
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`);
        }
    };

    const handleLancerLiquidation = async () => {
        if (articlesDormants.length === 0) return;
        setIsRunning(true);
        toast.loading(`Lancement de la liquidation (${nbArticlesExecutables} article(s), ${nbGroupesExecutables} groupe(s))...`, { id: 'liq-toast' });
        try {
            const response = await fetch('http://localhost:8000/api/liquidation/run-now', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    produits: articlesDormants.map(a => ({
                        id: a.id,
                        nom: a.nom,
                        dressing: a.dressing,
                        pourcentage_necessaire: a.liquidation_pourcentage_necessaire,
                        prix_cible: a.liquidation_prix_cible,
                    })),
                }),
            });
            if (response.ok) {
                const data = await response.json();
                const suffixeReporte = data.nb_articles_reportes > 0
                    ? ` (${data.nb_articles_reportes} article(s) en attente d'un groupe plus grand)`
                    : '';
                toast.success(`Liquidation lancée en arrière-plan (${data.nb_groupes} groupe(s) de baisse)${suffixeReporte}.`, { id: 'liq-toast' });
            } else {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || 'Erreur lors du lancement');
            }
        } catch (err) {
            toast.error(`Échec : ${err.message}`, { id: 'liq-toast' });
        } finally {
            setIsRunning(false);
        }
    };

    if (isLoading) {
        return <div className="p-10 text-slate-400 font-bold">Chargement...</div>;
    }

    return (
        <div className="p-6 max-w-6xl mx-auto">
            <Toaster position="bottom-right" />
            <header className="mb-8">
                <h1 className="text-2xl font-black tracking-tight flex items-center gap-2 mb-2">
                    <Flame className="text-orange-500" size={26} />
                    Liquidation — Stock dormant
                </h1>
                <p className="text-sm text-slate-500">
                    Articles en vente depuis 45 jours ou plus, avec un prix d'achat renseigné --
                    Phase 0 (45-89j) en simple surveillance, Phases 1 à 3 (90j+) en vraie urgence
                    de décision. Le prix cible et le pourcentage nécessaire sont recalculés à
                    chaque chargement, à partir du nombre de jours en vente — rien n'est stocké
                    en état.
                </p>
            </header>

            {/* Résumé + bouton de lancement */}
            <div className="flex items-center justify-between gap-4 mb-6 p-4 bg-slate-50 border border-slate-100 rounded-2xl">
                <div className="flex items-center gap-6">
                    <div>
                        <p className="text-[10px] font-black text-slate-400 uppercase tracking-wide">Articles exécutables</p>
                        <p className="text-xl font-black text-slate-800">{nbArticlesExecutables}</p>
                    </div>
                    <div>
                        <p className="text-[10px] font-black text-slate-400 uppercase tracking-wide">Groupes exécutables</p>
                        <p className="text-xl font-black text-slate-800">{nbGroupesExecutables}</p>
                    </div>
                    {nbArticlesReportes > 0 && (
                        <div>
                            <p className="text-[10px] font-black text-amber-500 uppercase tracking-wide flex items-center gap-1">
                                <Clock size={11} /> En attente
                            </p>
                            <p className="text-xl font-black text-amber-600">
                                {nbArticlesReportes} <span className="text-xs font-bold text-amber-400">({nbGroupesReportes} groupe{nbGroupesReportes > 1 ? 's' : ''} &lt; {MIN_ARTICLES_PAR_GROUPE})</span>
                            </p>
                        </div>
                    )}
                </div>
                <button
                    onClick={handleLancerLiquidation}
                    disabled={isRunning || nbArticlesExecutables === 0}
                    className="flex items-center gap-2 px-6 py-2.5 bg-orange-500 text-white rounded-lg font-black uppercase text-xs hover:scale-105 transition-all shadow-lg shadow-orange-100 disabled:opacity-40"
                >
                    {isRunning ? (
                        <>
                            <Loader size={16} className="animate-spin" />
                            Lancement...
                        </>
                    ) : (
                        <>
                            <Flame size={16} />
                            Lancer la liquidation ({nbArticlesExecutables})
                        </>
                    )}
                </button>
            </div>

            {/* Liste des articles, groupée par phase */}
            <div className="space-y-6">
                {[0, 1, 2, 3].map(phase => {
                    const items = articlesDormants.filter(a => a.liquidation_phase === phase);
                    if (items.length === 0) return null;
                    const phaseInfo = PHASE_LABELS[phase];
                    return (
                        <div key={phase}>
                            <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-black uppercase mb-3 ${phaseInfo.color}`}>
                                {phaseInfo.label} ({items.length})
                            </div>
                            <div className="space-y-2">
                                {items.map(a => {
                                    const enAttenteGroupe = !idsExecutables.has(a.id);
                                    return (
                                        <div key={a.id} className={`flex items-center gap-4 p-4 bg-white border rounded-xl ${enAttenteGroupe ? 'border-amber-200 bg-amber-50/40' : 'border-slate-100'}`}>
                                            <button onClick={() => toggleExclude(a.id)} title="Exclure de ce lancement">
                                                <CheckCircle2 size={20} className="text-emerald-500" />
                                            </button>
                                            <img src={a.photo_url} alt={a.nom} className="w-14 h-14 object-cover rounded-lg bg-slate-100" />
                                            <div className="flex-1 min-w-0">
                                                <p className="font-bold text-sm text-slate-800 truncate">{a.nom}</p>
                                                <p className="text-xs text-slate-400">
                                                    {a.dressing} · {a.jours_en_vente}j en vente · Achat {a.prix_achat?.toFixed(2)}€
                                                </p>
                                            </div>
                                            {enAttenteGroupe && (
                                                <span className="flex items-center gap-1 text-[10px] font-black text-amber-600 bg-amber-100 px-2 py-1 rounded whitespace-nowrap">
                                                    <Clock size={11} /> Groupe &lt; {MIN_ARTICLES_PAR_GROUPE}
                                                </span>
                                            )}
                                            <div className="text-right">
                                                <p className="text-xs text-slate-400">Actuel</p>
                                                <p className="font-black text-slate-700">{a.prix_vente?.toFixed(2)}€</p>
                                            </div>
                                            <div className="text-right">
                                                <p className="text-xs text-slate-400">Cible</p>
                                                <p className="font-black text-orange-600">{a.liquidation_prix_cible?.toFixed(2)}€</p>
                                            </div>
                                            <div className="text-right" title="Prix réellement appliqué par le groupe (mode 'prix fixe' Clemz) -- toujours >= à la cible">
                                                <p className="text-xs text-slate-400">Prix fixe</p>
                                                <p className="font-black text-emerald-600">{calculerPrixFixeLiquidation(a.liquidation_prix_cible).toFixed(2)}€</p>
                                            </div>
                                            <div className="text-right w-20">
                                                <p className="text-xs text-slate-400">Baisse</p>
                                                <p className="font-black text-red-500">-{a.liquidation_pourcentage_necessaire}%</p>
                                            </div>
                                            <div className="flex flex-col gap-1 items-end">
                                                <button
                                                    onClick={() => handleToggleExclusionSaisonniere(a.id, true)}
                                                    className="bg-sky-50 text-sky-700 border border-sky-200 px-2 py-1 rounded-lg text-[9px] font-bold uppercase tracking-tighter hover:bg-sky-100 transition-all whitespace-nowrap"
                                                    title="Retirer de la Liquidation -- vêtement hors saison, redeviendra vendable normalement"
                                                >
                                                    ❄️ Garder pour la saison
                                                </button>
                                                <button
                                                    onClick={() => handleToggleMarquerPersonnel(a.id, true)}
                                                    className="bg-violet-50 text-violet-700 border border-violet-200 px-2 py-1 rounded-lg text-[9px] font-bold uppercase tracking-tighter hover:bg-violet-100 transition-all whitespace-nowrap"
                                                    title="Article personnel ou vendu pour un ami -- pas un vrai achat, retiré de la Liquidation"
                                                >
                                                    🏷️ Perso/ami
                                                </button>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    );
                })}

                {articlesDormants.length === 0 && (
                    <p className="text-center text-slate-400 py-12">Aucun article en liquidation à traiter pour le moment.</p>
                )}
            </div>

            {/* Articles exclus (saisonnier/perso-ami) -- sans cette section, une
                exclusion persistée serait irréversible en pratique. */}
            {articlesExclus.length > 0 && (
                <div className="mt-10">
                    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-black uppercase mb-3 bg-slate-100 text-slate-500 border-slate-200">
                        Exclus ({articlesExclus.length})
                    </div>
                    <div className="space-y-2">
                        {articlesExclus.map(a => (
                            <div key={a.id} className="flex items-center gap-4 p-4 bg-white border border-slate-100 rounded-xl opacity-70">
                                <img src={a.photo_url} alt={a.nom} className="w-14 h-14 object-cover rounded-lg bg-slate-100" />
                                <div className="flex-1 min-w-0">
                                    <p className="font-bold text-sm text-slate-800 truncate">{a.nom}</p>
                                    <p className="text-xs text-slate-400">
                                        {a.dressing} · {a.jours_en_vente}j en vente ·{' '}
                                        {a.exclu_liquidation_saisonnier ? '❄️ Gardé pour la saison' : '🏷️ Perso/ami'}
                                    </p>
                                </div>
                                <button
                                    onClick={() => a.exclu_liquidation_saisonnier
                                        ? handleToggleExclusionSaisonniere(a.id, false)
                                        : handleToggleMarquerPersonnel(a.id, false)}
                                    className="bg-slate-100 text-slate-500 border border-slate-200 px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-tighter hover:bg-slate-200 transition-all whitespace-nowrap"
                                    title="Réintégrer cet article dans la liste Liquidation"
                                >
                                    ↩️ Réintégrer
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}