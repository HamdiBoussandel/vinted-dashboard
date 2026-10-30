import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Cookie, RefreshCw, Loader } from 'lucide-react';

const API_URL = "http://localhost:8000/api";

// Seuil de la tâche d'entretien (SEUIL_RENOUVELLEMENT_JOURS, entretien_sessions.py) :
// en dessous, la session est renouvelée au prochain passage quotidien (13h-15h).
const SEUIL_JOURS = 5;

function couleur(jours) {
  if (jours == null || jours < 1) return 'text-red-600 bg-red-50';
  if (jours < SEUIL_JOURS) return 'text-amber-600 bg-amber-50';
  return 'text-emerald-600 bg-emerald-50';
}

function libelle(session) {
  if (!session.expire_le) return 'Aucune session';
  if (session.jours_restants <= 0) return 'Expirée';
  const date = new Date(session.expire_le).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  return `${session.jours_restants} j — jusqu'au ${date}`;
}

// Expiration des sessions Vinted (cookie refresh_token_web, 7 jours glissants)
// des 4 profils automatisés + renouvellement manuel (26/10/2026).
export default function SessionsVintedCard() {
  const [sessions, setSessions] = useState(null);
  const [error, setError] = useState(null);
  const [enCours, setEnCours] = useState(false);
  const suivi = useRef(null);

  const charger = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/maintenance/sessions`);
      if (!res.ok) throw new Error('Erreur serveur');
      setSessions(await res.json());
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    charger();
    return () => clearInterval(suivi.current);
  }, [charger]);

  const renouveler = async (forcer) => {
    setEnCours(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/maintenance/entretien-sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ forcer }),
      });
      if (!res.ok) throw new Error('Erreur serveur');
      // Le renouvellement tourne en arrière-plan (~30 s par profil) : on
      // rafraîchit l'affichage toutes les 10 s pendant 4 minutes.
      let tours = 0;
      clearInterval(suivi.current);
      suivi.current = setInterval(async () => {
        tours += 1;
        await charger();
        if (tours >= 24) {
          clearInterval(suivi.current);
          setEnCours(false);
        }
      }, 10000);
    } catch (err) {
      setError(err.message);
      setEnCours(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 mt-5">
      <div className="flex items-center gap-3 mb-4">
        <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
          <Cookie size={20} className="text-slate-500" />
        </div>
        <div className="flex-1">
          <p className="text-sm font-black text-slate-800">Sessions Vinted</p>
          <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
            Renouvelées chaque jour (13h-15h) sous {SEUIL_JOURS} jours restants
          </p>
        </div>
        <button
          onClick={charger}
          title="Relire les expirations"
          className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-50 rounded-lg transition-all"
        >
          <RefreshCw size={16} />
        </button>
      </div>

      {error && <p className="text-xs font-semibold text-red-600 mb-3">{error}</p>}

      <div className="space-y-2">
        {sessions ? Object.entries(sessions).map(([cle, session]) => (
          <div key={cle} className="flex items-center justify-between gap-3 px-3 py-2 rounded-xl bg-slate-50">
            <span className="text-sm font-bold text-slate-700">{session.nom}</span>
            <span className={`text-xs font-bold px-2 py-1 rounded-lg ${couleur(session.jours_restants)}`}>
              {libelle(session)}
            </span>
          </div>
        )) : !error && <p className="text-xs text-slate-400">Chargement…</p>}
      </div>

      <div className="flex flex-wrap gap-3 mt-4">
        <button
          onClick={() => renouveler(false)}
          disabled={enCours}
          className="flex items-center gap-2 px-4 py-2 bg-blue-500 text-white rounded-lg text-xs font-black uppercase disabled:opacity-40 hover:bg-blue-600 transition-all"
        >
          {enCours ? <Loader size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Renouveler celles qui expirent
        </button>
        <button
          onClick={() => renouveler(true)}
          disabled={enCours}
          className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs font-black uppercase disabled:opacity-40 hover:bg-slate-200 transition-all"
        >
          Tout renouveler
        </button>
      </div>
      {enCours && (
        <p className="text-[11px] text-slate-400 mt-2">
          Renouvellement en cours (un profil à la fois, ~30 s chacun) — l'affichage se met à jour tout seul.
        </p>
      )}
    </div>
  );
}
