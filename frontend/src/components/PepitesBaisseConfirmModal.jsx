import React from 'react';
import { X, Trash2, TrendingDown } from 'lucide-react';

export default function PepitesBaisseConfirmModal({
    items,
    onRemove,
    onConfirm,
    onCancel,
    isSubmitting
}) {
    return (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">

                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
                    <h2 className="text-lg font-black text-slate-800">
                        Confirmer la baisse de prix pépites ({items.length})
                    </h2>
                    <button onClick={onCancel} className="text-slate-400 hover:text-slate-600">
                        <X size={22} />
                    </button>
                </div>

                {/* Liste des articles -- un taux propre par article (tranche de
                    prix pépite), pas un taux unique pour tout le lot. */}
                <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
                    {items.length === 0 ? (
                        <p className="text-center text-slate-400 py-8">
                            Aucun article restant — tout a été exclu.
                        </p>
                    ) : (
                        items.map(a => {
                            const taux = a.pepite_taux_suggere;
                            const prixCible = taux != null ? (a.prix_vente * (1 - taux / 100)).toFixed(2) : null;
                            return (
                                <div key={a.id} className="flex items-center gap-3 p-3 bg-slate-50 border border-slate-100 rounded-xl">
                                    {a.photo_url && (
                                        <img src={a.photo_url} alt={a.nom} className="w-10 h-10 object-cover rounded-lg bg-slate-200" />
                                    )}
                                    <div className="flex-1 min-w-0">
                                        <p className="font-bold text-sm text-slate-800 truncate">{a.nom}</p>
                                        <p className="text-xs text-slate-400">
                                            {a.dressing} · {a.prix_vente}€
                                        </p>
                                    </div>
                                    {taux != null && (
                                        <span className="flex items-center gap-1 text-xs font-black text-cyan-700 bg-cyan-50 border border-cyan-200 px-2 py-1 rounded-md shrink-0">
                                            <TrendingDown size={12} /> -{taux}% ({prixCible}€)
                                        </span>
                                    )}
                                    <button
                                        onClick={() => onRemove(a.id)}
                                        title="Exclure cet article de ce lancement"
                                        className="p-2 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                                    >
                                        <Trash2 size={16} />
                                    </button>
                                </div>
                            );
                        })
                    )}
                </div>

                {/* Footer */}
                <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-100">
                    <button
                        onClick={onCancel}
                        disabled={isSubmitting}
                        className="px-5 py-2 text-sm font-bold text-slate-500 hover:bg-slate-100 rounded-lg transition-all disabled:opacity-40"
                    >
                        Annuler
                    </button>
                    <button
                        onClick={onConfirm}
                        disabled={isSubmitting || items.length === 0}
                        className="flex items-center gap-2 px-6 py-2 bg-cyan-600 text-white rounded-lg text-sm font-black uppercase disabled:opacity-40 hover:scale-105 transition-all"
                    >
                        {isSubmitting ? (
                            <div className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                        ) : (
                            <TrendingDown size={16} />
                        )}
                        Confirmer ({items.length})
                    </button>
                </div>
            </div>
        </div>
    );
}
