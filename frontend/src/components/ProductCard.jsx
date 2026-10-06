// components/ProductCard.jsx
import React, { useState } from 'react';

const API_URL = 'http://localhost:8000/api';

// Seuil (en points de score) en dessous duquel on considère l'évolution comme
// stable plutôt que comme une vraie hausse/baisse -- évite qu'un bruit de 0.5
// point entre deux snapshots déclenche une flèche trompeuse.
const SEUIL_TENDANCE = 2;

// Doit rester synchronisé avec le seuil "v_reel >= 5" de la formule de score
// (supabase_service.py, get_processed_inventory) -- en dessous, le backend
// force score=0.0 faute d'échantillon fiable, ce qui afficherait à tort une
// jauge "0% d'attirance" au lieu de "pas encore assez de données".
const SEUIL_MIN_VUES_SCORE_FIABLE = 5;

function calculerTendance(trend) {
    if (!trend || trend.length < 3) {
        return { direction: 'insuffisant', delta: 0 };
    }
    const scores = trend.map((p) => p.score);
    const premier = scores[0];
    const dernier = scores[scores.length - 1];
    const delta = dernier - premier;

    if (delta > SEUIL_TENDANCE) return { direction: 'hausse', delta };
    if (delta < -SEUIL_TENDANCE) return { direction: 'baisse', delta };
    return { direction: 'stable', delta };
}

function TendanceIndicateur({ trend }) {
    const { direction, delta } = calculerTendance(trend);

    if (direction === 'insuffisant') {
        return <span className="text-[9px] text-slate-300" title="Pas assez d'historique">➡️</span>;
    }

    const config = {
        hausse: { icone: '↗️', couleur: 'text-emerald-500' },
        baisse: { icone: '↘️', couleur: 'text-red-500' },
        stable: { icone: '➡️', couleur: 'text-slate-400' },
    }[direction];

    return (
        <div className={`flex items-center gap-1 ${config.couleur}`} title={`Score ${delta >= 0 ? '+' : ''}${delta.toFixed(1)} sur la période`}>
            <span className="text-[10px]">{config.icone}</span>
            <span className="text-[9px] font-bold">{delta >= 0 ? '+' : ''}{delta.toFixed(1)}</span>
        </div>
    );
}

export default function ProductCard({
    art,
    activeSegment,
    processingIds,
    onTreat,
    onRepublish,
    // Exclusion baisse de prix
    isExcludedBaisse,
    onToggleExcludeBaisse,
    // Exclusion republication
    isExcludedRepublish,
    onToggleExcludeRepublish,
    // Sélection manuelle pour baisse de prix libre (indépendante de is_low_perf) --
    // visible sur TOUTE carte, dans tous les onglets, contrairement aux exclusions
    // ci-dessus qui ne concernent que les articles déjà éligibles au segment actif.
    isSelectedManualBaisse,
    onToggleManualBaisse,
    // Report au lendemain (Republications) -- expire automatiquement, pas de
    // réintégration manuelle nécessaire contrairement aux deux ci-dessus.
    onReporterRepublication,
    trend,
}) {
    const getTemperatureStyle = (score) => {
        if (score >= 12) return { dot: "bg-red-600", text: "text-red-600", label: "🔥 BRÛLANT" };
        if (score >= 8) return { dot: "bg-orange-500", text: "text-orange-600", label: "☀️ CHAUD" };
        if (score >= 5) return { dot: "bg-amber-400", text: "text-amber-600", label: "⛅ TIÈDE" };
        return { dot: "bg-slate-300", text: "text-slate-400", label: "❄️ FROID" };
    };

    const getDressingStyles = (dressingName) => {
        if (dressingName === "Dressing 1") return "bg-blue-50 text-blue-600 border-blue-100";
        if (dressingName === "Dressing 2") return "bg-purple-50 text-purple-600 border-purple-100";
        return "bg-slate-50 text-slate-400 border-slate-100";
    };

    const temp = getTemperatureStyle(art.score);

    const isRepublishEligible = art.is_invisible || art.needs_time_republish || art.is_critical;
    // baisse_prix_taux (10/15/20/None, par tranche de prix depuis le 19/09/2026)
    // est calculé côté backend (get_processed_inventory)
    // et vaut None précisément dans tous les cas où aucune baisse ne doit être
    // proposée -- plancher atteint (marge_dispo <= 3€), mais aussi audit, pépite,
    // très ancien, liquidation, statut quo. On s'aligne dessus plutôt que de
    // recalculer une condition plancher approximative côté frontend -- garantit
    // aussi la cohérence avec le bouton "BAISSE -X%" plus bas, qui utilise le
    // même champ.
    const isBaisseEligible = art.is_low_perf && art.baisse_prix_taux != null;

    // Selon le segment actif, la checkbox alimente l'un ou l'autre panier
    const isRepublishSegment = activeSegment === 'Republications' || activeSegment === 'Republish';
    const isExcluded = isRepublishSegment ? isExcludedRepublish : isExcludedBaisse;
    const onToggleExclude = isRepublishSegment ? onToggleExcludeRepublish : onToggleExcludeBaisse;
    const isEligible = isRepublishSegment ? isRepublishEligible : isBaisseEligible;

    // Couleur du ring selon le panier actif
    const ringColor = isExcluded
        ? 'border-slate-300 ring-2 ring-slate-300 opacity-50'
        : 'border-slate-200';

    return (
        <div className={`group relative grid grid-cols-[40px_100px_1fr_1fr_420px] items-center p-4 rounded-2xl transition-all duration-500 bg-white border shadow-soft hover:shadow-soft-lg mb-2 ${ringColor}`}>
            {/* BOUTON EXCLURE — visible uniquement si l'article est éligible à l'action du segment */}
            <div className="flex flex-col items-center gap-2">
                {isEligible && (
                    <button
                        onClick={onToggleExclude}
                        title={isExcluded ? "Réinclure dans le traitement" : "Exclure du traitement"}
                        className={`w-7 h-7 flex items-center justify-center rounded-lg border text-xs font-black transition-all ${isExcluded
                            ? 'bg-slate-100 text-slate-400 border-slate-200'
                            : 'bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-red-50 hover:text-red-600 hover:border-red-200'
                            }`}
                    >
                        {isExcluded ? '↺' : '✕'}
                    </button>
                )}
                {isRepublishSegment && isEligible && (
                    <button
                        onClick={() => onReporterRepublication(art.id)}
                        title="Reporter la republication à demain -- réapparaîtra automatiquement"
                        className="w-7 h-7 flex items-center justify-center rounded-lg border text-xs bg-amber-50 text-amber-600 border-amber-200 hover:bg-amber-100 transition-all"
                    >
                        📅
                    </button>
                )}
                {/* Ajout au panier de baisse de prix manuelle -- masqué dans le
                    segment Republications, où ça n'a pas de sens d'ajouter un
                    article "à republier" à une baisse de prix (cf. échange du
                    11/09/2026). */}
                {!isRepublishSegment && (
                    <button
                        onClick={() => onToggleManualBaisse(art.id)}
                        title={isSelectedManualBaisse ? "Retirer de la baisse de prix manuelle" : "Ajouter à la baisse de prix manuelle"}
                        className={`w-7 h-7 flex items-center justify-center rounded-lg border text-xs font-black transition-all ${isSelectedManualBaisse
                            ? 'bg-rose-500 text-white border-rose-500'
                            : 'bg-white text-rose-400 border-rose-200 hover:bg-rose-50'
                            }`}
                    >
                        {isSelectedManualBaisse ? '✓' : '+'}
                    </button>
                )}
            </div>

            {/* IMAGE */}
            <div className="relative w-22 h-30 shrink-0 rounded-xl overflow-hidden">
                <img
                    src={art.photo_url || '...'}
                    className="w-full h-full object-cover"
                    alt={art.nom}
                />
            </div>

            {/* INFOS & STATS */}
            <div className="flex flex-col gap-1">
                <h3 className="text-sm font-semibold line-clamp-2">{art.nom}</h3>
                <div className="flex gap-3 text-[10px] font-mono font-bold text-slate-500 bg-slate-50 p-2 rounded-lg border border-slate-100">
                    <span title="En vente depuis">⌛️ {art.jours_en_vente}j</span>
                    <span title="Depuis republication">📅 {art.jours_en_ligne}j</span>
                    <span title="Vues réelles">👁️ {art.v_reel}</span>
                    <span title="Favoris réels">❤️ {art.f_reel}</span>
                    <span title="Rang">🚩 {art.rang}</span>
                    {art.prix_vente <= art.prix_plancher && art.prix_plancher > 0 && (
                        <span className="bg-orange-100 text-orange-700 border border-orange-200 px-2 py-1 rounded-md text-[9px] font-bold uppercase tracking-tighter">
                            ⚠️ PLANCHER ATTEINT ({art.prix_plancher}€)
                        </span>
                    )}
                    {art.is_low_perf && (
                        <span className={`px-2 py-1 rounded-md text-[9px] font-bold uppercase tracking-tighter border ${
                            art.baisse_prix_taux != null
                                ? 'bg-red-50 text-red-600 border-red-200'
                                : 'bg-slate-100 text-slate-400 border-slate-200'
                        }`}>
                            {art.baisse_prix_taux != null ? `📉 Baisse -${art.baisse_prix_taux}%` : '🚫 Aucune baisse'}
                        </span>
                    )}
                    {art.action_label && art.action_label !== "✅ OK" && (
                        <div className="my-0.5">
                            <div
                                title={art.pepite_taux_raison || art.baisse_prix_raison || undefined}
                                className={`text-[10px] font-black tracking-wide text-slate-700 bg-slate-100 px-2 py-1 rounded-md w-fit border border-slate-200 shadow-sm ${
                                    (art.pepite_taux_raison || art.baisse_prix_raison) ? 'cursor-help' : ''
                                }`}
                            >
                                {art.action_label}
                            </div>
                            {/* Raison du taux choisi, toujours visible (pas seulement au survol)
                                -- explique pourquoi CE pourcentage précis a été retenu (tranche de
                                prix, ancienneté...), cf. échange du 19/09/2026. */}
                            {(art.pepite_taux_raison || art.baisse_prix_raison) && (
                                <p className="text-[9px] text-slate-400 italic mt-0.5 pl-1">
                                    {art.pepite_taux_raison || art.baisse_prix_raison}
                                </p>
                            )}
                        </div>
                    )}
                    {(art.action_label?.includes("REPUBLIER avec BAISSE") || art.action_label?.includes("baisse déjà appliquée")) && art.is_done && (
                        <span className="bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-1 rounded-md text-[9px] font-bold uppercase tracking-tighter">
                            ✅ Baisse déjà appliquée
                        </span>
                    )}
                </div>

                {/* ATTIRANCE -- affichage discret (puce de couleur), plus de barre pleine largeur */}
                <div className="flex items-center gap-1.5 mt-1">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">ATTIRANCE</span>
                    {art.v_reel >= SEUIL_MIN_VUES_SCORE_FIABLE ? (
                        <div className="flex items-center gap-1.5">
                            <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${temp.dot}`} />
                            <span className={`text-[10px] font-bold ${temp.text}`}>{art.score}%</span>
                            <TendanceIndicateur trend={trend} />
                        </div>
                    ) : (
                        <span className="text-[10px] font-medium text-slate-400 italic">
                            ⏳ {art.v_reel}/{SEUIL_MIN_VUES_SCORE_FIABLE} vues
                        </span>
                    )}
                </div>
            </div>

            {/* BADGES DE STATUT */}
            <div className="w-full flex flex-wrap items-center justify-center gap-x-1 gap-y-1 border border-slate-50 h-full px-4 py-2">
                {art.is_new && (
                    <span className="flex items-center gap-x-1.5 rounded-md px-2 py-1 text-xs font-medium bg-blue-50 text-blue-600">
                        Nouveau
                    </span>
                )}
                {art.action_label?.includes("LIQUIDATION") && (
                    <span className={`px-2 py-1 rounded text-[10px] font-black uppercase ${art.action_label.includes("Poste OK")
                        ? 'bg-slate-200 text-slate-600'
                        : 'bg-red-600 text-white animate-pulse'
                        }`}>
                        💀 Liquidation
                    </span>
                )}
                {art.is_very_old && (
                    <span className="bg-amber-950 text-amber-400 px-2 py-1 rounded-md text-[9px] font-black tracking-widest uppercase flex items-center gap-1 shadow-sm border border-amber-800">
                        🚨 URGENCE SORTIE
                    </span>
                )}
                {art.dressing && (
                    <span className={`text-[9px] font-black px-2 py-0.5 rounded-md border uppercase tracking-tighter ${getDressingStyles(art.dressing)}`}>
                        {art.dressing}
                    </span>
                )}
                <span className={`flex items-center gap-x-1.5 rounded-md px-2 py-1 text-xs font-medium ${art.is_critical ? 'bg-red-100/50 text-red-700' : 'bg-green-50 text-green-700'
                    }`}>
                    {art.is_critical ? 'Critique' : 'Stable'}
                </span>
                {art.is_invisible && (
                    <span className="bg-slate-800 text-white px-2 py-1 rounded-md text-[9px] font-bold tracking-wider uppercase flex items-center gap-1 shadow-sm">
                        👻 Invisible
                    </span>
                )}
                {art.is_audit && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest bg-amber-100 text-amber-700 border border-amber-200">
                        À auditer
                    </span>
                )}
                {art.is_audit && (
                    <span
                        title={art.action_detail}
                        className="px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest bg-amber-100 text-amber-700 border border-amber-200 cursor-help"
                    >
                        🔍 {art.nb_republications_sans_vente}× sans vente
                    </span>
                )}
                {art.erreur_clemz && (
                    <span
                        title={art.erreur_clemz_reason || "Erreur Clemz — intervention manuelle nécessaire"}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[10px] font-black uppercase tracking-wider bg-red-600 text-white shadow-sm cursor-help"
                    >
                        ⚠️ Erreur Clemz
                    </span>
                )}
            </div>

            {/* PRIX ET ACTIONS */}
            <div className="flex items-center justify-end gap-6">
                <div className="text-right">
                    <p className="text-[9px] text-slate-400 uppercase tracking-wide">Achat</p>
                    <p className="text-sm font-black">{art.prix_achat > 0 ? `${art.prix_achat.toFixed(2)}€` : "—"}</p>
                </div>
                {art.prix_achat > 0 && (
                    <>
                        <div className="text-right px-3 border-l border-slate-100">
                            <p className="text-[9px] text-orange-400 uppercase tracking-wide font-bold">Plancher</p>
                            <p className="text-sm font-black text-orange-600">
                                {(art.prix_plancher || 0).toFixed(2)}€
                            </p>
                        </div>
                        <div className="text-right px-3 border-l border-slate-100">
                            <p className="text-[9px] text-emerald-400 uppercase tracking-wide font-bold">Optimiste (x2.6)</p>
                            <p className="text-sm font-black text-emerald-600">
                                {(art.prix_optimiste || 0).toFixed(2)}€
                            </p>
                        </div>
                    </>
                )}
                <div className="text-right pl-3 border-l border-slate-100">
                    <p className="text-[9px] text-slate-400 uppercase tracking-wide">Vente</p>
                    <p className={`text-xs font-medium ${art.action_label?.includes("LIQUIDATION") ? 'text-red-600 animate-bounce' : 'text-blue-600'}`}>
                        {(art.prix_vente || 0).toFixed(2)}€
                    </p>
                </div>

                <div className="flex items-center justify-end gap-3 pr-4">
                    {/*
                      Taux réellement affiché : art.baisse_prix_taux (10/15/20 par tranche
                      de prix, calculé côté backend par get_processed_inventory). Bouton masqué si le
                      plancher est atteint (aucune baisse possible -- même logique que
                      l'exclusion faite dans automation_service.run_baisse_prix_auto) ou
                      si baisse_prix_taux est absent/null pour une autre raison.
                    */}
                    {(art.is_stuck || art.is_statut_quo || art.is_low_perf) &&
                        art.baisse_prix_taux != null && (
                        <button
                            onClick={() => onTreat(art.id)}
                            disabled={art.is_done}
                            className="group flex items-center gap-2 px-4 py-2 rounded-lg font-bold text-xs bg-red-50 text-red-600 border border-red-100 hover:bg-red-600 hover:text-white transition-all"
                        >
                            Baisse
                        </button>
                    )}

                    {(art.is_invisible || art.needs_time_republish || art.is_critical) && (
                        <button
                            onClick={() => onRepublish(art.nom, art.id)}
                            disabled={processingIds.has(art.id)}
                            className="group flex items-center gap-2 px-4 py-2 rounded-lg font-bold text-xs bg-blue-50 text-blue-600 border border-blue-100 hover:bg-blue-600 hover:text-white transition-all"
                        >
                            {processingIds.has(art.id) ? 'Mise à jour...' : 'Republication OK'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}