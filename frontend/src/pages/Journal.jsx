// pages/Journal.jsx
import React, { useState, useEffect, useCallback } from 'react';
import {
    CheckCircle, XCircle, AlertCircle, Clock, Loader, Ban, Play, Moon, Pause, Square,
    Power, ScanLine, TrendingDown, RefreshCw, Heart, ChevronLeft, ChevronRight,
} from 'lucide-react';
import { maintenanceService } from '../services/api';

// Couvre à la fois les statuts VM ("succes"/"echec") et les statuts de tâche
// (global_status : "success"/"failed"/"partial_success"/"running") -- même
// langage visuel que TaskHistory.jsx pour rester cohérent entre les 2 pages.
const STATUS_CONFIG = {
    success: { label: 'Succès', icon: <CheckCircle size={14} className="text-emerald-500" />, bg: 'bg-emerald-50 border-emerald-100', text: 'text-emerald-700' },
    succes: { label: 'Succès', icon: <CheckCircle size={14} className="text-emerald-500" />, bg: 'bg-emerald-50 border-emerald-100', text: 'text-emerald-700' },
    partial_success: { label: 'Succès partiel', icon: <AlertCircle size={14} className="text-amber-500" />, bg: 'bg-amber-50 border-amber-100', text: 'text-amber-700' },
    failed: { label: 'Échec', icon: <XCircle size={14} className="text-red-500" />, bg: 'bg-red-50 border-red-100', text: 'text-red-700' },
    echec: { label: 'Échec', icon: <XCircle size={14} className="text-red-500" />, bg: 'bg-red-50 border-red-100', text: 'text-red-700' },
    running: { label: 'En cours', icon: <Loader size={14} className="animate-spin text-blue-500" />, bg: 'bg-blue-50 border-blue-100', text: 'text-blue-700' },
    inconnu: { label: 'Pas encore passé', icon: <Clock size={14} className="text-slate-300" />, bg: 'bg-slate-50 border-slate-100', text: 'text-slate-400' },
    // Statuts propres à la chaîne de routine par dressing (12/10/2026)
    bloque: { label: 'Bloqué', icon: <Ban size={14} className="text-red-400" />, bg: 'bg-red-50 border-red-100', text: 'text-red-600' },
    interrompue: { label: 'Interrompue', icon: <AlertCircle size={14} className="text-orange-500" />, bg: 'bg-orange-50 border-orange-100', text: 'text-orange-700' },
    arrete: { label: 'Arrêtée', icon: <Square size={14} className="text-slate-500" />, bg: 'bg-slate-50 border-slate-200', text: 'text-slate-600' },
    rien_a_faire: { label: 'Rien à faire', icon: <CheckCircle size={14} className="text-slate-400" />, bg: 'bg-slate-50 border-slate-100', text: 'text-slate-500' },
    repos: { label: 'Repos', icon: <Moon size={14} className="text-slate-400" />, bg: 'bg-slate-50 border-slate-100', text: 'text-slate-500' },
    desactive: { label: 'Désactivé', icon: <Ban size={14} className="text-slate-300" />, bg: 'bg-slate-50 border-slate-100', text: 'text-slate-400' },
};

// Étapes qui attendent "Reprendre la routine" (jamais relancées au simple
// redémarrage du backend, cf. 12/10/2026).
const STATUTS_CHAINE_A_REPRENDRE = ['echec', 'bloque', 'interrompue'];
const STATUTS_AVEC_RAISON = [...STATUTS_CHAINE_A_REPRENDRE, 'arrete'];

// Au-delà, une action en cours sans aucun changement de texte Clemz est
// signalée comme probablement figée.
const SEUIL_FIGE_MINUTES = 5;
const RAFRAICHISSEMENT_MS = 10000;

// Le statut de la chaîne est PAR dressing (une tâche scraping/baisse couvre
// les deux) : il prime, sauf tâche en cours ou succès (le statut de tâche
// distingue alors "succès partiel").
function statutAffiche(etape) {
    const chaine = etape.chaine?.statut;
    if (chaine && chaine !== 'succes' && etape.statut !== 'running') return chaine;
    return etape.statut;
}

const ICONS_ETAPE = [Power, ScanLine, ScanLine, TrendingDown, TrendingDown, RefreshCw, RefreshCw, Heart, Power];

function formatHeure(iso) {
    if (!iso) return null;
    return new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function minutesDepuis(iso) {
    if (!iso) return null;
    return Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
}

function Progression({ progression }) {
    const figeDepuis = minutesDepuis(progression.derniere_avancee);
    const fige = figeDepuis !== null && figeDepuis >= SEUIL_FIGE_MINUTES;
    return (
        <div className="mt-1.5 space-y-0.5">
            <p className="text-[11px] text-blue-700 font-semibold">
                {progression.action}
                {progression.compteur !== null && ` · ${progression.compteur}/${progression.total}`}
                {progression.article && <span className="font-medium text-blue-600"> · {progression.article}</span>}
            </p>
            {progression.texte && (
                <p className="text-[10px] text-slate-500 truncate">{progression.texte}</p>
            )}
            {fige && (
                <p className="text-[10px] text-orange-600 font-semibold">
                    Aucune progression depuis {figeDepuis} min — Clemz est peut-être figé.
                </p>
            )}
            {progression.arret_demande && (
                <p className="text-[10px] text-slate-600 font-semibold">
                    Arrêt demandé — Clemz s'arrêtera avant de supprimer la prochaine annonce.
                </p>
            )}
        </div>
    );
}

function EtapeCard({ etape, index, onArreter, arretEnCours }) {
    const statut = statutAffiche(etape);
    const cfg = STATUS_CONFIG[statut] || STATUS_CONFIG.inconnu;
    const Icon = ICONS_ETAPE[index];
    const heure = formatHeure(etape.heure || etape.chaine?.heure);
    const raisonChaine = STATUTS_AVEC_RAISON.includes(statut) ? etape.chaine?.raison : null;
    // Arrêt possible sur une action Clemz en cours d'un dressing précis
    // (progression live présente) -- pas sur le scraping ni le partage.
    const arretable = onArreter && statut === 'running' && etape.progression && !etape.progression.arret_demande;

    return (
        <div className={`rounded-xl border p-4 flex items-center justify-between gap-4 ${cfg.bg}`}>
            <div className="flex items-center gap-3 min-w-0">
                <Icon size={16} className="text-slate-400 shrink-0" />
                <div className="min-w-0">
                    <p className="text-sm font-bold text-slate-700 flex items-center gap-2">
                        {etape.label}
                        {heure && <span className="text-[10px] font-medium text-slate-400">· {heure}</span>}
                    </p>
                    {etape.compteurs && (
                        <p className="text-[11px] text-slate-500 mt-0.5">
                            {etape.compteurs.succes}/{etape.compteurs.total} réussi(s)
                            {etape.compteurs.ecartes > 0 && (
                                <span className="text-slate-400"> · {etape.compteurs.ecartes} écarté(s)</span>
                            )}
                            {etape.compteurs.bloques_quota > 0 && (
                                <span className="text-amber-600 font-semibold"> · {etape.compteurs.bloques_quota} bloqué(s) quota</span>
                            )}
                            {etape.compteurs.erreurs_clemz > 0 && (
                                <span className="text-red-700 font-semibold"> · {etape.compteurs.erreurs_clemz} erreur(s) Clemz</span>
                            )}
                        </p>
                    )}
                    {etape.prevu && (
                        <p className="text-[10px] text-slate-400 mt-0.5">
                            Prévu : {etape.prevu.niveau === 'calendrier' ? 'calendrier' : etape.prevu.niveau}
                            {etape.prevu.actif && etape.prevu.horaire && ` vers ${etape.prevu.horaire.slice(0, 5)}`}
                            {etape.prevu.actif && ` · cible ${etape.prevu.volume_cible}`}
                            {!etape.prevu.actif && ' · repos'}
                        </p>
                    )}
                    {etape.detail && (
                        <p className="text-[10px] text-slate-500 mt-0.5">{etape.detail}</p>
                    )}
                    {raisonChaine && (
                        <p className={`text-[11px] font-semibold mt-0.5 ${statut === 'arrete' ? 'text-slate-600' : 'text-red-600'}`}>{raisonChaine}</p>
                    )}
                    {statut === 'running' && etape.progression && <Progression progression={etape.progression} />}
                </div>
            </div>
            <div className="flex items-center gap-3 shrink-0">
                {arretable && (
                    <button
                        onClick={() => onArreter(etape.dressing)}
                        disabled={arretEnCours}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-black uppercase bg-white border border-slate-300 text-slate-700 hover:border-red-300 hover:text-red-700 disabled:opacity-50"
                        title="Arrête l'action avant la suppression de la prochaine annonce — les articles déjà traités restent enregistrés"
                    >
                        <Square size={11} />
                        Arrêter
                    </button>
                )}
                <div className={`flex items-center gap-1.5 text-xs font-bold ${cfg.text}`}>
                    {cfg.icon}
                    {cfg.label}
                </div>
            </div>
        </div>
    );
}

function BarrePause({ pause, onChanger, enCours }) {
    if (pause) {
        return (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 mb-2 flex items-center justify-between gap-4">
                <p className="text-xs text-amber-800 font-semibold flex items-center gap-2">
                    <Pause size={14} />
                    Routine en pause {pause.mode === 'jour' ? "jusqu'à demain" : "jusqu'à reprise"} (depuis {formatHeure(pause.depuis)}) — aucune étape ne démarre.
                </p>
                <button
                    onClick={() => onChanger(null)}
                    disabled={enCours}
                    className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-black uppercase bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-50"
                >
                    <Play size={12} />
                    Lever la pause
                </button>
            </div>
        );
    }
    return (
        <div className="flex items-center justify-end gap-2 mb-2">
            <span className="text-[11px] text-slate-400">Mettre la routine en pause :</span>
            {[['jour', "jusqu'à demain"], ['indefini', "jusqu'à reprise"]].map(([mode, label]) => (
                <button
                    key={mode}
                    onClick={() => onChanger(mode)}
                    disabled={enCours}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-black uppercase bg-white border border-slate-200 text-slate-600 hover:border-amber-300 hover:text-amber-700 disabled:opacity-50"
                >
                    <Pause size={11} />
                    {label}
                </button>
            ))}
        </div>
    );
}

// Lecture du journal de la VM depuis le PC principal (09/10/2026) -- les deux
// PC sont sur le même réseau local, le backend de la VM écoute déjà sur
// 0.0.0.0:8000 (run.py) avec CORS ouvert. Choix de source + adresse IP
// mémorisés dans le navigateur (simple confort, la page marche sans).
const CLE_SOURCE = 'journal_source';
const CLE_ADRESSE_VM = 'journal_adresse_vm';

function lireStockage(cle, defaut) {
    try { return localStorage.getItem(cle) ?? defaut; } catch { return defaut; }
}
function ecrireStockage(cle, valeur) {
    try { localStorage.setItem(cle, valeur); } catch { /* stockage indisponible */ }
}

export default function Journal() {
    const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
    const [journal, setJournal] = useState(null);
    const [isLoading, setIsLoading] = useState(true);
    const [source, setSource] = useState(() => lireStockage(CLE_SOURCE, 'local'));
    const [adresseVm, setAdresseVm] = useState(() => lireStockage(CLE_ADRESSE_VM, ''));
    const [adresseVmSaisie, setAdresseVmSaisie] = useState(adresseVm);
    const [actionEnCours, setActionEnCours] = useState(false);
    const [message, setMessage] = useState(null);

    const lectureVm = source === 'vm';
    const vmNonConfiguree = lectureVm && !adresseVm;
    // undefined = backend de ce PC (valeur par défaut des méthodes de l'API)
    const apiUrl = lectureVm ? `http://${adresseVm}:8000/api` : undefined;
    const estAujourdhui = date === new Date().toISOString().slice(0, 10);

    // silencieux : rafraîchissement automatique, sans repasser par "Chargement..."
    const fetchJournal = useCallback(async (silencieux = false) => {
        if (vmNonConfiguree) {
            setJournal(null);
            setIsLoading(false);
            return;
        }
        if (!silencieux) setIsLoading(true);
        try {
            setJournal(await maintenanceService.getJournal(date, apiUrl));
        } catch {
            if (!silencieux) setJournal(null);
        } finally {
            if (!silencieux) setIsLoading(false);
        }
    }, [date, apiUrl, vmNonConfiguree]);

    useEffect(() => { fetchJournal(); }, [fetchJournal]);

    // Journal en direct (12/10/2026) : rafraîchi toutes les 10 s pour la
    // journée en cours -- progression Clemz, arrêts, reprises.
    useEffect(() => {
        if (!estAujourdhui) return undefined;
        const id = setInterval(() => fetchJournal(true), RAFRAICHISSEMENT_MS);
        return () => clearInterval(id);
    }, [estAujourdhui, fetchJournal]);

    const changerSource = (nouvelleSource) => {
        setSource(nouvelleSource);
        ecrireStockage(CLE_SOURCE, nouvelleSource);
    };

    const enregistrerAdresseVm = () => {
        const adresse = adresseVmSaisie.trim().replace(/^https?:\/\//, '').replace(/:\d+.*$/, '').replace(/\/.*$/, '');
        setAdresseVm(adresse);
        setAdresseVmSaisie(adresse);
        ecrireStockage(CLE_ADRESSE_VM, adresse);
    };

    const decalerDate = (jours) => {
        const d = new Date(date);
        d.setDate(d.getDate() + jours);
        setDate(d.toISOString().slice(0, 10));
    };

    const executer = async (action, messageSucces, messageEchec) => {
        setActionEnCours(true);
        setMessage(null);
        try {
            await action();
            setMessage(messageSucces);
            await fetchJournal(true);
        } catch (e) {
            setMessage(`${messageEchec}${e?.message ? ` (${e.message})` : ''}`);
        } finally {
            setActionEnCours(false);
        }
    };

    const reprendreRoutine = () => executer(
        () => maintenanceService.reprendreRoutine(apiUrl),
        "Reprise lancée — les étapes en échec ou interrompues repartent dans l'ordre.",
        "Impossible de lancer la reprise (backend injoignable, ou ce PC n'est pas la VM)",
    );

    const arreterTache = (dressing) => executer(
        () => maintenanceService.arreterTache(dressing, apiUrl),
        `Arrêt demandé pour ${dressing} — Clemz s'arrêtera avant de supprimer la prochaine annonce.`,
        "Impossible de demander l'arrêt",
    );

    const changerPause = (mode) => executer(
        () => maintenanceService.setPauseRoutine(mode, apiUrl),
        mode ? 'Routine en pause — aucune nouvelle étape ne démarrera.' : 'Pause levée — les étapes du jour non commencées repartent dans l\'ordre.',
        "Impossible de changer la pause (backend injoignable, ou ce PC n'est pas la VM)",
    );

    const aReprendre = estAujourdhui && journal?.etapes.some(
        (e) => STATUTS_CHAINE_A_REPRENDRE.includes(e.chaine?.statut)
    );

    return (
        <div className="p-8">
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-2xl font-black text-slate-800">Journal de routine</h1>
                    <p className="text-sm text-slate-400 mt-1">
                        Démarrage VM → scraping → baisses de prix → republications → partage vues/favoris → extinction, dans l'ordre du scénario quotidien.
                    </p>
                </div>

                <div className="flex items-center gap-2 bg-white rounded-xl border border-slate-100 shadow-sm px-3 py-2">
                    <button onClick={() => decalerDate(-1)} className="p-1.5 rounded-lg hover:bg-slate-50 text-slate-500">
                        <ChevronLeft size={16} />
                    </button>
                    <input
                        type="date"
                        value={date}
                        max={new Date().toISOString().slice(0, 10)}
                        onChange={(e) => setDate(e.target.value)}
                        className="text-sm font-semibold text-slate-700 border-none focus:outline-none"
                    />
                    <button onClick={() => decalerDate(1)} disabled={estAujourdhui} className="p-1.5 rounded-lg hover:bg-slate-50 text-slate-500 disabled:opacity-30 disabled:cursor-not-allowed">
                        <ChevronRight size={16} />
                    </button>
                </div>
            </div>

            {/* Source du journal : ce PC ou la VM (réseau local) */}
            <div className="flex flex-wrap items-center gap-3 mb-6 max-w-3xl">
                <div className="flex items-center gap-1 bg-white rounded-xl border border-slate-100 shadow-sm p-1">
                    {[['local', 'Ce PC'], ['vm', 'VM']].map(([valeur, label]) => (
                        <button
                            key={valeur}
                            onClick={() => changerSource(valeur)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-black uppercase tracking-wide transition-all ${source === valeur
                                ? 'bg-slate-800 text-white'
                                : 'text-slate-500 hover:bg-slate-50'
                                }`}
                        >
                            {label}
                        </button>
                    ))}
                </div>
                {lectureVm && (
                    <form
                        onSubmit={(e) => { e.preventDefault(); enregistrerAdresseVm(); }}
                        className="flex items-center gap-2"
                    >
                        <input
                            type="text"
                            value={adresseVmSaisie}
                            onChange={(e) => setAdresseVmSaisie(e.target.value)}
                            placeholder="IP de la VM (ex. 192.168.1.42)"
                            className="text-sm px-3 py-1.5 rounded-lg border border-slate-200 focus:outline-none focus:border-slate-400 w-56"
                        />
                        <button type="submit" className="px-3 py-1.5 rounded-lg text-xs font-black uppercase bg-white border border-slate-200 text-slate-600 hover:border-slate-300">
                            Valider
                        </button>
                        {adresseVm && (
                            <button type="button" onClick={() => fetchJournal()} className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-50" title="Recharger">
                                <RefreshCw size={14} />
                            </button>
                        )}
                    </form>
                )}
            </div>

            {vmNonConfiguree ? (
                <p className="text-sm text-slate-500">Saisis l'adresse IP locale de la VM pour afficher son journal.</p>
            ) : isLoading ? (
                <p className="text-sm text-slate-400">Chargement...</p>
            ) : !journal ? (
                <p className="text-sm text-red-500">
                    {lectureVm
                        ? `Impossible de joindre la VM (${adresseVm}:8000) — vérifie qu'elle est allumée, que le backend tourne, et que le pare-feu Windows de la VM autorise le port 8000 en réseau privé.`
                        : 'Impossible de charger le journal de cette journée.'}
                </p>
            ) : (
                <div className="space-y-2 max-w-3xl">
                    {estAujourdhui && (
                        <BarrePause pause={journal.pause} onChanger={changerPause} enCours={actionEnCours} />
                    )}
                    {aReprendre && (
                        <div className="rounded-xl border border-red-100 bg-white p-4 mb-2 flex items-center justify-between gap-4">
                            <p className="text-xs text-slate-600">
                                Une étape a échoué ou a été interrompue : la suite de la routine de ce dressing attend. Corrige la cause (ex. session Vinted), puis reprends.
                            </p>
                            <button
                                onClick={reprendreRoutine}
                                disabled={actionEnCours}
                                className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-black uppercase bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-50"
                            >
                                <Play size={12} />
                                Reprendre la routine
                            </button>
                        </div>
                    )}
                    {message && (
                        <p className="text-xs text-slate-500 mb-2">{message}</p>
                    )}
                    {journal.etapes.map((etape, i) => (
                        <EtapeCard
                            key={i}
                            etape={etape}
                            index={i}
                            onArreter={estAujourdhui ? arreterTache : null}
                            arretEnCours={actionEnCours}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
