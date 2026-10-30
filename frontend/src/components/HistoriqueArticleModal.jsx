import React, { useEffect, useMemo, useState } from 'react';
import {
    Banknote, Circle, Compass, Loader, OctagonX, Package, Pencil, Recycle, SkipForward, Sparkles, Trash2, TrendingDown, Undo2, X, XCircle,
} from 'lucide-react';
import { AvecIcone, TexteIcone } from './Icones';

const API_URL = 'http://localhost:8000/api';

// Journal par article (28/10/2026, inspiré du "Journal" de Bleam) : lit la
// table article_evenements via GET /api/articles/{id}/journal.
const TYPES = {
    publication: { Icone: Sparkles, libelle: 'Publication', couleur: 'bg-blue-50 text-blue-700 border-blue-200' },
    baisse: { Icone: TrendingDown, libelle: 'Baisse de prix', couleur: 'bg-rose-50 text-rose-700 border-rose-200' },
    republication: { Icone: Recycle, libelle: 'Republication', couleur: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    suppression: { Icone: Trash2, libelle: 'Suppression', couleur: 'bg-slate-100 text-slate-700 border-slate-200' },
    vente: { Icone: Banknote, libelle: 'Vente', couleur: 'bg-amber-50 text-amber-700 border-amber-200' },
    erreur_clemz: { Icone: OctagonX, libelle: 'Erreur Clemz', couleur: 'bg-red-100 text-red-700 border-red-300' },
    echec: { Icone: XCircle, libelle: 'Échec', couleur: 'bg-red-50 text-red-600 border-red-200' },
    ecarte: { Icone: SkipForward, libelle: 'Écarté', couleur: 'bg-slate-50 text-slate-500 border-slate-200' },
    prix_change: { Icone: Pencil, libelle: 'Prix changé à la main', couleur: 'bg-violet-50 text-violet-700 border-violet-200' },
    reprise_strategie: { Icone: Undo2, libelle: 'Remis sous stratégie', couleur: 'bg-teal-50 text-teal-700 border-teal-200' },
    strategie: { Icone: Compass, libelle: 'Changement de stratégie', couleur: 'bg-sky-50 text-sky-700 border-sky-200' },
};

const ORIGINES = {
    prix_boucher: 'Prix boucher',
    mauvaise_performance: 'Mauvaise performance',
    prix_boucher_et_mauvaise_performance: 'Prix boucher / mauvaise perf',
    panier_manuel: 'Panier manuel',
    republication: 'À la republication',
    validation_manuelle: 'Validation manuelle',
    republication_assistee: 'Republication assistée',
    ancien_script: 'Ancien script Clemz',
    routine: 'Routine',
    liquidation: 'Liquidation',
    smart_dressing: 'Smart Dressing',
    inconnue: 'Origine inconnue',
    manuel: 'Hors VintedPro',
    standard: 'Standard',
    destockage: 'Déstockage',
};

const STRATEGIES = [
    {
        id: 'standard', libelle: 'Standard',
        description: "Parcours par défaut : prix boucher les 15 premiers jours, republication tous les 7 jours, fin de vie après 3 republications sans vente.",
    },
    {
        id: 'destockage', libelle: <AvecIcone Icone={Package}>Déstockage</AvecIcone>,
        description: "Pour qu'il parte vite : republication tous les 7 jours avec −10 % à chaque fois, jusqu'au prix d'achat (ou 50 % du prix de départ, 3 € minimum), puis sortie après 3 republications au plancher.",
    },
];

function dateHeure(iso) {
    const d = new Date(iso);
    return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function euros(x) {
    return x == null ? '—' : `${Number(x).toFixed(2).replace('.', ',')} €`;
}

export default function HistoriqueArticleModal({ article, onClose }) {
    const [donnees, setDonnees] = useState(null);
    const [erreur, setErreur] = useState(null);
    const [afficherEcartes, setAfficherEcartes] = useState(false);
    const [prixManuel, setPrixManuel] = useState(article.prix_manuel);
    const [reprise, setReprise] = useState({ enCours: false, erreur: null });
    const [version, setVersion] = useState(0);
    const [strategie, setStrategie] = useState(article.strategie || 'standard');
    const [choix, setChoix] = useState({ enCours: false, erreur: null });

    // Choix de stratégie (28/10/2026, inspiré de Bleam) : Standard / Déstockage
    const changerStrategie = async (nouvelle) => {
        setChoix({ enCours: true, erreur: null });
        try {
            const r = await fetch(`${API_URL}/articles/strategie`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: [article.id], strategie: nouvelle }),
            });
            if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Erreur serveur');
            setStrategie(nouvelle);
            setVersion((v) => v + 1);
            setChoix({ enCours: false, erreur: null });
        } catch (e) {
            setChoix({ enCours: false, erreur: e.message });
        }
    };

    // "Remettre sous stratégie" (prix changé à la main, 28/10/2026)
    const remettreSousStrategie = async () => {
        setReprise({ enCours: true, erreur: null });
        try {
            const r = await fetch(`${API_URL}/articles/${article.id}/reprendre-strategie`, { method: 'POST' });
            if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Erreur serveur');
            setPrixManuel(false);
            setVersion((v) => v + 1);
            setReprise({ enCours: false, erreur: null });
        } catch (e) {
            setReprise({ enCours: false, erreur: e.message });
        }
    };

    useEffect(() => {
        fetch(`${API_URL}/articles/${article.id}/journal`)
            .then(async (r) => {
                if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Erreur serveur');
                return r.json();
            })
            .then(setDonnees)
            .catch((e) => setErreur(e.message));
    }, [article.id, version]);

    const evenements = useMemo(
        () => (donnees?.evenements || []).filter((e) => afficherEcartes || e.type !== 'ecarte'),
        [donnees, afficherEcartes],
    );
    const nbEcartes = (donnees?.evenements || []).filter((e) => e.type === 'ecarte').length;

    // Évolution du prix d'après les relevés de score : un point à chaque changement
    const prixReleves = useMemo(() => {
        const points = [];
        for (const r of donnees?.releves || []) {
            if (r.prix != null && (!points.length || points[points.length - 1].prix !== r.prix)) {
                points.push({ quand: r.captured_at, prix: r.prix });
            }
        }
        return points;
    }, [donnees]);
    const dernierReleve = donnees?.releves?.[donnees.releves.length - 1];

    return (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6" onClick={onClose}>
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-slate-100">
                    <div>
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Historique · {article.dressing}</p>
                        <h2 className="text-base font-black text-slate-800">{article.nom}</h2>
                        <p className="text-xs text-slate-500 mt-0.5">Prix actuel {euros(article.prix_vente)}</p>
                    </div>
                    <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={22} /></button>
                </div>

                <div className="flex-1 overflow-y-auto px-6 py-4">
                    <div className="mb-4 p-3 rounded-xl bg-slate-50 border border-slate-100">
                        <div className="flex items-center justify-between gap-3">
                            <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Stratégie</p>
                            <div className="flex gap-1">
                                {STRATEGIES.map((s) => (
                                    <button
                                        key={s.id}
                                        onClick={() => changerStrategie(s.id)}
                                        disabled={choix.enCours || strategie === s.id}
                                        className={`px-3 py-1.5 rounded-lg text-[11px] font-black transition-all ${strategie === s.id
                                            ? 'bg-slate-800 text-white'
                                            : 'bg-white text-slate-500 border border-slate-200 hover:bg-slate-100 disabled:opacity-40'}`}
                                    >
                                        {s.libelle}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <p className="text-[11px] text-slate-500 mt-1.5">{STRATEGIES.find((s) => s.id === strategie)?.description}</p>
                        {choix.erreur && <p className="text-[11px] font-semibold text-red-600 mt-1">{choix.erreur}</p>}
                    </div>
                    {prixManuel && (
                        <div className="mb-4 p-3 rounded-xl bg-violet-50 border border-violet-200 flex items-center justify-between gap-3">
                            <div>
                                <p className="text-xs font-black text-violet-800"><AvecIcone Icone={Pencil}>Prix changé à la main</AvecIcone></p>
                                <p className="text-[11px] text-violet-700">
                                    {article.prix_manuel_detail || 'Baisses automatiques suspendues pour cet article.'}
                                </p>
                                {reprise.erreur && <p className="text-[11px] font-semibold text-red-600 mt-1">{reprise.erreur}</p>}
                            </div>
                            <button
                                onClick={remettreSousStrategie}
                                disabled={reprise.enCours}
                                className="shrink-0 px-3 py-2 rounded-lg bg-violet-600 text-white text-[11px] font-black uppercase disabled:opacity-40 hover:bg-violet-700"
                            >
                                {reprise.enCours ? '…' : 'Remettre sous stratégie'}
                            </button>
                        </div>
                    )}
                    {erreur && <p className="text-sm font-semibold text-red-600">{erreur}</p>}
                    {!donnees && !erreur && (
                        <p className="flex items-center gap-2 text-sm text-slate-400"><Loader size={14} className="animate-spin" /> Chargement…</p>
                    )}

                    {donnees && (
                        <>
                            {prixReleves.length > 0 && (
                                <div className="mb-4 p-3 rounded-xl bg-slate-50 border border-slate-100">
                                    <p className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-1">Prix relevés par le scraping</p>
                                    <p className="text-sm font-bold text-slate-700">
                                        {prixReleves.map((p) => euros(p.prix)).join('  →  ')}
                                    </p>
                                    {dernierReleve && (
                                        <p className="text-[11px] text-slate-400 mt-1">
                                            Dernier relevé le {dateHeure(dernierReleve.captured_at)} : {dernierReleve.vues} vues, {dernierReleve.favoris} favoris réels
                                        </p>
                                    )}
                                </div>
                            )}

                            <div className="flex items-center justify-between mb-2">
                                <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                                    {evenements.length} événement{evenements.length > 1 ? 's' : ''}
                                </p>
                                {nbEcartes > 0 && (
                                    <label className="flex items-center gap-1.5 text-[11px] text-slate-500 cursor-pointer">
                                        <input type="checkbox" checked={afficherEcartes} onChange={(e) => setAfficherEcartes(e.target.checked)} />
                                        Afficher les {nbEcartes} passage{nbEcartes > 1 ? 's' : ''} où l'article a été écarté
                                    </label>
                                )}
                            </div>

                            {evenements.length === 0 ? (
                                <p className="text-sm text-slate-400 py-6 text-center">Aucun événement enregistré pour cet article.</p>
                            ) : (
                                <ol className="relative border-l-2 border-slate-100 ml-2 space-y-3">
                                    {evenements.map((e) => {
                                        const t = TYPES[e.type] || { Icone: Circle, libelle: e.type, couleur: 'bg-slate-50 text-slate-600 border-slate-200' };
                                        return (
                                            <li key={e.id} className="ml-4">
                                                <span className="absolute -left-[9px] mt-1.5 h-4 w-4 rounded-full bg-white border-2 border-slate-200" />
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="text-[11px] font-mono text-slate-400">{dateHeure(e.survenu_le)}</span>
                                                    <span className={`px-2 py-0.5 rounded-md border text-[10px] font-black uppercase tracking-wide ${t.couleur}`}>
                                                        <AvecIcone Icone={t.Icone}>{t.libelle}</AvecIcone>
                                                    </span>
                                                    {e.origine && (
                                                        <span className="text-[10px] font-bold text-slate-500 bg-slate-100 px-1.5 rounded">
                                                            {ORIGINES[e.origine] || e.origine}
                                                        </span>
                                                    )}
                                                </div>
                                                {e.detail && <p className="text-xs text-slate-600 mt-0.5"><TexteIcone texte={e.detail} /></p>}
                                            </li>
                                        );
                                    })}
                                </ol>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
