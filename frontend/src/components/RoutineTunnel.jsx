import { useState } from 'react';
import { ArrowRight, PartyPopper, LayoutDashboard } from 'lucide-react';

// Tunnel guidé "Routine du jour" : force un parcours séquentiel à travers les
// étapes fournies (etapes[i] = { id, titre, articles, emptyLabel }), en
// réutilisant renderCard(art, segmentId) fourni par Dashboard.jsx (même
// composant ProductCard que le dashboard, pas de duplication). Prend toute la
// zone principale (pas une modale) -- monté conditionnellement par Dashboard.
export default function RoutineTunnel({ etapes, renderCard, onClose }) {
    const [step, setStep] = useState(0);
    const totalEtapes = etapes.length;
    const estTermine = step >= totalEtapes;

    if (estTermine) {
        return (
            <div className="flex flex-col items-center justify-center text-center py-24 px-6 bg-white rounded-3xl border border-brand-50 shadow-soft">
                <PartyPopper className="text-brand-500 mb-4" size={48} />
                <h2 className="text-xl font-black text-slate-800 mb-2">Routine du jour terminée !</h2>
                <p className="text-sm text-slate-500 mb-8 max-w-md">
                    Tu as passé en revue les baisses de prix, les pépites et l'audit du jour. Le stock est à jour, bravo 🎉
                </p>
                <button
                    onClick={onClose}
                    className="flex items-center gap-2 px-6 py-2 bg-brand-600 text-white rounded-lg font-black uppercase text-xs hover:scale-105 transition-all active:scale-95"
                >
                    <LayoutDashboard size={16} /> Retour au dashboard
                </button>
            </div>
        );
    }

    const etape = etapes[step];

    return (
        <div className="bg-white rounded-3xl border border-brand-50 shadow-soft p-6">
            <div className="flex items-center justify-between mb-4">
                <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">
                    Étape {step + 1}/{totalEtapes}
                </span>
                <button
                    onClick={onClose}
                    className="text-[10px] font-black text-slate-400 uppercase tracking-widest hover:text-slate-600"
                >
                    Quitter la routine
                </button>
            </div>

            <div className="w-full h-1.5 bg-brand-50 rounded-full mb-6 overflow-hidden">
                <div
                    className="h-full bg-gradient-to-r from-brand-500 to-brand-600 rounded-full transition-all duration-500"
                    style={{ width: `${((step + 1) / totalEtapes) * 100}%` }}
                />
            </div>

            <h2 className="text-lg font-black text-slate-800 mb-4">{etape.titre} ({etape.articles.length})</h2>

            <div className="space-y-1 mb-6">
                {etape.articles.length > 0
                    ? etape.articles.map((art) => renderCard(art, etape.id))
                    : <p className="text-xs text-slate-400">{etape.emptyLabel}</p>}
            </div>

            <div className="flex justify-end">
                <button
                    onClick={() => setStep((s) => s + 1)}
                    className="flex items-center gap-2 px-6 py-2 bg-gradient-to-r from-brand-500 to-brand-600 text-white rounded-lg font-black uppercase text-xs hover:scale-105 transition-all active:scale-95"
                >
                    Suivant <ArrowRight size={16} />
                </button>
            </div>
        </div>
    );
}
