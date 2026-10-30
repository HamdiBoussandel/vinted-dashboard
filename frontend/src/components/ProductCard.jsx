// components/ProductCard.jsx
import React, { useState } from 'react';
import {
    AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, Ban, Calendar, CalendarClock, Check, CheckCircle2, CircleDashed, Clock, DoorOpen, Eye, Flag, Flame, Hand, Heart, HelpCircle, History,
    Hourglass, ImageOff, Leaf, Package, Pause, Recycle, Search, Shirt, Siren, Skull, TrendingDown, XCircle,
} from 'lucide-react';
import HistoriqueArticleModal from './HistoriqueArticleModal';
import { AvecIcone, TexteIcone } from './Icones';

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

// Verdict d'attirance (30/10/2026), calé sur l'inventaire réel du jour (126
// articles : 9 attirent, 75 stagnent, 15 en échec, 27 sans assez de vues).
// Doit rester synchronisé avec SEUIL_LOW_PERF (supabase_service.py).
const SEUIL_SCORE_ATTIRE = 5;
// Un seul favori ne prouve rien (1 favori sur 6 vues affiche 16,67 %).
const MIN_FAVORIS_ATTIRE = 2;
// Sous 30 vues, un article réellement à 5 % a encore 21 % de chances de n'avoir
// aucun favori (0,95^30) : on ne parle d'échec qu'au-delà.
const MIN_VUES_ECHEC = 30;

const STYLES_ATTIRANCE = {
    attire: { libelle: 'Attire', Icone: Flame, classes: 'bg-emerald-50 border-emerald-200 text-emerald-700' },
    stagne: { libelle: 'Stagne', Icone: Hourglass, classes: 'bg-amber-50 border-amber-200 text-amber-700' },
    echec: { libelle: 'Échec', Icone: XCircle, classes: 'bg-rose-50 border-rose-200 text-rose-700' },
    inconnu: { libelle: 'Peu de vues', Icone: CircleDashed, classes: 'bg-zinc-50 border-zinc-200 text-zinc-500' },
};

function verdictAttirance({ score, v_reel, f_reel }) {
    if (v_reel < SEUIL_MIN_VUES_SCORE_FIABLE) return 'inconnu';
    if (score >= SEUIL_SCORE_ATTIRE && f_reel >= MIN_FAVORIS_ATTIRE) return 'attire';
    if (score < SEUIL_SCORE_ATTIRE && v_reel >= MIN_VUES_ECHEC) return 'echec';
    return 'stagne';
}

// Prochaine action calculée par le backend (art.prochaine_action, 28/10/2026,
// inspirée du "Cycle de vie" de Bleam) : ce que les règles existantes vont
// faire de l'article, et quand.
const ICONES_ACTION = {
    baisse: TrendingDown, republication: Recycle, republication_baisse: Recycle, controle: Search,
    attente: Hourglass, manuel: Hand, aucune: Check, inconnue: HelpCircle,
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
        <div className={`px-2.5 py-1.5 rounded-lg border text-[11px] ${due ? 'bg-sky-50 border-sky-200' : 'bg-zinc-50 border-zinc-200'}`}>
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                <span className="font-bold uppercase tracking-wider text-[9px] text-zinc-500">Prochaine action</span>
                <span className="font-semibold text-zinc-800">
                    <AvecIcone Icone={ICONES_ACTION[action.type] || HelpCircle}>{action.libelle}</AvecIcone>
                </span>
                {quand && <span className={`font-semibold ${due ? 'text-sky-600' : 'text-zinc-500'}`}>— {quand}</span>}
                <span
                    title={action.automatique ? 'Faite par la routine (baisse de 10h40)' : 'À lancer depuis le dashboard'}
                    className={`px-1.5 rounded font-semibold ${action.automatique ? 'bg-emerald-50 text-emerald-700' : 'bg-zinc-100 text-zinc-500'}`}
                >
                    {action.automatique ? 'auto' : 'manuel'}
                </span>
                {action.auto_bloquee && (
                    <span className="px-1.5 rounded font-semibold bg-amber-50 text-amber-700">
                        <AvecIcone Icone={Pause}>{action.auto_bloquee} — à lancer à la main</AvecIcone>
                    </span>
                )}
            </div>
            {action.detail && <p className="text-zinc-500 italic mt-0.5"><TexteIcone texte={action.detail} /></p>}
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
        return <span className="text-zinc-400" title="Pas assez d'historique"><ArrowRight size={14} /></span>;
    }

    const config = {
        hausse: { Icone: ArrowUpRight, couleur: 'text-emerald-600' },
        baisse: { Icone: ArrowDownRight, couleur: 'text-red-600' },
        stable: { Icone: ArrowRight, couleur: 'text-zinc-500' },
    }[direction];

    return (
        <div className={`flex items-center gap-1 ${config.couleur}`} title={`Score ${delta >= 0 ? '+' : ''}${delta.toFixed(1)} sur la période`}>
            <config.Icone size={14} />
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

function Montant({ valeur, libelle, grand = false, classe = 'text-zinc-800' }) {
    return (
        <div className="font-prix">
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
    const verdict = verdictAttirance(art);
    const attirance = STYLES_ATTIRANCE[verdict];
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

    // Couleurs de la carte selon son statut (titre, pastille en haut à droite)
    const statut = art.is_critical
        ? { libelle: 'Critique', titre: 'text-rose-600', pastille: 'bg-rose-50 text-rose-600', Icone: AlertTriangle }
        : { libelle: 'Stable', titre: 'text-emerald-600', pastille: 'bg-emerald-50 text-emerald-600', Icone: CheckCircle2 };

    const margeBrute = art.prix_achat > 0 ? art.prix_vente - art.prix_achat : null;
    const raison = art.pepite_taux_raison || art.baisse_prix_raison;
    const boutonIcone = 'w-8 h-8 flex items-center justify-center rounded-lg text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 transition-all';
    const puce = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full border border-zinc-200 text-zinc-600 text-[11px] font-medium';

    return (
        <>
            <div className={`rounded-2xl border border-zinc-200 bg-white text-zinc-800 mb-3 transition-all duration-300 ${isExcluded ? 'opacity-50' : ''}`}>
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
                                ? 'bg-white border-zinc-300 text-transparent hover:border-zinc-400'
                                : 'bg-emerald-500 border-emerald-500 text-black hover:bg-emerald-400'}`}
                        >
                            <Check size={14} strokeWidth={3} />
                        </button>
                    )}

                    {/* Photo */}
                    <div className="w-24 h-24 shrink-0 rounded-2xl overflow-hidden bg-zinc-100 flex items-center justify-center">
                        {art.photo_url
                            ? <img src={art.photo_url} className="w-full h-full object-cover" alt={art.nom} />
                            : <ImageOff size={22} className="text-zinc-500" />}
                    </div>

                    {/* Informations */}
                    <div className="flex-1 min-w-0 flex flex-col gap-2">
                        <h3 className={`text-base font-semibold leading-snug line-clamp-2 ${statut.titre}`}>{art.nom}</h3>

                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500">
                            {art.dressing && (
                                <span className="inline-flex items-center gap-1.5 text-zinc-700">
                                    <Shirt size={14} className="text-zinc-500" /> {art.dressing}
                                </span>
                            )}
                            <span title="Rang" className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-100 text-[11px] font-medium text-zinc-600">
                                <Flag size={11} /> {art.rang}
                            </span>
                            {art.is_new && <span className="px-1.5 py-0.5 rounded bg-sky-50 text-sky-600 text-[11px] font-medium">Nouveau</span>}
                            {art.is_very_old && <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[11px] font-semibold"><AvecIcone Icone={Siren}>Urgence sortie</AvecIcone></span>}
                            {art.action_label?.includes("LIQUIDATION") && (
                                <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold ${art.action_label.includes("Poste OK") ? 'bg-zinc-100 text-zinc-600' : 'bg-rose-100 text-rose-600 animate-pulse'}`}>
                                    <AvecIcone Icone={Skull}>Liquidation</AvecIcone>
                                </span>
                            )}
                            {/* Plan "Fin de vie" (28/10/2026) -- remplace l'ancien badge "À auditer" */}
                            {art.fin_de_vie && (
                                <span
                                    title={art.baisse_prix_raison || undefined}
                                    className={`px-1.5 py-0.5 rounded text-[11px] font-semibold cursor-help ${art.sortie ? 'bg-rose-50 text-rose-600' : 'bg-amber-50 text-amber-700'}`}
                                >
                                    {art.sortie ? <AvecIcone Icone={DoorOpen}>Sortie</AvecIcone>
                                        : <AvecIcone Icone={art.strategie === 'destockage' ? Package : Leaf}>{art.strategie === 'destockage' ? 'Déstockage' : 'Fin de vie'} · plancher {art.plancher_fin_de_vie}€</AvecIcone>}
                                </span>
                            )}
                            {art.prix_vente <= art.prix_plancher && art.prix_plancher > 0 && (
                                <span className="px-1.5 py-0.5 rounded bg-orange-50 text-orange-700 text-[11px] font-semibold">
                                    <AvecIcone Icone={AlertTriangle}>Plancher atteint ({art.prix_plancher}€)</AvecIcone>
                                </span>
                            )}
                            {art.is_low_perf && (
                                <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold ${art.baisse_prix_taux != null ? 'bg-rose-50 text-rose-600' : 'bg-zinc-100 text-zinc-500'}`}>
                                    <AvecIcone Icone={art.baisse_prix_taux != null ? TrendingDown : Ban}>{art.baisse_prix_taux != null ? `Baisse -${art.baisse_prix_taux}%` : 'Aucune baisse'}</AvecIcone>
                                </span>
                            )}
                            {(art.action_label?.includes("REPUBLIER avec BAISSE") || art.action_label?.includes("baisse déjà appliquée")) && art.is_done && (
                                <span className="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[11px] font-semibold"><AvecIcone Icone={CheckCircle2}>Baisse déjà appliquée</AvecIcone></span>
                            )}
                            {art.erreur_clemz && (
                                <span
                                    title={art.erreur_clemz_reason || "Erreur Clemz — intervention manuelle nécessaire"}
                                    className="px-1.5 py-0.5 rounded bg-rose-600 text-white text-[11px] font-semibold cursor-help"
                                >
                                    <AvecIcone Icone={AlertTriangle}>Erreur Clemz</AvecIcone>
                                </span>
                            )}
                        </div>

                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-zinc-500">
                            <span className="inline-flex items-center gap-1.5">
                                <Calendar size={14} /> {dateCourte(art.date_de_publication) || '—'}
                            </span>
                            {art.date_de_republication && (
                                <span className="text-sky-600">Republié le {dateCourte(art.date_de_republication)}</span>
                            )}
                            <span className={puce} title="En vente depuis"><Clock size={12} /> {art.jours_en_vente} j en vente</span>
                            <span className={puce} title="Depuis la dernière republication"><CalendarClock size={12} /> {art.jours_en_ligne} j en ligne</span>
                            <span className={puce} title="Vues réelles"><Eye size={12} /> {art.v_reel}</span>
                            <span className={puce} title="Favoris réels"><Heart size={12} /> {art.f_reel}</span>
                        </div>

                        {art.action_label && art.action_label !== "✅ OK" && (
                            <p className="text-xs">
                                <span className="font-semibold text-zinc-800"><TexteIcone texte={art.action_label} /></span>
                                {/* Raison du taux choisi, toujours visible (cf. échange du 19/09/2026) */}
                                {raison && !art.prochaine_action?.detail?.includes(raison) && (
                                    <span className="text-zinc-500 italic"> — <TexteIcone texte={raison} /></span>
                                )}
                            </p>
                        )}

                        <ProchaineAction action={art.prochaine_action} />
                    </div>

                    {/* Statut et attirance : même emplacement sur chaque carte, pour comparer d'un coup d'œil */}
                    <div className="shrink-0 flex flex-col items-end gap-2">
                        <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-sm font-medium ${statut.pastille}`}>
                            <statut.Icone size={15} /> {statut.libelle}
                        </span>
                        <div
                            title={`Attirance : ${art.v_reel} vues, ${art.f_reel} favoris`}
                            className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border ${attirance.classes}`}
                        >
                            <attirance.Icone size={20} className="shrink-0" />
                            <div className="leading-tight">
                                <p className="text-[9px] font-semibold uppercase tracking-wider opacity-70">Attirance</p>
                                <p className="text-sm font-bold">
                                    {attirance.libelle}
                                    {verdict === 'inconnu'
                                        ? <span className="ml-1 text-[11px] font-medium">{art.v_reel}/{SEUIL_MIN_VUES_SCORE_FIABLE}</span>
                                        : <span className="ml-1 text-xs font-semibold">{art.score}%</span>}
                                </p>
                            </div>
                            {verdict !== 'inconnu' && <TendanceIndicateur trend={trend} />}
                        </div>
                    </div>
                </div>

                {/* BAS : montants et actions */}
                <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 px-5 py-4 border-t border-zinc-200">
                    <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
                        <Montant
                            valeur={euros(art.prix_vente)}
                            libelle="Prix Vinted"
                            grand
                            classe={art.action_label?.includes("LIQUIDATION") ? 'text-rose-600' : 'text-zinc-900'}
                        />
                        <Montant valeur={art.prix_achat > 0 ? euros(art.prix_achat) : '—'} libelle="Achat" classe="text-zinc-500" />
                        <Montant valeur={art.prix_achat > 0 ? euros(art.prix_plancher) : '—'} libelle="Plancher" classe="text-amber-700" />
                        <Montant valeur={art.prix_achat > 0 ? euros(art.prix_optimiste) : '—'} libelle="Optimiste" classe="text-sky-600" />
                        <Montant
                            valeur={margeBrute === null ? '—' : `${margeBrute >= 0 ? '+' : ''}${euros(margeBrute)}`}
                            libelle="Marge"
                            classe={margeBrute === null ? 'text-zinc-500' : margeBrute >= 0 ? 'text-emerald-600' : 'text-rose-600'}
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
                                className="ml-1.5 px-3.5 py-2 rounded-lg font-semibold text-xs bg-rose-50 text-rose-600 hover:bg-rose-500 hover:text-white disabled:opacity-40 transition-all"
                            >
                                Baisse
                            </button>
                        )}

                        {(art.is_invisible || art.needs_time_republish || art.is_critical) && (
                            <button
                                onClick={() => onRepublish(art.nom, art.id)}
                                disabled={processingIds.has(art.id)}
                                className="ml-1.5 px-3.5 py-2 rounded-lg font-semibold text-xs bg-sky-50 text-sky-600 hover:bg-sky-500 hover:text-white disabled:opacity-40 transition-all"
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
