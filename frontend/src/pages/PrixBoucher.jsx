// pages/PrixBoucher.jsx
// Stratégie "prix de boucher" (13/10/2026) : affiche, pour
// chaque article publié ou republié récemment, s'il "fonctionne" à son prix haut et la marche de prix
// qui sera appliquée sinon par la routine (automation_service.run_baisse_prix_auto).
// Aucun prix n'est modifié depuis cette page elle-même.
import React, { useState, useEffect } from 'react';
import { Eye, CheckCircle, TrendingDown, Clock, Minus, RefreshCw } from 'lucide-react';
import { prixBoucherService } from '../services/api';

const STATUTS = {
    ne_fonctionne_pas: { label: 'Ne fonctionne pas', icon: <TrendingDown size={13} />, classes: 'bg-red-50 text-red-700 border-red-100' },
    plancher_atteint: { label: 'Plancher atteint', icon: <Minus size={13} />, classes: 'bg-slate-100 text-slate-600 border-slate-200' },
    fonctionne: { label: 'Fonctionne', icon: <CheckCircle size={13} />, classes: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
    trop_tot: { label: 'Trop tôt', icon: <Clock size={13} />, classes: 'bg-slate-50 text-slate-400 border-slate-100' },
};

function euros(valeur) {
    return valeur === null || valeur === undefined ? '—' : `${valeur.toFixed(2).replace('.', ',')} €`;
}

function ecart(depuis, vers) {
    if (!depuis || vers === null || vers === undefined) return null;
    return Math.round((vers - depuis) / depuis * 100);
}

export default function PrixBoucher() {
    const [donnees, setDonnees] = useState(null);
    const [erreur, setErreur] = useState(false);
    const [chargement, setChargement] = useState(true);

    const charger = async () => {
        setChargement(true);
        try {
            setDonnees(await prixBoucherService.getObservation());
            setErreur(false);
        } catch {
            setErreur(true);
        } finally {
            setChargement(false);
        }
    };

    useEffect(() => { charger(); }, []);

    const articles = donnees?.articles ?? [];
    const regles = donnees?.regles;
    const compte = (statut) => articles.filter((a) => a.statut === statut).length;

    return (
        <div className="p-8">
            <div className="flex items-start justify-between mb-6">
                <div>
                    <h1 className="text-2xl font-black text-slate-800">Prix de boucher</h1>
                    <p className="text-sm text-slate-400 mt-1">
                        Articles publiés ou republiés volontairement au-dessus du prix visé : lesquels tiennent leur prix, lesquels faudrait-il baisser.
                    </p>
                </div>
                <button onClick={charger} className="p-2 rounded-lg text-slate-500 hover:bg-white" title="Recharger">
                    <RefreshCw size={16} />
                </button>
            </div>

            <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 mb-6 max-w-5xl flex items-start gap-3">
                <Eye size={16} className="text-blue-500 shrink-0 mt-0.5" />
                <div className="text-xs text-blue-800 space-y-1">
                    <p className="font-bold">
                        Baisses actives : les prix conseillés sont appliqués par la baisse de prix de la routine (10h40), les jours actifs du dressing,
                        en priorité dans le quota de 10 baisses par jour.
                    </p>
                    {regles && (
                        <p>
                            Contrôle à J+{regles.jour_premier_controle} puis tous les {regles.intervalle_controles_jours} jours.
                            Un article « fonctionne » à partir de {regles.seuil_favoris_reels} vrais favoris (boost Clemz de 20 déduit).
                            Sinon, marche conseillée de -{regles.pas_marche_pourcent} %, jamais sous le plancher de sa tranche
                            (-26 % sous 50 €, -24 % de 50 à 80 €, -17 % au-dessus), calculé sur le plus haut prix connu de l'article,
                            ni sous le plancher de marge. Articles suivis : publiés ou republiés depuis moins de {regles.fenetre_jours} jours,
                            hors liquidation — les jours comptent depuis la dernière (re)publication.
                        </p>
                    )}
                    {regles && (
                        <p>
                            Une marche manquée (jour de repos) part au jour actif suivant ; deux marches d'un même article
                            sont espacées d'au moins {regles.intervalle_controles_jours} jours.
                        </p>
                    )}
                </div>
            </div>

            {chargement ? (
                <p className="text-sm text-slate-400">Chargement...</p>
            ) : erreur ? (
                <p className="text-sm text-red-500">Impossible de charger l'observation (backend injoignable).</p>
            ) : articles.length === 0 ? (
                <p className="text-sm text-slate-500">Aucun article publié ou republié récemment à observer pour le moment.</p>
            ) : (
                <div className="max-w-5xl">
                    <div className="flex flex-wrap gap-2 mb-4">
                        {Object.entries(STATUTS).map(([cle, cfg]) => (
                            <span key={cle} className={`flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-lg border ${cfg.classes}`}>
                                {cfg.icon} {cfg.label} : {compte(cle)}
                            </span>
                        ))}
                    </div>

                    <div className="bg-white rounded-xl border border-slate-100 overflow-hidden">
                        <table className="w-full text-xs">
                            <thead className="bg-slate-50 text-slate-500 text-[10px] uppercase tracking-wide">
                                <tr>
                                    <th className="text-left px-3 py-2">Article</th>
                                    <th className="text-center px-2 py-2">Jour</th>
                                    <th className="text-right px-2 py-2">Prix de départ</th>
                                    <th className="text-right px-2 py-2">Prix actuel</th>
                                    <th className="text-center px-2 py-2">Vrais favoris</th>
                                    <th className="text-left px-2 py-2">Statut</th>
                                    <th className="text-right px-3 py-2">Prix conseillé</th>
                                </tr>
                            </thead>
                            <tbody>
                                {articles.map((a) => {
                                    const cfg = STATUTS[a.statut];
                                    const baisse = ecart(a.prix_actuel, a.prix_conseille);
                                    const deja = ecart(a.prix_depart, a.prix_actuel);
                                    return (
                                        <tr key={a.id} className="border-t border-slate-50">
                                            <td className="px-3 py-2">
                                                <div className="flex items-center gap-2 min-w-0">
                                                    {a.photo_url && <img src={a.photo_url} alt="" className="w-8 h-8 rounded object-cover shrink-0" />}
                                                    <div className="min-w-0">
                                                        <p className="font-semibold text-slate-700 truncate max-w-xs">{a.nom}</p>
                                                        <p className="text-[10px] text-slate-400">
                                                            {a.dressing} · {a.republie ? 'republié' : 'première publication'} · {a.vues} vues
                                                        </p>
                                                    </div>
                                                </div>
                                            </td>
                                            <td className="text-center px-2 py-2 text-slate-600">
                                                J+{a.jours}
                                                <p className="text-[10px] text-slate-400">
                                                    {a.jour_de_controle ? 'contrôle auj.' : `contrôle J+${a.prochain_controle}`}
                                                </p>
                                            </td>
                                            <td className="text-right px-2 py-2 text-slate-500">{euros(a.prix_depart)}</td>
                                            <td className="text-right px-2 py-2 text-slate-700 font-semibold">
                                                {euros(a.prix_actuel)}
                                                {deja !== null && deja < 0 && <p className="text-[10px] text-slate-400">{deja} % depuis le départ</p>}
                                            </td>
                                            <td className="text-center px-2 py-2">
                                                <span className={a.favoris_reels >= (regles?.seuil_favoris_reels ?? 5) ? 'text-emerald-600 font-bold' : 'text-slate-600'}>
                                                    {a.favoris_reels}
                                                </span>
                                                <span className="text-slate-300"> / {regles?.seuil_favoris_reels ?? 5}</span>
                                            </td>
                                            <td className="px-2 py-2">
                                                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border font-bold ${cfg.classes}`}>
                                                    {cfg.icon} {cfg.label}
                                                </span>
                                            </td>
                                            <td className="text-right px-3 py-2">
                                                {a.prix_conseille !== null ? (
                                                    <>
                                                        <span className="font-bold text-red-700">{euros(a.prix_conseille)}</span>
                                                        <p className="text-[10px] text-slate-400">{baisse} % · plancher {euros(a.plancher)}</p>
                                                    </>
                                                ) : (
                                                    <span className="text-slate-300">—</span>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
