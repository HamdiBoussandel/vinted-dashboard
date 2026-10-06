import React, { useState, useRef } from 'react';
import { CalendarClock, Upload, Loader, CheckCircle2 } from 'lucide-react';

const API_URL = "http://localhost:8000/api";

export default function SyncDatesCard() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const [error, setError] = useState(null);
  const [applied, setApplied] = useState(null);
  const fileInputRef = useRef(null);

  const runSync = async (selectedFile, apply) => {
    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('apply', apply ? 'true' : 'false');

    const res = await fetch(`${API_URL}/maintenance/sync-dates-republication`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || 'Erreur serveur');
    }
    return res.json();
  };

  const handleFileChange = async (e) => {
    const selectedFile = e.target.files?.[0];
    if (!selectedFile) return;

    setFile(selectedFile);
    setPreview(null);
    setApplied(null);
    setError(null);
    setIsLoading(true);

    try {
      const data = await runSync(selectedFile, false);
      setPreview(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleApply = async () => {
    if (!file) return;
    setIsApplying(true);
    setError(null);
    try {
      const data = await runSync(file, true);
      setApplied(data);
      setPreview(null);
      setFile(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsApplying(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 mt-5">
      <div className="flex items-center gap-3 mb-4">
        <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
          <CalendarClock size={20} className="text-slate-500" />
        </div>
        <div>
          <p className="text-sm font-black text-slate-800">Synchroniser les dates de republication</p>
          <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
            Depuis un export CSV Clemz
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 rounded-xl bg-red-50 text-red-600 text-sm font-semibold">
          {error}
        </div>
      )}

      {!preview && !applied && (
        <label className="flex flex-col items-center justify-center gap-2 border-2 border-dashed border-slate-200 rounded-xl py-8 cursor-pointer hover:border-[#6ED8EF] hover:bg-slate-50/50 transition-all">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            onChange={handleFileChange}
            className="hidden"
            disabled={isLoading}
          />
          {isLoading ? (
            <>
              <Loader size={22} className="text-slate-400 animate-spin" />
              <span className="text-xs font-bold text-slate-500">Analyse en cours...</span>
            </>
          ) : (
            <>
              <Upload size={22} className="text-slate-300" />
              <span className="text-xs font-bold text-slate-500">
                Clique pour choisir le CSV exporté de Clemz
              </span>
            </>
          )}
        </label>
      )}

      {preview && (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-[10px] font-black uppercase text-slate-400">À mettre à jour</p>
              <p className="text-lg font-black text-slate-700">{preview.to_update_count}</p>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-[10px] font-black uppercase text-slate-400">Déjà à jour</p>
              <p className="text-lg font-black text-slate-700">{preview.unchanged_count}</p>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-[10px] font-black uppercase text-slate-400">Non trouvés</p>
              <p className="text-lg font-black text-slate-700">{preview.not_found_count}</p>
            </div>
          </div>

          {(preview.updates_preview?.length > 0 || preview.not_found?.length > 0) && (
            <div className="max-h-56 overflow-y-auto flex flex-col gap-1 border border-slate-100 rounded-xl p-3 bg-slate-50/50">
              {preview.updates_preview.map((u, i) => (
                <div key={i} className="text-[11px] text-slate-600">
                  <span className="font-bold">{u.nom}</span>
                  <span className="text-slate-400"> — {u.raisons.join(' · ')}</span>
                </div>
              ))}
              {preview.to_update_count > preview.updates_preview.length && (
                <p className="text-[10px] text-slate-400 italic mt-1">
                  ... et {preview.to_update_count - preview.updates_preview.length} de plus
                </p>
              )}

              {preview.not_found?.length > 0 && (
                <>
                  <p className="text-[10px] font-black uppercase text-red-400 mt-3 mb-1">
                    Non trouvés dans le CSV ({preview.not_found_count})
                  </p>
                  {preview.not_found.map((nom, i) => (
                    <div key={`nf-${i}`} className="text-[11px] text-red-500 font-medium">
                      {nom}
                    </div>
                  ))}
                  {preview.not_found_count > preview.not_found.length && (
                    <p className="text-[10px] text-red-300 italic mt-1">
                      ... et {preview.not_found_count - preview.not_found.length} de plus
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={handleApply}
              disabled={isApplying || preview.to_update_count === 0}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all
                ${isApplying || preview.to_update_count === 0
                  ? 'bg-slate-50 text-slate-300 cursor-not-allowed'
                  : 'bg-[#6ED8EF] text-white hover:brightness-95 shadow-sm'
                }`}
            >
              {isApplying ? <Loader size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
              {isApplying ? 'Application...' : `Confirmer la mise à jour de ${preview.to_update_count} article(s)`}
            </button>
            <button
              onClick={() => { setPreview(null); setFile(null); }}
              disabled={isApplying}
              className="px-4 py-2 rounded-lg text-sm font-bold text-slate-500 border border-slate-200 hover:border-slate-300 transition-all"
            >
              Annuler
            </button>
          </div>
        </div>
      )}

      {applied && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-emerald-50 border border-emerald-100 text-sm">
          <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />
          <span className="text-emerald-700 font-medium">
            {applied.success_count} article(s) mis à jour.
            {applied.errors?.length > 0 && ` ${applied.errors.length} erreur(s).`}
          </span>
        </div>
      )}
    </div>
  );
}