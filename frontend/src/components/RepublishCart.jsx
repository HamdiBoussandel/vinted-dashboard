// components/RepublishCart.jsx
import React from 'react';
import { RefreshCw, Clock, CheckCircle, Loader } from 'lucide-react';

export default function RepublishCart({
    selectedItems,
    isScheduling,
    scheduledTask,
    onSchedule,
    onClear
}) {
    // Rien à afficher si panier vide ET pas de tâche programmée aujourd'hui
    if (selectedItems.length === 0 && !scheduledTask) return null;

    // Statut d'une tâche déjà programmée
    const statusConfig = {
        pending: {
            label: "Programmée",
            color: "text-emerald-600",
            bg: "bg-emerald-50 border-emerald-100",
            icon: <Clock size={12} className="text-emerald-500" />,
        },
        running: {
            label: "En cours...",
            color: "text-blue-600",
            bg: "bg-blue-50 border-blue-100",
            icon: <Loader size={12} className="animate-spin text-blue-500" />,
        },
        done: {
            label: "Terminée",
            color: "text-slate-500",
            bg: "bg-slate-50 border-slate-100",
            icon: <CheckCircle size={12} className="text-slate-400" />,
        },
        failed: {
            label: "Échec",
            color: "text-red-600",
            bg: "bg-red-50 border-red-100",
            icon: <CheckCircle size={12} className="text-red-400" />,
        },
    };

    const currentStatus = scheduledTask
        ? statusConfig[scheduledTask.status] || statusConfig.pending
        : null;

    return (
        <div className="fixed bottom-8 left-8 z-50 flex flex-col items-start gap-3">

            {/* Statut tâche déjà programmée aujourd'hui */}
            {scheduledTask && (
                <div className={`flex items-center gap-2 px-4 py-2 rounded-lg shadow-xl border text-[10px] font-black uppercase ${currentStatus.bg} ${currentStatus.color}`}>
                    {currentStatus.icon}
                    <span>
                        {scheduledTask.status === 'pending' && scheduledTask.scheduled_run_time
                            ? `Republication à ${scheduledTask.scheduled_run_time}`
                            : scheduledTask.status === 'pending'
                            ? `${scheduledTask.items?.length} article(s) programmé(s) ce soir`
                            : scheduledTask.status === 'running'
                            ? `Republication en cours (${scheduledTask.items?.length} articles)`
                            : scheduledTask.status === 'done'
                            ? `Republication terminée aujourd'hui`
                            : `Republication échouée — vérifier l'historique`
                        }
                    </span>
                </div>
            )}

            {/* Panier en cours de constitution */}
            {selectedItems.length > 0 && (
                <>
                    {/* Badge */}
                    <div className="bg-white px-4 py-2 rounded-lg shadow-xl border border-slate-100 text-[10px] font-black uppercase text-slate-500">
                        {selectedItems.length} {selectedItems.length > 1 ? 'articles' : 'article'} à republier
                    </div>

                    {/* Bouton principal */}
                    <button
                        onClick={onSchedule}
                        disabled={isScheduling || scheduledTask?.status === 'running'}
                        className="flex items-center gap-3 px-8 py-4 bg-emerald-500 text-white rounded-2xl font-black uppercase text-xs hover:scale-105 transition-all shadow-2xl shadow-emerald-200 disabled:opacity-50 active:scale-95"
                    >
                        {isScheduling ? (
                            <div className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                        ) : (
                            <RefreshCw size={18} />
                        )}
                        {isScheduling
                            ? "Programmation..."
                            : `Programmer ce soir (${selectedItems.length})`
                        }
                    </button>

                    {/* Vider la sélection */}
                    <button
                        onClick={onClear}
                        disabled={isScheduling}
                        className="bg-white px-4 py-2 rounded-lg shadow-xl border border-slate-100 text-[10px] font-black uppercase text-red-500 hover:text-red-700 disabled:opacity-40"
                    >
                        Vider la sélection
                    </button>
                </>
            )}
        </div>
    );
}