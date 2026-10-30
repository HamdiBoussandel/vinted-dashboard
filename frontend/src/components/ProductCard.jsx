// components/ProductCard.jsx
import React, { useState } from 'react';
import {
    AlertTriangle, Calendar, CalendarClock, Check, CheckCircle2, Clock, Eye, Flag, Heart, History,
    ImageOff, Shirt, TrendingDown,
} from 'lucide-react';
import HistoriqueArticleModal from './HistoriqueArticleModal';

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

// Prochaine action calculée par le backend (art.prochaine_action, 28/10/2026,
// inspirée du "Cycle de vie" de Bleam) : ce que les règles existantes vont
// faire de l'article, et quand.
const ICONES_ACTION = {
    baisse: '📉', republication: '♻️', republication_baisse: '♻️📉', controle: '🔎',
    attente: '⏳', manuel: '✋', aucune: '✔️', inconnue: '❔',
};

function quandLisible(iso) {
    if (!iso) return null;
    const cible = new Date(`${iso}T00:00:00`);
    const aujourdhui = new Date();
    aujourdhui.setHours(0, 0, 0, 0);
    const jours = Math.round((cible - aujourdhui) / 86400000);
    const date = cible.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });
    if (jours < 0) return `en retard (${date})`;
    if (jours === 0) return "aujourd'hui";
    if (jours === 1) return `demain (${date})`;
    return `dans ${jours} j (${date})`;
}

function ProchaineAction({ action }) {
    if (!action || action.type === 'aucune') return null;
    const quand = quandLisible(action.quand);
    const due = action.quand && (action.en_retard || quand === "aujourd'hui");
    return (
        <div className={`px-2.5 py-1.5 rounded-lg border text-[11px] ${due ? 'bg-sky-500/10 border-sky-500/30' : 'bg-white/[0.03] border-white/10'}`}>
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                <span className="font-bold uppercase tracking-wider text-[9px] text-zinc-500">Prochaine action</span>
                <span className="font-semibold text-zinc-200">
                    {ICONES_ACTION[action.type] || '•'} {action.libelle}
                </span>
                {quand && <span className={`font-semibold ${due ? 'text-sky-400' : 'text-zinc-400'}`}>— {quand}</span>}
                <span
                    title={action.automatique ? 'Faite par la routine (baisse de 10h40)' : 'À lancer depuis le dashboard'}
                    className={`px-1.5 rounded font-semibold ${action.automatique ? 'bg-emerald-500/15 text-emerald-400' : 'bg-white/10 text-zinc-400'}`}
                >
                    {action.automatique ? 'auto' : 'manuel'}
                </span>
                {action.auto_bloquee && (
                    <span className="px-1.5 rounded font-semibold bg-amber-500/15 text-amber-400">
                        ⏸ {action.auto_bloquee} — à lancer à la main
                    </span>
                )}
            </div>
            {action.detail && <p className="text-zinc-500 italic mt-0.5">{action.detail}</p>}
        </div>
    );
}

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
        return <span className="text-[9px] text-zinc-500" title="Pas assez d'historique">➡️</span>;
    }

    const config = {
        hausse: { icone: '↗️', couleur: 'text-emerald-500' },
        baisse: { icone: '↘️', couleur: 'text-red-500' },
        stable: { icone: '➡️', couleur: 'text-zinc-400' },
    }[direction];

    return (
        <div className={`flex items-center gap-1 ${config.couleur}`} title={`Score ${delta >= 0 ? '+' : ''}${delta.toFixed(1)} sur la période`}>
            <span className="text-[10px]">{config.icone}</span>
            <span className="text-[9px] font-bold">{delta >= 0 ? '+' : ''}{delta.toFixed(1)}</span>
        </div>
    );
}

function euros(x) {
    return `${Number(x || 0).toFixed(2).replace('.', ',')} €`;
}

function dateCourte(iso) {
    if (!iso) return null;
    return new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
}

function Montant({ valeur, libelle, grand = false, classe = 'text-zinc-200' }) {
    return (
        <div>
            <p className={`font-semibold leading-tight ${grand ? 'text-xl' : 'text-base'} ${classe}`}>{valeur}</p>
            <p className="text-[11px] uppercase tracking-wide text-zinc-500 mt-0.5">{libelle}</p>
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
        if (score >= 12) return { dot: "bg-red-500", text: "text-red-400", label: "🔥 BRÛLANT" };
        if (score >= 8) return { dot: "bg-orange-500", text: "text-orange-400", label: "☀️ CHAUD" };
        if (score >= 5) return { dot: "bg-amber-400", text: "text-amber-400", label: "⛅ TIÈDE" };
        return { dot: "bg-zinc-500", text: "text-zinc-400", label: "❄️ FROID" };
    };

    const temp = getTemperatureStyle(art.score);
    const [historiqueOuvert, setHistoriqueOuvert] = useState(false);

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

    // Couleurs de la carte selon son statut (titre, bordure, pastille en haut à droite)
    const statut = art.is_critical
        ? { libelle: 'Critique', titre: 'text-rose-400', bord: 'border-rose-500/40', pastille: 'bg-rose-500/15 text-rose-400', Icone: AlertTriangle }
        : { libelle: 'Stable', titre: 'text-emerald-400', bord: 'border-emerald-500/40', pastille: 'bg-emerald-500/15 text-emerald-400', Icone: CheckCircle2 };

    const margeBrute = art.prix_achat > 0 ? art.prix_vente - art.prix_achat : null;
    const raison = art.pepite_taux_raison || art.baisse_prix_raison;
    const boutonIcone = 'w-8 h-8 flex items-center justify-center rounded-lg text-zinc-400 hover:text-white hover:bg-white/10 transition-all';
    const puce = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full border border-white/10 text-zinc-300 text-[11px] font-medium';

    return (
        <>
            <div className={`rounded-2xl border bg-[#171717] text-zinc-200 mb-3 transition-all duration-300 ${isExcluded ? 'border-white/10 opacity-50' : statut.bord}`}>
                {/* HAUT : case, photo, informations, statut */}
                <div className="flex items-start gap-4 p-5">
                    {/* Case d'inclusion -- visible uniquement si l'article est éligible à l'action du segment */}
                    {isEligible && (
                        <button
                            role="checkbox"
                            aria-checked={!isExcluded}
                            onClick={onToggleExclude}
                            title={isExcluded ? "Réinclure dans le traitement" : "Exclure du traitement"}
                            className={`w-6 h-6 shrink-0 self-center flex items-center justify-center rounded-md border transition-all ${isExcluded
                                ? 'bg-white/5 border-white/20 text-transparent hover:border-white/40'
                                : 'bg-emerald-500 border-emerald-500 text-black hover:bg-emerald-400'}`}
                        >
                            <Check size={14} strokeWidth={3} />
                        </button>
                    )}

                    {/* Photo */}
                    <div className="w-24 h-24 shrink-0 rounded-2xl overflow-hidden bg-[#262626] flex items-center justify-center">
                        {art.photo_url
                            ? <img src={art.photo_url} className="w-full h-full object-cover" alt={art.nom} />
                            : <ImageOff size={22} className="text-zinc-500" />}
                    </div>

                    {/* Informations */}
                    <div className="flex-1 min-w-0 flex flex-col gap-2">
                        <h3 className={`text-base font-semibold leading-snug line-clamp-2 ${statut.titre}`}>{art.nom}</h3>

                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-400">
                            {art.dressing && (
                                <span className="inline-flex items-center gap-1.5 text-zinc-300">
                                    <Shirt size={14} className="text-zinc-400" /> {art.dressing}
                                </span>
                            )}
                            <span title="Rang" className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/10 text-[11px] font-medium text-zinc-300">
                                <Flag size={11} /> {art.rang}
                            </span>
                            {art.is_new && <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-400 text-[11px] font-medium">Nouveau</span>}
                            {art.is_invisible && <span className="px-1.5 py-0.5 rounded bg-white/10 text-zinc-200 text-[11px] font-medium">👻 Invisible</span>}
                            {art.is_very_old && <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[11px] font-semibold">🚨 Urgence sortie</span>}
                            {art.action_label?.includes("LIQUIDATION") && (
                                <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold ${art.action_label.includes("Poste OK") ? 'bg-white/10 text-zinc-300' : 'bg-rose-500/20 text-rose-400 animate-pulse'}`}>
                                    💀 Liquidation
                                </span>
                            )}
                            {/* Plan "Fin de vie" (28/10/2026) -- remplace l'ancien badge "À auditer" */}
                            {art.fin_de_vie && (
                                <span
                                    title={art.baisse_prix_raison || undefined}
                                    className={`px-1.5 py-0.5 rounded text-[11px] font-semibold cursor-help ${art.sortie ? 'bg-rose-500/15 text-rose-400' : 'bg-amber-500/15 text-amber-400'}`}
                                >
                                    {art.sortie ? '🚪 Sortie'
                                        : `${art.strategie === 'destockage' ? '📦 Déstockage' : '🍂 Fin de vie'} · plancher ${art.plancher_fin_de_vie}€`}
                                </span>
                            )}
                            {art.prix_vente <= art.prix_plancher && art.prix_plancher > 0 && (
                                <span className="px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-400 text-[11px] font-semibold">
                                    ⚠️ Plancher atteint ({art.prix_plancher}€)
                                </span>
                            )}
                            {art.is_low_perf && (
                                <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold ${art.baisse_prix_taux != null ? 'bg-rose-500/15 text-rose-400' : 'bg-white/10 text-zinc-400'}`}>
                                    {art.baisse_prix_taux != null ? `📉 Baisse -${art.baisse_prix_taux}%` : '🚫 Aucune baisse'}
                                </span>
                            )}
                            {(art.action_label?.includes("REPUBLIER avec BAISSE") || art.action_label?.includes("baisse déjà appliquée")) && art.is_done && (
                                <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[11px] font-semibold">✅ Baisse déjà appliquée</span>
                            )}
                            {art.erreur_clemz && (
                                <span
                                    title={art.erreur_clemz_reason || "Erreur Clemz — intervention manuelle nécessaire"}
                                    className="px-1.5 py-0.5 rounded bg-rose-600 text-white text-[11px] font-semibold cursor-help"
                                >
                                    ⚠️ Erreur Clemz
                                </span>
                            )}
                        </div>

                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-zinc-400">
                            <span className="inline-flex items-center gap-1.5">
                                <Calendar size={14} /> {dateCourte(art.date_de_publication) || '—'}
                            </span>
                            {art.date_de_republication && (
                                <span className="text-sky-400">Republié le {dateCourte(art.date_de_republication)}</span>
                            )}
                            <span className={puce} title="En vente depuis"><Clock size={12} /> {art.jours_en_vente} j en vente</span>
                            <span className={puce} title="Depuis la dernière republication"><CalendarClock size={12} /> {art.jours_en_ligne} j en ligne</span>
                            <span className={puce} title="Vues réelles"><Eye size={12} /> {art.v_reel}</span>
                            <span className={puce} title="Favoris réels"><Heart size={12} /> {art.f_reel}</span>
                            {/* ATTIRANCE */}
                            <span className={puce}>
                                <span className="text-zinc-500">Attirance</span>
                                {art.v_reel >= SEUIL_MIN_VUES_SCORE_FIABLE ? (
                                    <>
                                        <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${temp.dot}`} />
                                        <span className={`font-semibold ${temp.text}`}>{art.score}%</span>
                                        <TendanceIndicateur trend={trend} />
                                    </>
                                ) : (
                                    <span className="italic text-zinc-500">{art.v_reel}/{SEUIL_MIN_VUES_SCORE_FIABLE} vues</span>
                                )}
                            </span>
                        </div>

                        {art.action_label && art.action_label !== "✅ OK" && (
                            <p className="text-xs">
                                <span className="font-semibold text-zinc-200">{art.action_label}</span>
                                {/* Raison du taux choisi, toujours visible (cf. échange du 19/09/2026) */}
                                {raison && raison !== art.prochaine_action?.detail && (
                                    <span className="text-zinc-500 italic"> — {raison}</span>
                                )}
                            </p>
                        )}

                        <ProchaineAction action={art.prochaine_action} />
                    </div>

                    {/* Statut */}
                    <span className={`shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-sm font-medium ${statut.pastille}`}>
                        <statut.Icone size={15} /> {statut.libelle}
                    </span>
                </div>

                {/* BAS : montants et actions */}
                <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 px-5 py-4 border-t border-white/10">
                    <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
                        <Montant
                            valeur={euros(art.prix_vente)}
                            libelle="Vente"
                            grand
                            classe={art.action_label?.includes("LIQUIDATION") ? 'text-rose-400' : 'text-white'}
                        />
                        <Montant valeur={art.prix_achat > 0 ? euros(art.prix_achat) : '—'} libelle="Achat" />
                        <Montant valeur={art.prix_achat > 0 ? euros(art.prix_plancher) : '—'} libelle="Plancher" classe="text-sky-400" />
                        <Montant valeur={art.prix_achat > 0 ? euros(art.prix_optimiste) : '—'} libelle="Optimiste" />
                        <Montant
                            valeur={margeBrute === null ? '—' : `${margeBrute >= 0 ? '+' : ''}${euros(margeBrute)}`}
                            libelle="Marge"
                            classe={margeBrute === null ? 'text-zinc-500' : margeBrute >= 0 ? 'text-emerald-400' : 'text-rose-400'}
                        />
                    </div>

                    <div className="flex flex-wrap items-center justify-end gap-1.5">
                        {/* Journal de l'article (28/10/2026) */}
                        <button
                            onClick={() => setHistoriqueOuvert(true)}
                            title="Historique de l'article : baisses, republications, erreurs, vente"
                            aria-label="Historique de l'article"
                            className={boutonIcone}
                        >
                            <History size={18} />
                        </button>
                        {isRepublishSegment && isEligible && (
                            <button
                                onClick={() => onReporterRepublication(art.id)}
                                title="Reporter la republication à demain -- réapparaîtra automatiquement"
                                aria-label="Reporter la republication à demain"
                                className={boutonIcone}
                            >
                                <CalendarClock size={18} />
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
                                aria-label={isSelectedManualBaisse ? "Retirer de la baisse de prix manuelle" : "Ajouter à la baisse de prix manuelle"}
                                aria-pressed={isSelectedManualBaisse}
                                className={isSelectedManualBaisse
                                    ? 'w-8 h-8 flex items-center justify-center rounded-lg bg-rose-500 text-white transition-all'
                                    : boutonIcone}
                            >
                                <TrendingDown size={18} />
                            </button>
                        )}

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
                                className="ml-1.5 px-3.5 py-2 rounded-lg font-semibold text-xs bg-rose-500/15 text-rose-400 hover:bg-rose-500 hover:text-white disabled:opacity-40 transition-all"
                            >
                                Baisse
                            </button>
                        )}

                        {(art.is_invisible || art.needs_time_republish || art.is_critical) && (
                            <button
                                onClick={() => onRepublish(art.nom, art.id)}
                                disabled={processingIds.has(art.id)}
                                className="ml-1.5 px-3.5 py-2 rounded-lg font-semibold text-xs bg-sky-500/15 text-sky-400 hover:bg-sky-500 hover:text-white disabled:opacity-40 transition-all"
                            >
                                {processingIds.has(art.id) ? 'Mise à jour...' : 'Republication OK'}
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {historiqueOuvert && (
                <HistoriqueArticleModal article={art} onClose={() => setHistoriqueOuvert(false)} />
            )}
        </>
    );
}
