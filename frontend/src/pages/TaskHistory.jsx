// pages/TaskHistory.jsx
import React, { useState, useEffect } from 'react';
import { CheckCircle, XCircle, AlertCircle, Clock, RefreshCw, TrendingDown, Loader, ClipboardList, ScanLine, FileText, Flame } from 'lucide-react';

const STATUS_CONFIG = {
    success: {
        label: "Succès",
        icon: <CheckCircle size={14} className="text-emerald-500" />,
        bg: "bg-emerald-50 border-emerald-100",
        text: "text-emerald-700",
        dot: "bg-emerald-500",
    },
    partial_success: {
        label: "Succès partiel",
        icon: <AlertCircle size={14} className="text-amber-500" />,
        bg: "bg-amber-50 border-amber-100",
        text: "text-amber-700",
        dot: "bg-amber-500",
    },
    failed: {
        label: "Échec",
        icon: <XCircle size={14} className="text-red-500" />,
        bg: "bg-red-50 border-red-100",
        text: "text-red-700",
        dot: "bg-red-500",
    },
    running: {
        label: "En cours",
        icon: <Loader size={14} className="animate-spin text-blue-500" />,
        bg: "bg-blue-50 border-blue-100",
        text: "text-blue-700",
        dot: "bg-blue-500",
    },
    skipped: {
        label: "Déjà fait",
        icon: <AlertCircle size={14} className="text-slate-400" />,
        bg: "bg-slate-50 border-slate-100",
        text: "text-slate-500",
        dot: "bg-slate-300",
    },

    stale: {
        label: "Bloqué ?",
        icon: <AlertCircle size={14} className="text-slate-500" />,
        bg: "bg-slate-100 border-slate-200",
        text: "text-slate-600",
        dot: "bg-slate-400",
    },
};

const TYPE_CONFIG = {
    republication: {
        label: "Republication",
        icon: <RefreshCw size={12} />,
        color: "bg-emerald-100 text-emerald-700 border-emerald-200",
    },
    baisse_prix: {
        label: "Baisse de prix",
        icon: <TrendingDown size={12} />,
        color: "bg-orange-100 text-orange-700 border-orange-200",
    },
    scraping: {
        label: "Scraping",
        icon: <ScanLine size={12} />,
        color: "bg-blue-100 text-blue-700 border-blue-200",
    },
    creation_brouillon: {
        label: "Création brouillon",
        icon: <FileText size={12} />,
        color: "bg-violet-100 text-violet-700 border-violet-200",
    },
    // Ajouté le 03/10/2026 -- sans cette entrée, une tâche de liquidation
    // (task_history "type": "liquidation", cf. _run_liquidation_task) tombait
    // sur le badge générique gris (fallback ci-dessous, "type = task.type" tel
    // quel), indistinct des vraies baisses de prix classiques malgré un
    // mécanisme différent (regroupement par prix fixe, jamais compté dans le
    // quota anti-détection).
    liquidation: {
        label: "Liquidation",
        icon: <Flame size={12} />,
        color: "bg-red-100 text-red-700 border-red-200",
    },
};

function formatDate(isoString) {
    if (!isoString) return "—";
    const d = new Date(isoString);
    return d.toLocaleDateString('fr-FR', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit'
    });
}

function formatDuration(start, end) {
    if (!start || !end) return null;
    const diff = Math.round((new Date(end) - new Date(start)) / 1000);
    if (diff < 60) return `${diff}s`;
    return `${Math.floor(diff / 60)}m ${diff % 60}s`;
}

function getDressings(task) {
    if (!task.results?.length) return [];
    const set = new Set();
    task.results.forEach(r => {
        if (r.dressing) {
            set.add(r.dressing);
        } else if (r.account) {
            // "Chrome - Dressing 1" -> "Dressing 1", "Edge - Dressing 2" -> "Dressing 2"
            const match = r.account.match(/Dressing\s*\d+/i);
            set.add(match ? match[0] : r.account);
        }
    });
    return Array.from(set);
}

function TaskResultRow({ result }) {
    const isSuccess = result.status === 'success';
    const dressingLabel = result.dressing || result.account || null;
    return (
        <div className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs border ${isSuccess
            ? 'bg-white border-slate-100 text-slate-600'
            : 'bg-red-50 border-red-100 text-red-700'
            }`}>
            <div className="flex items-center gap-2 min-w-0">
                {isSuccess
                    ? <CheckCircle size={12} className="text-emerald-500 shrink-0" />
                    : <XCircle size={12} className="text-red-500 shrink-0" />
                }
                <span className="font-medium line-clamp-1">{result.nom || result.id}</span>
                {dressingLabel && (
                    <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0">
                        {dressingLabel}
                    </span>
                )}
                {/* Sous-groupe de baisse de prix (-10%/-20%), absent pour les autres types de tâche */}
                {typeof result.taux === 'number' && (
                    <span className="text-[9px] font-black px-1.5 py-0.5 rounded bg-orange-100 text-orange-700 shrink-0">
                        -{result.taux}%
                    </span>
                )}
            </div>
            {result.reason && (
                // Pas de shrink-0 + troncature (cf. échange du 24/09/2026) : un motif
                // détaillé (plusieurs phrases) débordait de la ligne et chevauchait
                // visuellement les lignes voisines, faute de largeur plafonnée --
                // "title" garde le texte complet accessible au survol.
                <span
                    title={result.reason}
                    className={`text-[10px] font-medium ml-4 max-w-[45%] truncate ${isSuccess ? 'text-slate-400' : 'text-red-500'}`}
                >
                    {result.reason}
                </span>
            )}
        </div>
    );
}

function isPartageTask(task) {
    return task.type === 'partage_vues_favoris';
}

function getPartageCounts(task) {
    const okStatuses = new Set(['success', 'skipped']);
    const total = task.results?.length ?? 0;
    const success = task.results?.filter(r =>
        okStatuses.has(r.vues?.status) && okStatuses.has(r.favoris?.status)
    ).length ?? 0;
    return { success, total };
}

function SubStatusChip({ label, sub }) {
    const cfg = STATUS_CONFIG[sub?.status] || STATUS_CONFIG.failed;
    return (
        <div className="flex items-center justify-between gap-2 px-2 py-1 rounded bg-white/60 border border-slate-100">
            <div className="flex items-center gap-1.5 text-[11px] font-bold text-slate-600">
                {cfg.icon}
                {label}
                <span className={cfg.text}>{cfg.label}</span>
            </div>
            {sub?.reason && (
                <span className="text-[10px] text-red-500 font-medium ml-2">{sub.reason}</span>
            )}
        </div>
    );
}

function PartageResultRow({ result }) {
    return (
        <div className="px-3 py-2 rounded-lg text-xs border bg-white border-slate-100 text-slate-600">
            <div className="font-bold mb-1.5">👗 {result.account}</div>
            <div className="flex flex-col gap-1">
                <SubStatusChip label="👀 Vues" sub={result.vues} />
                <SubStatusChip label="💞 Favoris" sub={result.favoris} />
            </div>
        </div>
    );
}

function TaskCard({ task }) {
    const [expanded, setExpanded] = useState(false);
    const stale = isStaleRunning(task);
    const status = STATUS_CONFIG[stale ? 'stale' : task.global_status] || STATUS_CONFIG.failed; const type = TYPE_CONFIG[task.type] || { label: task.type, icon: null, color: "bg-slate-100 text-slate-600 border-slate-200" };
    const duration = formatDuration(task.started_at, task.finished_at);
    // Exclut les lignes d'anomalie globale (add_task_global_anomaly, id
    // "anomaly_HHMMSS") du comptage -- elles réutilisent le même tableau
    // results que les vrais articles pour l'affichage, mais ne représentent
    // pas un article traité : les compter gonflait le total à tort (ex: "5/7"
    // pour une demande réelle de 6 articles, cf. échange du 13/09/2026).
    const articleResults = task.results?.filter(r => !String(r.id).startsWith('anomaly_')) ?? [];
    const counts = isPartageTask(task)
        ? getPartageCounts(task)
        : {
            success: articleResults.filter(r => r.status === 'success').length,
            total: articleResults.length,
        };
    const successCount = counts.success;
    const totalCount = counts.total;
    const dressings = getDressings(task);
    const unitLabel = task.type === 'scraping'
        ? `dressing${totalCount > 1 ? 's' : ''}`
        : `article${totalCount > 1 ? 's' : ''}`;

    return (
        <div className={`rounded-2xl border p-5 shadow-sm transition-all ${status.bg}`}>
            {/* En-tête de la tâche */}
            <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                    {/* Indicateur de statut */}
                    <div className={`h-2.5 w-2.5 rounded-full shrink-0 ${status.dot} ${task.global_status === 'running' && !stale ? 'animate-pulse' : ''
                        }`} />

                    {/* Type de tâche */}
                    <span className={`flex items-center gap-1 text-[10px] font-black uppercase px-2 py-1 rounded-md border ${type.color}`}>
                        {type.icon}
                        {type.label}
                    </span>

                    {/* Badge(s) dressing(s) concerné(s) */}
                    {dressings.map(d => (
                        <span
                            key={d}
                            className="flex items-center gap-1 text-[10px] font-black uppercase px-2 py-1 rounded-md border bg-slate-100 text-slate-600 border-slate-200"
                        >
                            👗 {d}
                        </span>
                    ))}

                    {/* Badge créneau Midi/Soir -- uniquement les republications du cron
                        programmé (task.type = "republication_midi"/"republication_soir",
                        cf. automation_service.py). Ne pas se fier à un simple "_" dans
                        task.type : "baisse_prix" en contient un aussi mais n'a aucun
                        rapport avec un créneau horaire (cf. échange du 13/09/2026). */}
                    {(task.type?.endsWith("_midi") || task.type?.endsWith("_soir")) && (
                        <span className={`flex items-center gap-1 text-[10px] font-black uppercase px-2 py-1 rounded-md border ${task.type?.includes("midi")
                            ? "bg-yellow-100 text-yellow-700 border-yellow-200"
                            : "bg-indigo-100 text-indigo-700 border-indigo-200"
                            }`}>
                            {task.type?.includes("midi") ? "🌞 Midi" : "🌙 Soir"}
                        </span>
                    )}

                    {/* Statut global */}
                    <div className={`flex items-center gap-1.5 text-xs font-bold ${status.text}`}>
                        {status.icon}
                        {status.label}
                    </div>

                    {/* Compteur articles */}
                    <span className="text-[11px] text-slate-500 font-medium">
                        {successCount}/{totalCount} {unitLabel}
                    </span>
                </div>

                <div className="flex items-center gap-4 text-right">
                    {/* Date et durée */}
                    <div>
                        <p className="text-[10px] text-slate-400 font-medium">
                            <Clock size={10} className="inline mr-1" />
                            {formatDate(task.started_at)}
                        </p>
                        {duration && (
                            <p className="text-[10px] text-slate-400">Durée : {duration}</p>
                        )}
                    </div>

                    {/* Bouton expand */}
                    {totalCount > 0 && (
                        <button
                            onClick={() => setExpanded(e => !e)}
                            className="text-[10px] font-black uppercase text-slate-400 hover:text-slate-600 transition-all px-3 py-1.5 rounded-lg hover:bg-white border border-transparent hover:border-slate-200"
                        >
                            {expanded ? 'Masquer' : 'Détail'}
                        </button>
                    )}
                </div>
            </div>

            {/* Détail par article (expandable) */}
            {expanded && task.results?.length > 0 && (
                <div className="mt-4 flex flex-col gap-1.5">
                    {task.results.map((result, i) => (
                        isPartageTask(task)
                            ? <PartageResultRow key={i} result={result} />
                            : <TaskResultRow key={i} result={result} />
                    ))}
                </div>
            )}
        </div>
    );
}

function isStaleRunning(task) {
    if (task.global_status !== 'running') return false;
    const ageMinutes = (Date.now() - new Date(task.started_at).getTime()) / 60000;
    return ageMinutes > 180; // au-delà de 3h, considéré comme probablement bloqué
}

export default function TaskHistory() {
    const [history, setHistory] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [filter, setFilter] = useState('all'); // all | republication | baisse_prix

    const fetchHistory = async () => {
        setIsLoading(true);
        try {
            const params = filter !== 'all' ? `?task_type=${filter}&limit=100` : '?limit=100';
            const res = await fetch(`http://localhost:8000/api/automation/history${params}`);
            const data = await res.json();
            setHistory(data.history || []);
        } catch (err) {
            console.error("Erreur chargement historique :", err);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => { fetchHistory(); }, [filter]);

    const filters = [
        { id: 'all', label: 'Toutes les tâches' },
        { id: 'republication', label: 'Republications' },
        { id: 'baisse_prix', label: 'Baisses de prix' },
        { id: 'liquidation', label: 'Liquidation' },
        { id: 'scraping', label: 'Scraping' },
    ];

    return (
        <div className="p-10 max-w-4xl mx-auto">
            {/* En-tête */}
            <div className="mb-8">
                <h1 className="text-2xl font-black tracking-tight">
                    Historique des automatisations
                </h1>
                <p className="text-sm text-slate-400 mt-1">
                    Suivi des tâches de republication et de baisse de prix exécutées par Clemz.
                </p>
            </div>

            {/* Filtres */}
            <div className="flex gap-2 mb-6">
                {filters.map(f => (
                    <button
                        key={f.id}
                        onClick={() => setFilter(f.id)}
                        className={`px-4 py-2 rounded-lg text-xs font-black uppercase transition-all ${filter === f.id
                            ? 'bg-slate-800 text-white shadow-md'
                            : 'bg-white text-slate-500 border border-slate-200 hover:border-slate-300'
                            }`}
                    >
                        {f.label}
                    </button>
                ))}

                {/* Bouton rafraîchir */}
                <button
                    onClick={fetchHistory}
                    className="ml-auto flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase bg-white text-slate-500 border border-slate-200 hover:border-slate-300 transition-all"
                >
                    <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
                    Actualiser
                </button>
            </div>

            {/* Contenu */}
            {isLoading ? (
                <div className="flex items-center justify-center py-20 text-slate-400">
                    <Loader size={24} className="animate-spin mr-3" />
                    Chargement de l'historique...
                </div>
            ) : history.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-slate-400">
                    <ClipboardList size={40} className="mb-4 opacity-30" />
                    <p className="text-sm font-medium">Aucune tâche dans l'historique.</p>
                    <p className="text-xs mt-1">Les tâches apparaîtront ici après la première automatisation.</p>
                </div>
            ) : (
                <div className="flex flex-col gap-3">
                    {history.map(task => (
                        <TaskCard key={task.task_id} task={task} />
                    ))}
                </div>
            )}
        </div>
    );
}