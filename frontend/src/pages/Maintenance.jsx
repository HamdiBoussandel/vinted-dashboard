import React, { useState, useEffect, useCallback } from 'react';
import { Chrome, Globe, Wrench, ExternalLink, RefreshCw, PackageCheck, Zap, MessageSquare, Eye, TrendingDown, Heart, Eraser, Trash2 } from 'lucide-react';
import { maintenanceService } from '../services/api';
import SyncDatesCard from '../components/SyncDatesCard';

const STATUS_LABEL = {
  ferme: 'Fermé',
  ouverture: 'Ouverture...',
  ouvert: 'Ouvert',
};

const STATUS_STYLE = {
  ferme: 'bg-slate-100 text-slate-500',
  ouverture: 'bg-amber-100 text-amber-600',
  ouvert: 'bg-emerald-100 text-emerald-600',
};

const ProfileCard = ({ profile, onOpen, opening, onReset, resetting }) => {
  const isClemz = profile.key.startsWith('clemz');
  const Icon = profile.key.includes('edge') ? Globe : Chrome;
  const busy = profile.status !== 'ferme';

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0">
        <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
          <Icon size={20} className="text-slate-500" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-black text-slate-800 truncate">{profile.name}</p>
            <span className={`text-[11px] font-bold px-2 py-0.5 rounded-lg shrink-0 ${STATUS_STYLE[profile.status]}`}>
              {STATUS_LABEL[profile.status]}
            </span>
          </div>
          <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
            {isClemz ? 'Session + extension Clemz' : 'Session sans Clemz'}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        {isClemz && (
          <button
            onClick={() => onReset(profile.key)}
            disabled={busy || resetting === profile.key}
            title="Réinitialiser l'état de l'extension Clemz (après un crash)"
            className={`flex items-center justify-center px-3 py-2 rounded-lg text-sm font-bold transition-all
              ${busy
                ? 'bg-slate-50 text-slate-300 cursor-not-allowed'
                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
              }`}
          >
            <Eraser size={16} className={resetting === profile.key ? 'animate-pulse' : ''} />
          </button>
        )}

        <button
          onClick={() => onOpen(profile.key)}
          disabled={busy || opening === profile.key}
          className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all
            ${busy
              ? 'bg-slate-50 text-slate-300 cursor-not-allowed'
              : 'bg-[#6ED8EF] text-white hover:brightness-95 shadow-sm'
            }`}
        >
          <ExternalLink size={16} />
          {opening === profile.key ? 'Lancement...' : 'Ouvrir'}
        </button>
      </div>
    </div>
  );
};

const EXTENSION_STATUS_STYLE = {
  up_to_date: 'bg-emerald-100 text-emerald-600',
  outdated: 'bg-amber-100 text-amber-600',
  unknown: 'bg-slate-100 text-slate-500',
};

const ExtensionCard = ({ status, onUpdate, updating }) => {
  const hasComparison = status?.local_version && status?.chrome_found;
  const badgeKey = !hasComparison ? 'unknown' : (status.up_to_date ? 'up_to_date' : 'outdated');
  const badgeLabel = !hasComparison
    ? 'Statut inconnu'
    : (status.up_to_date ? 'À jour' : 'Mise à jour disponible');

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4 mb-8">
      <div className="flex items-center gap-3 min-w-0">
        <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
          <PackageCheck size={20} className="text-slate-500" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-black text-slate-800">Extension Clemz</p>
            <span className={`text-[11px] font-bold px-2 py-0.5 rounded-lg shrink-0 ${EXTENSION_STATUS_STYLE[badgeKey]}`}>
              {badgeLabel}
            </span>
          </div>
          <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
            Locale (Playwright) : {status?.local_version || 'inconnue'}
            {' — '}
            Chrome perso : {status?.chrome_found ? (status.chrome_version || 'inconnue') : 'introuvable'}
          </p>
        </div>
      </div>

      <button
        onClick={onUpdate}
        disabled={updating}
        className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all shrink-0
          ${updating
            ? 'bg-slate-50 text-slate-300 cursor-not-allowed'
            : 'bg-[#6ED8EF] text-white hover:brightness-95 shadow-sm'
          }`}
      >
        <RefreshCw size={16} className={updating ? 'animate-spin' : ''} />
        {updating ? 'Synchronisation...' : 'Mettre à jour depuis Chrome'}
      </button>
    </div>
  );
};

const ToggleSwitch = ({ checked, onChange, disabled }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    onClick={() => !disabled && onChange(!checked)}
    disabled={disabled}
    className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors shrink-0
      ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
      ${checked ? 'bg-emerald-500' : 'bg-slate-200'}`}
  >
    <span
      className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform
        ${checked ? 'translate-x-6' : 'translate-x-1'}`}
    />
  </button>
);

const WatchdogCard = ({ active, onToggle, loading }) => (
  <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4">
    <div className="flex items-center gap-3 min-w-0">
      <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
        <MessageSquare size={20} className="text-slate-500" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-black text-slate-800">Watchdog messages automatiques</p>
        <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
          Surveillance et réponse auto (Chrome + Edge)
        </p>
      </div>
    </div>

    <ToggleSwitch checked={active} onChange={onToggle} disabled={loading} />
  </div>
);

const BaissePrixAutoCard = ({ active, onToggle, loading }) => (
  <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4 mt-5">
    <div className="flex items-center gap-3 min-w-0">
      <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
        <TrendingDown size={20} className="text-slate-500" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-black text-slate-800">Baisse de prix automatique</p>
        <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
          Job quotidien 14h05 — n'affecte pas "Forcer baisse de prix"
        </p>
      </div>
    </div>

    <ToggleSwitch checked={active} onChange={onToggle} disabled={loading} />
  </div>
);

const PartageVuesFavorisAutoCard = ({ active, onToggle, loading }) => (
  <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4 mt-5">
    <div className="flex items-center gap-3 min-w-0">
      <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
        <Heart size={20} className="text-slate-500" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-black text-slate-800">Partage vues/favoris automatique</p>
        <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
          Déclenché après republication réussie — n'affecte pas le test manuel
        </p>
      </div>
    </div>

    <ToggleSwitch checked={active} onChange={onToggle} disabled={loading} />
  </div>
);

const ScrapingAutoCard = ({ active, onToggle, loading }) => (
  <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4 mt-5">
    <div className="flex items-center gap-3 min-w-0">
      <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
        <RefreshCw size={20} className="text-slate-500" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-black text-slate-800">Scraping automatique</p>
        <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
          Syncs planifiées 14h/22h — n'affecte pas "Lancer Scraping"
        </p>
      </div>
    </div>

    <ToggleSwitch checked={active} onChange={onToggle} disabled={loading} />
  </div>
);

const ClemzVisibleCard = ({ visible, onToggle, loading }) => (
  <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-6 flex items-center justify-between gap-4 mt-5">
    <div className="flex items-center gap-3 min-w-0">
      <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0">
        <Eye size={20} className="text-slate-500" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-black text-slate-800">Navigateur visible (mode test)</p>
        <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
          {visible
            ? 'Fenêtre affichée et maximisée à l\'écran pour observer les automatisations'
            : 'Mode discret actif — position hors écran'}
        </p>
      </div>
    </div>

    <ToggleSwitch checked={visible} onChange={onToggle} disabled={loading} />
  </div>
);

const ClearDatabaseCard = ({ onClear, clearing }) => (
  <div className="bg-white rounded-2xl border border-red-100 shadow-sm p-6 flex items-center justify-between gap-4 mt-5">
    <div className="flex items-center gap-3 min-w-0">
      <div className="h-10 w-10 rounded-xl bg-red-50 flex items-center justify-center shrink-0">
        <Trash2 size={20} className="text-red-500" />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-black text-slate-800">Vider la base de données</p>
        <p className="text-[11px] text-slate-400 font-semibold uppercase tracking-wide">
          Supprime tout l'inventaire — irréversible, à réserver aux tests
        </p>
      </div>
    </div>

    <button
      onClick={onClear}
      disabled={clearing}
      className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all shrink-0
        ${clearing
          ? 'bg-slate-50 text-slate-300 cursor-not-allowed'
          : 'bg-red-500 text-white hover:bg-red-600 shadow-sm'
        }`}
    >
      <Trash2 size={16} className={clearing ? 'animate-pulse' : ''} />
      {clearing ? 'Suppression...' : 'Vider'}
    </button>
  </div>
);

export default function Maintenance() {
  const [profiles, setProfiles] = useState([]);
  const [opening, setOpening] = useState(null);
  const [resetting, setResetting] = useState(null);
  const [error, setError] = useState(null);
  // Distinct de `error` : le reset extension (et lui seul) n'avait AUCUN
  // retour visuel en cas de succès -- un clic qui marchait semblait ne rien
  // faire, indistinguable d'un clic qui ne partait jamais (cf. échange du
  // 21/09/2026). S'efface tout seul après quelques secondes.
  const [resetSuccessMessage, setResetSuccessMessage] = useState(null);

  const [extensionStatus, setExtensionStatus] = useState(null);
  const [updatingExtension, setUpdatingExtension] = useState(false);

  const [watchdogActive, setWatchdogActive] = useState(false);
  const [watchdogLoading, setWatchdogLoading] = useState(false);

  const [baissePrixAutoActive, setBaissePrixAutoActive] = useState(true);
  const [baissePrixAutoLoading, setBaissePrixAutoLoading] = useState(false);
  const [scrapingAutoActive, setScrapingAutoActive] = useState(true);
  const [scrapingAutoLoading, setScrapingAutoLoading] = useState(false);

  const [partageAutoActive, setPartageAutoActive] = useState(true);
  const [partageAutoLoading, setPartageAutoLoading] = useState(false);

  const [clemzVisible, setClemzVisible] = useState(false);
  const [clemzVisibleLoading, setClemzVisibleLoading] = useState(false);

  const [clearingDb, setClearingDb] = useState(false);

  const fetchProfiles = useCallback(async () => {
    try {
      const data = await maintenanceService.getProfiles();
      setProfiles(data);
    } catch (err) {
      console.error('Erreur chargement profils maintenance', err);
    }
  }, []);

  const fetchExtensionStatus = useCallback(async () => {
    try {
      const data = await maintenanceService.getExtensionInfo();
      setExtensionStatus(data);
    } catch (err) {
      console.error('Erreur chargement statut extension', err);
    }
  }, []);

  const fetchWatchdogStatus = useCallback(async () => {
    try {
      const data = await maintenanceService.getWatchdogStatus();
      setWatchdogActive(!!data.active);
    } catch (err) {
      console.error('Erreur chargement statut watchdog', err);
    }
  }, []);

  const fetchClemzVisible = useCallback(async () => {
    try {
      const data = await maintenanceService.getClemzVisible();
      setClemzVisible(!!data.visible);
    } catch (err) {
      console.error('Erreur chargement statut navigateur visible', err);
    }
  }, []);

  const fetchScrapingAutoStatus = useCallback(async () => {
    try {
      const data = await maintenanceService.getScrapingAutoStatus();
      setScrapingAutoActive(!!data.active);
    } catch (err) {
      console.error('Erreur chargement statut scraping auto', err);
    }
  }, []);

  const fetchBaissePrixAutoStatus = useCallback(async () => {
    try {
      const data = await maintenanceService.getBaissePrixAutoStatus();
      setBaissePrixAutoActive(!!data.active);
    } catch (err) {
      console.error('Erreur chargement statut baisse de prix auto', err);
    }
  }, []);

  const fetchPartageAutoStatus = useCallback(async () => {
    try {
      const data = await maintenanceService.getPartageVuesFavorisAutoStatus();
      setPartageAutoActive(!!data.active);
    } catch (err) {
      console.error('Erreur chargement statut partage vues/favoris auto', err);
    }
  }, []);

  useEffect(() => {
    fetchProfiles();
    fetchExtensionStatus();
    fetchWatchdogStatus();
    fetchClemzVisible();
    fetchBaissePrixAutoStatus();
    fetchScrapingAutoStatus();
    fetchPartageAutoStatus();
    // Poll léger : reflète l'ouverture/fermeture manuelle du navigateur, détecte
    // si Chrome a reçu une nouvelle version de Clemz, et reflète le statut watchdog
    // + le mode navigateur visible + les deux toggles auto (baisse de prix, partage).
    const interval = setInterval(() => {
      fetchProfiles();
      fetchExtensionStatus();
      fetchWatchdogStatus();
      fetchClemzVisible();
      fetchBaissePrixAutoStatus();
      fetchPartageAutoStatus();
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchProfiles, fetchExtensionStatus, fetchWatchdogStatus, fetchClemzVisible, fetchBaissePrixAutoStatus, fetchPartageAutoStatus, fetchScrapingAutoStatus]);

  const handleOpen = async (key) => {
    setError(null);
    setOpening(key);
    try {
      await maintenanceService.openBrowser(key);
      await fetchProfiles();
    } catch (err) {
      console.error('Erreur ouverture navigateur', err);
      setError(err.message || "Impossible d'ouvrir le navigateur.");
    } finally {
      setOpening(null);
    }
  };

  const handleReset = async (key) => {
    setError(null);
    setResetSuccessMessage(null);
    setResetting(key);
    try {
      const result = await maintenanceService.resetExtensionState(key);
      await fetchProfiles();
      setResetSuccessMessage(
        result.removed?.length
          ? `${result.profile} : état de l'extension réinitialisé (${result.removed.join(', ')}).`
          : `${result.profile} : rien à réinitialiser, déjà propre.`
      );
      setTimeout(() => setResetSuccessMessage(null), 5000);
    } catch (err) {
      console.error('Erreur réinitialisation extension Clemz', err);
      setError(err.message || "Impossible de réinitialiser l'extension Clemz. Ferme le profil d'abord si besoin.");
    } finally {
      setResetting(null);
    }
  };

  const handleUpdateExtension = async () => {
    setError(null);
    setUpdatingExtension(true);
    try {
      await maintenanceService.updateExtension();
      await fetchExtensionStatus();
    } catch (err) {
      console.error('Erreur mise à jour extension', err);
      setError(err.message || "Impossible de mettre à jour l'extension Clemz.");
    } finally {
      setUpdatingExtension(false);
    }
  };

  const handleToggleWatchdog = async (nextValue) => {
    setError(null);
    setWatchdogLoading(true);
    // Optimiste : le toggle bouge tout de suite, corrigé si l'appel échoue.
    setWatchdogActive(nextValue);
    try {
      const data = await maintenanceService.toggleWatchdog(nextValue);
      setWatchdogActive(!!data.active);
    } catch (err) {
      console.error('Erreur bascule watchdog', err);
      setError(err.message || "Impossible de changer l'état du watchdog.");
      setWatchdogActive(!nextValue);
    } finally {
      setWatchdogLoading(false);
    }
  };

  const handleToggleClemzVisible = async (nextValue) => {
    setError(null);
    setClemzVisibleLoading(true);
    setClemzVisible(nextValue);
    try {
      const data = await maintenanceService.toggleClemzVisible(nextValue);
      setClemzVisible(!!data.visible);
    } catch (err) {
      console.error('Erreur bascule navigateur visible', err);
      setError(err.message || "Impossible de changer le mode navigateur visible.");
      setClemzVisible(!nextValue);
    } finally {
      setClemzVisibleLoading(false);
    }
  };

  const handleToggleBaissePrixAuto = async (nextValue) => {
    setError(null);
    setBaissePrixAutoLoading(true);
    setBaissePrixAutoActive(nextValue);
    try {
      const data = await maintenanceService.toggleBaissePrixAuto(nextValue);
      setBaissePrixAutoActive(!!data.active);
    } catch (err) {
      console.error('Erreur bascule baisse de prix auto', err);
      setError(err.message || "Impossible de changer l'état de la baisse de prix automatique.");
      setBaissePrixAutoActive(!nextValue);
    } finally {
      setBaissePrixAutoLoading(false);
    }
  };

  const handleToggleScrapingAuto = async (nextValue) => {
    setError(null);
    setScrapingAutoLoading(true);
    setScrapingAutoActive(nextValue);
    try {
      const data = await maintenanceService.toggleScrapingAuto(nextValue);
      setScrapingAutoActive(!!data.active);
    } catch (err) {
      console.error('Erreur bascule scraping auto', err);
      setError(err.message || "Impossible de changer l'état du scraping automatique.");
      setScrapingAutoActive(!nextValue);
    } finally {
      setScrapingAutoLoading(false);
    }
  };

  const handleTogglePartageAuto = async (nextValue) => {
    setError(null);
    setPartageAutoLoading(true);
    setPartageAutoActive(nextValue);
    try {
      const data = await maintenanceService.togglePartageVuesFavorisAuto(nextValue);
      setPartageAutoActive(!!data.active);
    } catch (err) {
      console.error('Erreur bascule partage vues/favoris auto', err);
      setError(err.message || "Impossible de changer l'état du partage vues/favoris automatique.");
      setPartageAutoActive(!nextValue);
    } finally {
      setPartageAutoLoading(false);
    }
  };

  const handleClearDatabase = async () => {
    if (!window.confirm("Vider TOUTE la base de données ?")) return;
    setError(null);
    setClearingDb(true);
    try {
      const response = await fetch('http://localhost:8000/api/clear-database', { method: 'DELETE' });
      if (!response.ok) throw new Error();
    } catch (err) {
      console.error('Erreur suppression base de données', err);
      setError("Impossible de vider la base de données.");
    } finally {
      setClearingDb(false);
    }
  };

  return (
    <div className="p-10">
      <div className="flex items-center gap-3 mb-1">
        <Wrench size={22} className="text-slate-400" />
        <h1 className="text-2xl font-black text-slate-800">Maintenance</h1>
      </div>
      <p className="text-sm text-slate-400 font-semibold mb-8">
        Ouvre le navigateur Playwright utilisé par les tâches d'automatisation, pour
        vérifier ou rétablir la connexion Vinted/Clemz et ajuster des paramètres Clemz à la main.
      </p>

      {error && (
        <div className="mb-6 px-4 py-3 rounded-xl bg-red-50 text-red-600 text-sm font-semibold">
          {error}
        </div>
      )}

      {resetSuccessMessage && (
        <div className="mb-6 px-4 py-3 rounded-xl bg-emerald-50 text-emerald-600 text-sm font-semibold">
          {resetSuccessMessage}
        </div>
      )}

      <ExtensionCard
        status={extensionStatus}
        onUpdate={handleUpdateExtension}
        updating={updatingExtension}
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {profiles.map((profile) => (
          <ProfileCard
            key={profile.key}
            profile={profile}
            onOpen={handleOpen}
            opening={opening}
            onReset={handleReset}
            resetting={resetting}
          />
        ))}
      </div>

      <div className="flex items-center gap-3 mt-12 mb-4">
        <Zap size={18} className="text-slate-400" />
        <h2 className="text-lg font-black text-slate-800">Automatisation</h2>
      </div>

      <WatchdogCard
        active={watchdogActive}
        onToggle={handleToggleWatchdog}
        loading={watchdogLoading}
      />

      <BaissePrixAutoCard
        active={baissePrixAutoActive}
        onToggle={handleToggleBaissePrixAuto}
        loading={baissePrixAutoLoading}
      />

      <ScrapingAutoCard
        active={scrapingAutoActive}
        onToggle={handleToggleScrapingAuto}
        loading={scrapingAutoLoading}
      />

      <PartageVuesFavorisAutoCard
        active={partageAutoActive}
        onToggle={handleTogglePartageAuto}
        loading={partageAutoLoading}
      />

      <ClemzVisibleCard
        visible={clemzVisible}
        onToggle={handleToggleClemzVisible}
        loading={clemzVisibleLoading}
      />

      <div className="flex items-center gap-3 mt-12 mb-4">
        <RefreshCw size={18} className="text-slate-400" />
        <h2 className="text-lg font-black text-slate-800">Données</h2>
      </div>

      <SyncDatesCard />

      <ClearDatabaseCard onClear={handleClearDatabase} clearing={clearingDb} />
    </div>
  );
}