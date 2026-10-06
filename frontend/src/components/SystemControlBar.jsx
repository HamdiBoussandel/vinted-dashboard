// components/SystemControlBar.jsx
import React, { useState, useEffect, useCallback } from 'react';
import { AlertCircle, CheckCircle2, Shirt, TrendingDown } from 'lucide-react';
import { riskGuardService } from '../services/api';

const INTERVALLE_RISK_GUARD_MS = 60000;
const INTERVALLE_SERVEUR_MS = 8000;

const LABEL_TYPE_TACHE = {
    scraping: 'Scraping',
    republication: 'Republication',
    baisse_prix: 'Baisse de prix',
    partage_vues_favoris: 'Partage vues/favoris',
};

// Même logique que TaskHistory.jsx (getDressings) -- une tâche travaille soit
// par produit (résultats avec "dressing"), soit par compte (résultats avec
// "account", ex: "Chrome - Dressing 1" pour le partage vues/favoris).
function extraireDressings(task) {
    if (!task.results?.length) return [];
    const set = new Set();
    task.results.forEach((r) => {
        if (r.dressing) {
            set.add(r.dressing);
        } else if (r.account) {
            const match = r.account.match(/Dressing\s*\d+/i);
            set.add(match ? match[0] : r.account);
        }
    });
    return Array.from(set);
}

// Construit un libellé "Republication (Dressing 2) • Baisse de prix (Dressing 1)"
// à partir des tâches encore "running" dans l'historique -- couvre le scraping
// ET les automatisations (republication/baisse de prix/partage vues-favoris),
// contrairement à l'ancien indicateur limité au scraping (is_clemz_running).
function construireLabelTachesEnCours(runningTasks) {
    if (!runningTasks.length) return null;
    return runningTasks
        .map((task) => {
            const label = LABEL_TYPE_TACHE[task.type] || task.type;
            const dressings = extraireDressings(task);
            return dressings.length ? `${label} (${dressings.join(', ')})` : label;
        })
        .join(' • ');
}

// "14:32:07" -> "14h32"
function formaterHeure(horaireStr) {
    if (!horaireStr) return '';
    const [h, m] = horaireStr.split(':');
    return `${parseInt(h, 10)}h${m}`;
}

const LIBELLE_NIVEAU = {
    repos_force: 'repos forcé après activité consécutive',
    calendrier: 'calendrier',
};

// Version courte pour le badge visible en permanence (pas juste au survol) --
// demande explicite du 21/09/2026 : savoir d'un coup d'œil si un repos vient
// du pur hasard (tirage 25%, aucun signal de sécurité derrière) ou d'un vrai
// signal anti-détection (seuil de volume franchi, convalescence).
const LIBELLE_NIVEAU_COURT = {
    calendrier: 'tirage 25%',
    repos_force: 'seuil volume',
    convalescence: 'convalescence',
};

// Construit le tooltip enrichi à partir du plan du jour (niveau ayant tranché
// -- convalescence / repos forcé / calendrier -- + actif/pause + horaire/volume
// prévus). Retombe sur la seule convalescence si le plan du jour n'a pas
// encore été généré (ex: juste après un redémarrage backend, avant le
// rattrapage de main.py).
function construireTooltipAntiDetection(label, convalescence, planDuJour) {
    if (!planDuJour) {
        if (!convalescence) return `${label} : aucune restriction active.`;
        const { rang, duree_palier_jours, jours_restants, jour_actif, ecart_jours_precedente } = convalescence;
        const rangTexte = rang === 1 ? '1re' : `${rang}e`;
        const ecartTexte = ecart_jours_precedente != null
            ? ` en ${ecart_jours_precedente} jour${ecart_jours_precedente > 1 ? 's' : ''}`
            : '';
        const jourTexte = jour_actif ? 'jour actif' : 'jour de pause';
        return (
            `${label} : ${rangTexte} restriction${ecartTexte}, palier ${duree_palier_jours}j ` +
            `(${jours_restants}j restant${jours_restants > 1 ? 's' : ''}), aujourd'hui = ${jourTexte} ` +
            `(plan du jour pas encore généré).`
        );
    }

    const { niveau, actif, horaire, volume_cible } = planDuJour;

    if (niveau === 'convalescence') {
        const conv = planDuJour.convalescence || convalescence || {};
        const rangTexte = conv.rang === 1 ? '1re' : `${conv.rang}e`;
        const ecartTexte = conv.ecart_jours_precedente != null
            ? ` en ${conv.ecart_jours_precedente} jour${conv.ecart_jours_precedente > 1 ? 's' : ''}`
            : '';
        const base = `${label} : ${rangTexte} restriction${ecartTexte}, palier ${conv.duree_palier_jours}j ` +
            `(${conv.jours_restants}j restant${conv.jours_restants > 1 ? 's' : ''})`;
        return actif
            ? `${base}, actif aujourd'hui, prévu vers ${formaterHeure(horaire)} (volume cible : ${volume_cible})`
            : `${base}, repos aujourd'hui`;
    }

    if (actif) {
        const mention = niveau === 'continuite'
            ? " -- MODE CONTINUITÉ : repos Niveau 2 levé volontairement pour garder un dressing actif, volume plafonné"
            : '';
        return `${label} : actif aujourd'hui, prévu vers ${formaterHeure(horaire)} (volume cible : ${volume_cible})${mention}`;
    }
    return `${label} : repos aujourd'hui (raison : ${LIBELLE_NIVEAU[niveau] || niveau})`;
}

// Petit point de statut anti-détection (risk_guard.py) -- purement informatif,
// aucune automatisation n'est bloquée par ceci. Rouge clignotant si le
// dressing est en convalescence (palier à sévérité croissante selon la
// récidive) -- niveau 2/3 ne changent JAMAIS la couleur, seulement le texte du
// tooltip. Vert plein = actif aujourd'hui, vert en anneau = repos aujourd'hui
// (repos forcé ou calendrier), pour distinguer les deux sans ajouter de
// couleur -- cf. discussion, à ajuster si une autre approche est préférée.
function LedAntiDetection({ label, convalescence, planDuJour }) {
    const enConvalescence = !!convalescence;
    const reposAujourdhui = !enConvalescence && planDuJour != null && !planDuJour.actif;

    const tooltip = construireTooltipAntiDetection(label, convalescence, planDuJour);

    let classeCentre = 'bg-emerald-500'; // actif, ou statut pas encore connu -> plein par défaut
    if (enConvalescence) {
        classeCentre = 'bg-red-500';
    } else if (reposAujourdhui) {
        classeCentre = 'bg-white border-2 border-emerald-500';
    }

    return (
        <span title={tooltip} className="relative flex h-2 w-2 shrink-0">
            {enConvalescence && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
            )}
            <span className={`relative inline-flex rounded-full h-2 w-2 ${classeCentre}`}></span>
        </span>
    );
}

// Objectif du jour visible en permanence (pas seulement au survol de la LED) --
// utile en mode manuel tant que l'automatisation reste désactivée : combien
// d'articles republier aujourd'hui pour ce dressing (plafond RÉELLEMENT
// appliqué par le bouton manuel, cf. _plafonner_par_plan_du_jour côté
// backend), et à côté combien de baisses de prix restent possibles --
// affiché même si la republication est en repos, les deux quotas n'étant pas
// gouvernés par la même règle (baisse_prix n'a pas de plan du jour propre,
// cf. échange du 11/09/2026).
function ObjectifDuJourBadge({ planDuJour, quotaBaissePrix, quotaRepublication }) {
    // quotaRepublication : ce qu'il reste RÉELLEMENT à faire aujourd'hui,
    // distinct de planDuJour.volume_cible (la cible du jour, jamais
    // décrémentée) -- sans lui, "9 auj." restait affiché avec un point vert
    // plein même une fois la cible atteinte, aucun moyen de distinguer "9 à
    // faire" de "9 faits, 0 restant" (cf. échange du 15/09/2026).
    const badgeRepublication = !planDuJour ? null : !planDuJour.actif ? (
        <span
            title="Raison du repos : si c'est le tirage 25% (niveau 'calendrier'), aucun signal de sécurité derrière -- c'est le pur hasard qui est tombé sur repos aujourd'hui."
            className="flex items-center gap-1"
        >
            <Shirt size={11} className="shrink-0" /> Repos auj.
            {' '}({LIBELLE_NIVEAU_COURT[planDuJour.niveau] || planDuJour.niveau})
        </span>
    ) : (
        <span
            title="Volume et horaire de republication conseillés par le plan du jour ; le nombre entre parenthèses est ce qu'il reste réellement à faire aujourd'hui (plafond réellement appliqué par le bouton manuel)."
            className="flex items-center gap-1"
        >
            <Shirt size={11} className="shrink-0" />
            {planDuJour.volume_cible} auj.
            {quotaRepublication != null && ` (${quotaRepublication} restant${quotaRepublication > 1 ? 's' : ''})`}
            {' '}• vers {formaterHeure(planDuJour.horaire)}
        </span>
    );

    const badgeBaissePrix = quotaBaissePrix == null ? null : (
        <span
            title="Nombre de baisses de prix que le bouton manuel autoriserait réellement maintenant (quota du jour + niveaux anti-détection)."
            className="flex items-center gap-1"
        >
            <TrendingDown size={11} className="shrink-0" />
            {quotaBaissePrix} baisse{quotaBaissePrix > 1 ? 's' : ''}
        </span>
    );

    if (!badgeRepublication && !badgeBaissePrix) return null;

    return (
        <span className="flex items-center gap-2 text-[9px] font-black text-slate-500 uppercase tracking-wide">
            {badgeRepublication}
            {badgeBaissePrix}
        </span>
    );
}

export default function SystemControlBar({
    sysState,
    handleReconnect
}) {
    const [riskGuardStatut, setRiskGuardStatut] = useState({});
    const [serverOnline, setServerOnline] = useState(true);
    const [runningTasksLabel, setRunningTasksLabel] = useState(null);

    useEffect(() => {
        let annule = false;

        const rafraichir = async () => {
            try {
                const statut = await riskGuardService.getStatut();
                if (!annule) setRiskGuardStatut(statut);
            } catch {
                // Silencieux -- purement informatif, ne pas spammer l'utilisateur
                // si un poll échoue ponctuellement.
            }
        };

        rafraichir();
        const interval = setInterval(rafraichir, INTERVALLE_RISK_GUARD_MS);
        return () => {
            annule = true;
            clearInterval(interval);
        };
    }, []);

    // Sert DEUX rôles avec un seul poll : (1) détecter si le serveur FastAPI
    // répond (indicateur "SERVEUR OK", indépendant de has_error qui reflétait
    // des erreurs métier de scraping, pas la joignabilité du serveur), et (2)
    // lister les tâches encore "running" dans l'historique (scraping +
    // republication/baisse de prix/partage vues-favoris) pour le libellé
    // Synchronisation.
    const rafraichirServeur = useCallback(async () => {
        try {
            const res = await fetch('http://localhost:8000/api/automation/history?limit=10');
            if (!res.ok) throw new Error('Erreur réseau');
            const data = await res.json();
            setServerOnline(true);
            const enCours = (data.history || []).filter((t) => t.global_status === 'running');
            setRunningTasksLabel(construireLabelTachesEnCours(enCours));
        } catch {
            setServerOnline(false);
            setRunningTasksLabel(null);
        }
    }, []);

    useEffect(() => {
        rafraichirServeur();
        const interval = setInterval(rafraichirServeur, INTERVALLE_SERVEUR_MS);
        return () => clearInterval(interval);
    }, [rafraichirServeur]);

    return (
        <div className="flex flex-col gap-4 bg-white p-4 rounded-3xl border-3 border-brand-50 shadow-soft mb-6">

            {/* LIGNE 1 : statut, badges, historique, boutons */}
            <div className="flex flex-wrap items-center gap-6">

                {/* BLOC GAUCHE : STATUT DE SYNCHRONISATION */}
                <div className="flex items-center gap-4 bg-slate-50/50 p-1.5 rounded-xl border border-slate-100">
                    <div className="flex flex-col px-3 border-r border-slate-200">
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-wider">
                            Synchronisation
                        </span>

                        {runningTasksLabel ? (
                            /* --- ÉTAT : TÂCHE(S) EN COURS (scraping, republication, baisse de
                               prix ou partage vues-favoris -- avec le/les dressing(s) concerné(s)) --- */
                            <div className="flex items-center gap-2">
                                <span className="relative flex h-2 w-2">
                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                                    <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                                </span>
                                <span className="text-sm font-bold text-indigo-600 animate-pulse">
                                    {runningTasksLabel}
                                </span>
                            </div>
                        ) : (
                            /* --- ÉTAT : EN ATTENTE (Par défaut) --- */
                            <span className="text-sm font-bold text-slate-600">
                                {sysState.status_TEST || "En attente"}
                            </span>
                        )}
                    </div>

                    {/* Indicateur de joignabilité du serveur FastAPI -- purement technique,
                        ne reflète plus les erreurs métier de scraping (has_error), qui restent
                        visibles via les LED D1/D2 + boutons de reconnexion ci-dessous. */}
                    <div className="flex flex-col justify-center">
                        {serverOnline ? (
                            <span className="flex items-center gap-1 text-[10px] font-black text-emerald-500 bg-emerald-50 px-2 py-1 rounded">
                                <CheckCircle2 size={12} /> SERVEUR OK
                            </span>
                        ) : (
                            <div className="flex items-center gap-1.5">
                                <span className="flex items-center gap-1 text-[10px] font-black text-red-500 bg-red-50 px-2 py-1 rounded">
                                    <AlertCircle size={12} /> SERVEUR INJOIGNABLE
                                </span>
                                <button
                                    onClick={rafraichirServeur}
                                    title="Réessayer de contacter le serveur"
                                    className="text-[10px] font-black px-2 py-1 rounded-lg transition-all bg-amber-500 text-white hover:scale-105"
                                >
                                    🔄 Reconnecter
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Statut de connexion par dressing + bouton de reconnexion */}
                    <div className="flex items-center gap-1.5">
                        {[
                            { key: 'dressing1', dKey: 'd1', label: 'D1' },
                            { key: 'dressing2', dKey: 'd2', label: 'D2' },
                        ].map(({ key, dKey, label }) => {
                            const ok = sysState.session_status?.[key];
                            const isReconnecting = sysState.reconnect_status?.[key] === 'en_cours';
                            const convalescence = riskGuardStatut[key]?.convalescence ?? null;
                            const planDuJour = riskGuardStatut[key]?.plan_du_jour ?? null;
                            const quotaBaissePrix = riskGuardStatut[key]?.quota_baisse_prix_restant ?? null;
                            const quotaRepublication = riskGuardStatut[key]?.quota_republication_restant ?? null;

                            if (ok === null || ok === undefined) {
                                return (
                                    <div key={key} className="flex items-center gap-1">
                                        <span className="flex items-center gap-1 text-[10px] font-black text-slate-400 bg-slate-50 px-2 py-1 rounded">
                                            {label} —
                                        </span>
                                        <LedAntiDetection label={label} convalescence={convalescence} planDuJour={planDuJour} />
                                        <ObjectifDuJourBadge planDuJour={planDuJour} quotaBaissePrix={quotaBaissePrix} quotaRepublication={quotaRepublication} />
                                    </div>
                                );
                            }

                            if (ok) {
                                return (
                                    <div key={key} className="flex items-center gap-1">
                                        <span className="flex items-center gap-1 text-[10px] font-black text-emerald-500 bg-emerald-50 px-2 py-1 rounded">
                                            <CheckCircle2 size={12} /> {label}
                                        </span>
                                        <LedAntiDetection label={label} convalescence={convalescence} planDuJour={planDuJour} />
                                        <ObjectifDuJourBadge planDuJour={planDuJour} quotaBaissePrix={quotaBaissePrix} quotaRepublication={quotaRepublication} />
                                    </div>
                                );
                            }

                            return (
                                <div key={key} className="flex items-center gap-1">
                                    <span className="flex items-center gap-1 text-[10px] font-black text-red-500 bg-red-50 px-2 py-1 rounded">
                                        <AlertCircle size={12} /> {label} déco
                                    </span>
                                    <button
                                        onClick={() => handleReconnect(dKey)}
                                        disabled={isReconnecting}
                                        title={`Ouvrir le navigateur pour reconnecter ${label}`}
                                        className={`text-[10px] font-black px-2 py-1 rounded-lg transition-all ${
                                            isReconnecting
                                                ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                                                : 'bg-amber-500 text-white hover:scale-105'
                                        }`}
                                    >
                                        {isReconnecting ? '⏳ ...' : '🔄 Reconnecter'}
                                    </button>
                                    <LedAntiDetection label={label} convalescence={convalescence} planDuJour={planDuJour} />
                                    <ObjectifDuJourBadge planDuJour={planDuJour} quotaBaissePrix={quotaBaissePrix} quotaRepublication={quotaRepublication} />
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* BLOC CENTRE : HISTORIQUE DES CRONS */}
                <div className="flex items-center gap-8 ml-auto mr-8">
                    {/* CRON 14H */}
                    <div className="flex flex-col">
                        <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">
                            Dernier Scan 14h
                        </span>
                        <span className="text-[10px] font-black text-slate-600 uppercase">
                            {sysState.last_cron_14h || '---'}
                        </span>
                    </div>

                    {/* CRON 22H */}
                    <div className="flex flex-col">
                        <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">
                            Dernier Scan 22h
                        </span>
                        <span className="text-[10px] font-black text-indigo-600 uppercase">
                            {sysState.last_cron_22h || '---'}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
}