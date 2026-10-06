import { useState, useEffect, useCallback, useRef } from 'react';
import { Camera, Loader2, CheckCircle2, XCircle, HelpCircle, Scissors, Check, Trash2, Sparkles, Eye, CheckSquare, X,RefreshCw, LayoutGrid  } from 'lucide-react';
import { preparerAnnoncesService } from '../services/api';

const INTERVALLE_POLLING_MS = 4000;

// Mode "regroupement manuel" : grille de 9 zones-lots (3x3), chacune est une
// cible de glisser-déposer DIRECTEMENT depuis l'explorateur de fichiers --
// aucun lien avec les lots existants en base. Une fois remplie, une zone se
// valide indépendamment des autres (upload + création du lot), ce qui la vide
// et la rend disponible pour le groupe suivant.
const NB_SLOTS_VRAC = 9;

// Auto-scroll pendant un drag de photo(s) -- zone sensible en haut/bas du
// conteneur scrollable (en pixels) et vitesse max atteinte au bord extrême.
const AUTOSCROLL_ZONE_PX = 90;
const AUTOSCROLL_VITESSE_MAX_PX = 18;

function trouverConteneurScrollable(element) {
  let el = element?.parentElement;
  while (el) {
    const style = window.getComputedStyle(el);
    if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
      return el;
    }
    el = el.parentElement;
  }
  return document.scrollingElement || document.documentElement;
}

const STATUT_CONFIG = {
  pending_validation: {
    label: "En attente de validation",
    badge: "bg-amber-50 text-amber-700 border-amber-200",
    icon: Eye,
    spin: false,
  },
  pending_generation: {
    label: "Génération IA en cours...",
    badge: "bg-blue-50 text-blue-700 border-blue-200",
    icon: Loader2,
    spin: true,
  },
  generation_ok: {
    label: "Prêt",
    badge: "bg-green-50 text-green-700 border-green-200",
    icon: CheckCircle2,
    spin: false,
  },
  generation_error: {
    label: "Erreur de génération",
    badge: "bg-red-50 text-red-700 border-red-200",
    icon: XCircle,
    spin: false,
  },
  a_regrouper_manuellement: {
    label: "À regrouper manuellement",
    badge: "bg-orange-50 text-orange-700 border-orange-200",
    icon: HelpCircle,
    spin: false,
  },
  draft_error: {
    label: "Erreur de création du brouillon",
    badge: "bg-red-50 text-red-700 border-red-200",
    icon: XCircle,
    spin: false,
  },
};

function ToggleSwitch({ actif, onToggle, disabled }) {
  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors duration-200 ${
        actif ? 'bg-[#21C55D]' : 'bg-gray-300'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <span
        className={`inline-block h-6 w-6 transform rounded-full bg-white shadow-md transition-transform duration-200 ${
          actif ? 'translate-x-7' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

const OPTIONS_ETAT_DEFAUT = [
  { value: 'neuf', label: '✨ Neuf / Jamais porté' },
  { value: 'tache_legere', label: '🟡 Tache légère' },
  { value: 'decoloration', label: '🧴 Décoloration / Javel' },
  { value: 'retouche', label: '✂️ Vêtement retouché' },
];

const OPTIONS_FABRICATION = [
  { value: 'italie', label: '🇮🇹 Made in Italy' },
  { value: 'portugal', label: '🇵🇹 Made in Portugal' },
  { value: 'france', label: '🇫🇷 Made in France' },
];

function BoutonToggleRadio({ label, actif, onClick, disabled }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`text-[11px] font-semibold px-2.5 py-1.5 rounded-lg border transition-all ${
        actif
          ? 'bg-indigo-600 text-white border-indigo-600'
          : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
    >
      {label}
    </button>
  );
}

function LotCard({ lot, allLots, onRefresh, statutBrouillons, onRafraichirStatutBrouillons }) {
  const [modeSelection, setModeSelection] = useState(false);
  const [photosSelectionnees, setPhotosSelectionnees] = useState(new Set());
  const [lotCible, setLotCible] = useState('');
  const [division, setDivision] = useState(false);
  const [erreurDivision, setErreurDivision] = useState(null);
  const [validation, setValidation] = useState(false);
  const [erreurValidation, setErreurValidation] = useState(null);
  const [suppression, setSuppression] = useState(false);
  const [erreurSuppression, setErreurSuppression] = useState(null);
  const [relanceCreation, setRelanceCreation] = useState(false);
  const [erreurRelanceCreation, setErreurRelanceCreation] = useState(null);
  const [creationUnique, setCreationUnique] = useState(false);
  const [erreurCreationUnique, setErreurCreationUnique] = useState(null);
  const [survolPendantDrag, setSurvolPendantDrag] = useState(false);
  const [photoAgrandie, setPhotoAgrandie] = useState(null);
  const [dressing, setDressing] = useState(lot.dressing || 'Dressing 1');
  const [dressingEnCours, setDressingEnCours] = useState(false);
  const [erreurDressing, setErreurDressing] = useState(null);

  // Modifiable jusqu'à la création effective du brouillon -- une fois le
  // navigateur du dressing d'origine engagé (creating_draft/draft_created),
  // changer reviendrait à promettre un profil qui n'a pas réellement servi.
  const dressingModifiable = lot.status !== 'creating_draft' && lot.status !== 'draft_created';

  const changerDressing = async (nouveauDressing) => {
    if (nouveauDressing === dressing) return;
    const precedent = dressing;
    setDressing(nouveauDressing);
    setDressingEnCours(true);
    setErreurDressing(null);
    try {
      await preparerAnnoncesService.changerDressingLot(lot.id, nouveauDressing);
      onRefresh();
    } catch (e) {
      setDressing(precedent);
      setErreurDressing(e.message || "Échec du changement de dressing.");
    } finally {
      setDressingEnCours(false);
    }
  };

  const supprimerCeLot = async () => {
    setSuppression(true);
    setErreurSuppression(null);
    try {
      await preparerAnnoncesService.supprimerLot(lot.id);
      onRefresh();
    } catch (err) {
      setErreurSuppression(err.message || "Échec de la suppression.");
      setSuppression(false);
    }
  };

  const [etatDefaut, setEtatDefaut] = useState(lot.ajustements_manuels?.etat_defaut || null);
  const [fabrication, setFabrication] = useState(lot.ajustements_manuels?.fabrication || null);
  const [regeneration, setRegeneration] = useState(false);
  const [erreurRegeneration, setErreurRegeneration] = useState(null);

  const ajustementsModifies =
    etatDefaut !== (lot.ajustements_manuels?.etat_defaut || null) ||
    fabrication !== (lot.ajustements_manuels?.fabrication || null);

  const lancerRegeneration = async () => {
    setRegeneration(true);
    setErreurRegeneration(null);
    try {
      await preparerAnnoncesService.regenererLot(lot.id, {
        etat_defaut: etatDefaut,
        fabrication: fabrication,
      });
      onRefresh(); // rafraîchit la liste des lots après régénération
    } catch (err) {
      console.error('Erreur régénération lot', err);
      setErreurRegeneration(err.message || 'Échec de la régénération.');
    } finally {
      setRegeneration(false);
    }
  };

  const config = STATUT_CONFIG[lot.status] || {
    label: lot.status,
    badge: "bg-slate-50 text-slate-600 border-slate-200",
    icon: HelpCircle,
    spin: false,
  };
  const Icon = config.icon;
  const raw = lot.gemini_output?.raw;
  const resolution = lot.gemini_output?.resolution;

  const categorieAffichee = resolution?.categorie_chemin
    ? resolution.categorie_chemin.split(' > ').slice(1).join(' > ')
    : null;

  const peutModifier = lot.status === 'pending_validation' || lot.status === 'a_regrouper_manuellement';
  const peutDiviser = (lot.photos?.length || 0) >= 2 && peutModifier;
  const peutValider = peutModifier && (lot.photos?.length || 0) > 0;
  const peutReessayer = (lot.status === 'generation_error' || lot.status === 'generation_ok') && (lot.photos?.length || 0) > 0;
  const autresLots = (allLots || []).filter((l) => l.id !== lot.id);

  const relancerCreationBrouillon = async () => {
    setRelanceCreation(true);
    setErreurRelanceCreation(null);
    try {
      await preparerAnnoncesService.reessayerCreationBrouillon(lot.id);
      onRefresh();
    } catch (e) {
      setErreurRelanceCreation(e.message || "Échec de la relance.");
    } finally {
      setRelanceCreation(false);
    }
  };

  const creerBrouillonPourCeLot = async () => {
    if (!window.confirm(
      `Ce brouillon utilisera le profil Chrome de ${dressing}, partagé avec l'automatisation Clemz. ` +
      "Si une republication ou baisse de prix est en cours sur ce dressing, la création attendra " +
      "qu'elle se termine avant de démarrer.\n\n" +
      "Créer le brouillon de ce lot maintenant ?"
    )) {
      return;
    }
    setCreationUnique(true);
    setErreurCreationUnique(null);
    try {
      await preparerAnnoncesService.creerBrouillonUnique(lot.id);
      onRafraichirStatutBrouillons?.();
    } catch (e) {
      setErreurCreationUnique(e.message || "Échec du lancement.");
    } finally {
      setCreationUnique(false);
    }
  };

  const lancerValidation = async () => {
    setValidation(true);
    setErreurValidation(null);
    try {
      await preparerAnnoncesService.validerLot(lot.id);
      onRefresh();
    } catch (e) {
      setErreurValidation(e.message || "Échec de la validation.");
    } finally {
      setValidation(false);
    }
  };

  const togglePhoto = (photo) => {
    setPhotosSelectionnees((prev) => {
      const suivant = new Set(prev);
      if (suivant.has(photo)) suivant.delete(photo);
      else suivant.add(photo);
      return suivant;
    });
  };

  const annulerDivision = () => {
    setModeSelection(false);
    setPhotosSelectionnees(new Set());
    setLotCible('');
    setErreurDivision(null);
  };

  const confirmerDivision = async () => {
    if (photosSelectionnees.size === 0) {
      setErreurDivision("Sélectionne au moins une photo.");
      return;
    }
    if (!lotCible && photosSelectionnees.size === lot.photos.length) {
      setErreurDivision("Sélectionne au moins une photo, sans tout sélectionner -- ou choisis un lot cible pour tout y déplacer.");
      return;
    }
    setDivision(true);
    setErreurDivision(null);
    try {
      if (lotCible) {
        await preparerAnnoncesService.deplacerPhotos(lot.id, Array.from(photosSelectionnees), lotCible);
      } else {
        await preparerAnnoncesService.splitLot(lot.id, Array.from(photosSelectionnees));
      }
      annulerDivision();
      onRefresh();
    } catch (e) {
      setErreurDivision(e.message);
    } finally {
      setDivision(false);
    }
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setSurvolPendantDrag(true);
      }}
      onDragLeave={() => setSurvolPendantDrag(false)}
      onDrop={async (e) => {
        e.preventDefault();
        setSurvolPendantDrag(false);
        const data = e.dataTransfer.getData("application/json");
        if (!data) return;
        let payload;
        try {
          payload = JSON.parse(data);
        } catch {
          return;
        }
        const { lotId: lotIdSource, photos: photosADeplacer } = payload;
        if (!lotIdSource || lotIdSource === lot.id || !photosADeplacer?.length) return; // pas de dépôt sur soi-même
        try {
          await preparerAnnoncesService.deplacerPhotos(lotIdSource, photosADeplacer, lot.id);
          onRefresh();
        } catch (err) {
          setErreurDivision(err.message || "Échec du déplacement par glisser-déposer.");
        }
      }}
      className={`bg-white rounded-xl border-2 shadow-sm overflow-hidden p-4 flex flex-col md:flex-row gap-4 transition-all ${
        survolPendantDrag ? 'border-orange-400 bg-orange-50/40' : 'border-slate-100'
      }`}
    >
      {/* Colonne photos -- toutes les photos du lot, pour vérification visuelle */}
      <div className="md:w-56 shrink-0">
        {lot.photos?.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {lot.photos.map((photo, i) => {
              const selectionnee = photosSelectionnees.has(photo);
              // Nom de fichier seul (ex: 20260802_184625.jpg) -- extrait du
              // chemin complet archivé, pour identifier facilement l'heure de
              // prise de vue lors du diagnostic du regroupement.
              const nomFichier = photo.split(/[\\/]/).pop();
              return (
                <div key={i} className="flex flex-col items-center w-20">
                  <button
                    onClick={() => modeSelection ? togglePhoto(photo) : setPhotoAgrandie(photo)}
                    draggable={peutModifier}
                    onDragStart={(e) => {
                      // Si la photo glissée fait partie de la sélection en cours,
                      // on déplace TOUTE la sélection d'un coup -- sinon, juste
                      // cette photo isolée (pas besoin d'entrer en mode sélection
                      // pour déplacer une seule photo rapidement).
                      const photosADeplacer = modeSelection && selectionnee
                        ? Array.from(photosSelectionnees)
                        : [photo];
                      e.dataTransfer.setData(
                        "application/json",
                        JSON.stringify({ lotId: lot.id, photos: photosADeplacer })
                      );
                    }}
                    className={`relative w-14 h-14 rounded-lg overflow-hidden border-2 transition-all ${
                      modeSelection
                        ? selectionnee
                          ? 'border-orange-500'
                          : 'border-slate-100 hover:border-slate-300'
                        : 'border-slate-100 cursor-default'
                    } ${peutModifier ? 'cursor-grab active:cursor-grabbing' : ''}`}
                  >
                    <img
                      src={preparerAnnoncesService.getPhotoUrl(photo)}
                      alt={`Photo ${i + 1}`}
                      className="w-full h-full object-cover"
                    />
                    {modeSelection && selectionnee && (
                      <div className="absolute inset-0 bg-orange-500/40 flex items-center justify-center">
                        <Check size={18} className="text-white" strokeWidth={3} />
                      </div>
                    )}
                  </button>
                  <span className="text-[8px] text-slate-400 mt-0.5 w-full text-center break-all leading-tight">
                    {nomFichier}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="w-14 h-14 flex items-center justify-center text-slate-300 bg-slate-50 rounded-lg">
            <Camera size={20} />
          </div>
        )}
        <p className="text-[10px] text-slate-400 mt-2">
          {lot.photos?.length || 0} photo(s) · {new Date(lot.created_at).toLocaleString('fr-FR')}
        </p>

        <select
          value={dressing}
          onChange={(e) => changerDressing(e.target.value)}
          disabled={!dressingModifiable || dressingEnCours}
          title={dressingModifiable ? "Dressing utilisé pour créer ce brouillon" : "Brouillon déjà créé -- dressing figé"}
          className="mt-1.5 w-full text-[11px] font-medium border border-slate-200 rounded-lg px-2 py-1 text-slate-600 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <option value="Dressing 1">Dressing 1</option>
          <option value="Dressing 2">Dressing 2</option>
        </select>
        {erreurDressing && <p className="text-[10px] text-red-500 mt-1">{erreurDressing}</p>}

        <button
          onClick={supprimerCeLot}
          disabled={suppression}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-red-600 mt-1.5 font-medium disabled:opacity-50"
        >
          <Trash2 size={13} />
          {suppression ? 'Suppression...' : 'Supprimer ce lot'}
        </button>
        {erreurSuppression && <p className="text-[11px] text-red-500 mt-1">{erreurSuppression}</p>}

        {peutReessayer && (
          <button
            onClick={lancerValidation}
            disabled={validation}
            className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50 mt-1.5"
          >
            <RefreshCw size={13} className={validation ? 'animate-spin' : ''} />
            {validation
              ? 'Nouvelle tentative...'
              : lot.status === 'generation_ok' ? 'Régénérer quand même' : 'Réessayer'}
          </button>
        )}
        {erreurValidation && <p className="text-[11px] text-red-500 mt-1">{erreurValidation}</p>}

        {lot.status === 'generation_ok' && (
          <button
            onClick={creerBrouillonPourCeLot}
            disabled={creationUnique || statutBrouillons?.en_cours}
            className="flex items-center gap-1.5 text-xs text-emerald-600 hover:text-emerald-700 font-medium disabled:opacity-50 mt-1.5"
          >
            <CheckSquare size={13} className={creationUnique ? 'animate-pulse' : ''} />
            {creationUnique
              ? 'Lancement...'
              : statutBrouillons?.en_cours ? 'Traitement en cours...' : 'Créer ce brouillon'}
          </button>
        )}
        {erreurCreationUnique && <p className="text-[11px] text-red-500 mt-1">{erreurCreationUnique}</p>}

        {lot.status === 'draft_error' && (
          <button
            onClick={relancerCreationBrouillon}
            disabled={relanceCreation}
            className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50 mt-1.5"
          >
            <RefreshCw size={13} className={relanceCreation ? 'animate-spin' : ''} />
            {relanceCreation ? 'Relance...' : 'Réessayer la création du brouillon'}
          </button>
        )}
        {erreurRelanceCreation && <p className="text-[11px] text-red-500 mt-1">{erreurRelanceCreation}</p>}

        {peutModifier && !modeSelection && (
          <div className="flex flex-col gap-1.5 mt-2">
            {peutDiviser && (
              <button
                onClick={() => setModeSelection(true)}
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-orange-600 font-medium"
              >
                <Scissors size={13} /> Corriger le regroupement
              </button>
            )}
            {lot.status === 'a_regrouper_manuellement' && (
              <button
                onClick={() => setModeSelection(true)}
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-orange-600 font-medium"
              >
                <Scissors size={13} /> Déplacer vers un autre lot
              </button>
            )}
            {peutValider && (
              <button
                onClick={lancerValidation}
                disabled={validation}
                className="flex items-center gap-1.5 text-xs text-emerald-600 hover:text-emerald-700 font-medium disabled:opacity-50"
              >
                <CheckSquare size={13} className={validation ? 'animate-pulse' : ''} />
                {validation ? 'Validation...' : 'Valider ce lot'}
              </button>
            )}
            {erreurValidation && <p className="text-[11px] text-red-500">{erreurValidation}</p>}
          </div>
        )}

        {modeSelection && (
          <div className="mt-2 space-y-1.5">
            <p className="text-[11px] text-slate-500">
              Coche les photos à extraire ({photosSelectionnees.size} sélectionnée(s))
            </p>

            {autresLots.length > 0 && (
              <select
                value={lotCible}
                onChange={(e) => setLotCible(e.target.value)}
                className="w-full text-[11px] border border-slate-200 rounded-lg px-2 py-1.5 text-slate-600"
              >
                <option value="">→ Nouveau lot séparé</option>
                {autresLots.map((l, i) => (
                  <option key={l.id} value={l.id}>
                    → Fusionner dans lot #{i + 1} ({l.photos?.length || 0} photo(s), {STATUT_CONFIG[l.status]?.label || l.status})
                  </option>
                ))}
              </select>
            )}

            {erreurDivision && <p className="text-[11px] text-red-500">{erreurDivision}</p>}
            <div className="flex gap-2">
              <button
                onClick={confirmerDivision}
                disabled={division}
                className="text-xs bg-orange-500 hover:bg-orange-600 disabled:opacity-50 text-white font-semibold px-3 py-1.5 rounded-lg"
              >
                {division ? 'Traitement...' : 'Confirmer'}
              </button>
              <button
                onClick={annulerDivision}
                disabled={division}
                className="text-xs bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold px-3 py-1.5 rounded-lg"
              >
                Annuler
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Colonne infos générées */}
      <div className="flex-1 min-w-0">
        <div className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded-full border ${config.badge}`}>
          <Icon size={13} className={config.spin ? 'animate-spin' : ''} />
          {config.label}
        </div>

        {raw && (
          <div className="mt-3">
            <p className="text-sm font-bold text-slate-800">{raw.titre}</p>
            <p className="text-xs text-slate-500 mt-1 line-clamp-3 whitespace-pre-line">{raw.description}</p>

            <div className="flex flex-wrap gap-2 mt-3">
              {(resolution?.marque_nom || raw.marque_brute) && raw.marque_brute !== 'Non précisé' && (
                <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg">
                  🏷️ {resolution?.marque_nom || raw.marque_brute}
                </span>
              )}
              {categorieAffichee && (
                <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg">📁 {categorieAffichee}</span>
              )}
              {(raw.couleurs || []).map((c) => (
                <span key={c} className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg">🎨 {c}</span>
              ))}
              {raw.etat && (
                <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg">✨ {raw.etat}</span>
              )}
              {(raw.matieres || []).map((m) => (
                <span key={m} className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg">🧵 {m}</span>
              ))}
              {raw.taille_brute && raw.taille_brute !== 'Non précisé' && (
                <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg">📏 {raw.taille_brute}</span>
              )}
            </div>

            {raw.points_vigilance && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5 mt-2">
                ⚠️ {raw.points_vigilance}
              </p>
            )}

            {/* Ajustements manuels -- état/défaut et fabrication influencent le
                prix ET la description lors de la régénération Gemini */}
            <div className="mt-3 pt-3 border-t border-slate-100">
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wide mb-1.5">État / Défaut</p>
              <div className="flex flex-wrap gap-1.5">
                {OPTIONS_ETAT_DEFAUT.map((opt) => (
                  <BoutonToggleRadio
                    key={opt.value}
                    label={opt.label}
                    actif={etatDefaut === opt.value}
                    disabled={regeneration}
                    onClick={() => setEtatDefaut(etatDefaut === opt.value ? null : opt.value)}
                  />
                ))}
              </div>

              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wide mb-1.5 mt-2.5">Fabrication</p>
              <div className="flex flex-wrap gap-1.5">
                {OPTIONS_FABRICATION.map((opt) => (
                  <BoutonToggleRadio
                    key={opt.value}
                    label={opt.label}
                    actif={fabrication === opt.value}
                    disabled={regeneration}
                    onClick={() => setFabrication(fabrication === opt.value ? null : opt.value)}
                  />
                ))}
              </div>

              {ajustementsModifies && (
                <button
                  onClick={lancerRegeneration}
                  disabled={regeneration}
                  className="mt-2.5 flex items-center gap-1.5 text-[11px] font-bold px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  <Sparkles size={13} className={regeneration ? 'animate-pulse' : ''} />
                  {regeneration ? 'Régénération...' : 'Régénérer avec ces infos'}
                </button>
              )}
              {erreurRegeneration && (
                <p className="text-[10px] text-red-500 mt-1.5">{erreurRegeneration}</p>
              )}
            </div>
          </div>
        )}

        {(lot.status === 'generation_error' || lot.status === 'draft_error') && lot.error_message && (
          <p className="text-xs text-red-500 mt-2">{lot.error_message}</p>
        )}

        {lot.status === 'a_regrouper_manuellement' && (
          <p className="text-xs text-orange-600 mt-2">
            Cette photo n'a pas pu être datée avec fiabilité (ex : image WhatsApp) — regroupement automatique impossible.
          </p>
        )}
      </div>
    {photoAgrandie && (
        <div
          onClick={() => setPhotoAgrandie(null)}
          className="fixed inset-0 bg-black/85 z-[100] flex items-center justify-center p-6 cursor-zoom-out"
        >
          <img
            src={preparerAnnoncesService.getPhotoUrl(photoAgrandie)}
            alt="Aperçu agrandi"
            className="max-w-full max-h-full object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            onClick={() => setPhotoAgrandie(null)}
            className="absolute top-4 right-4 text-white bg-white/10 hover:bg-white/20 rounded-full p-2 transition-all"
          >
            <X size={24} />
          </button>
        </div>
      )}
    </div>
  );
}

export default function PreparerAnnonces() {
  const [watcherActif, setWatcherActif] = useState(false);
  const [scanInitialEnCours, setScanInitialEnCours] = useState(false);
  const [photosEnAttente, setPhotosEnAttente] = useState(0);
  const [lots, setLots] = useState([]);
  const [toggling, setToggling] = useState(false);
  const [vidage, setVidage] = useState(false);
  const [erreur, setErreur] = useState(null);
  const [statutBrouillons, setStatutBrouillons] = useState({ en_cours: false, lot_actuel: 0, total_lots: 0, dernier_message: "" });
  const [lancementEnCours, setLancementEnCours] = useState(false);
  const [validationGlobaleEnCours, setValidationGlobaleEnCours] = useState(false);
  const racineRef = useRef(null);

  const [modeVrac, setModeVrac] = useState(false);
  const [slots, setSlots] = useState(() => Array.from({ length: NB_SLOTS_VRAC }, () => []));
  const [slotsEnCours, setSlotsEnCours] = useState({});
  const [slotsErreurs, setSlotsErreurs] = useState({});
  const [slotsDressing, setSlotsDressing] = useState(() => Array.from({ length: NB_SLOTS_VRAC }, () => 'Dressing 1'));
  // Item en cours de glissement ENTRE deux zones (pas un drag OS) -- un objet
  // File ne peut pas être sérialisé dans dataTransfer, donc on le garde ici
  // plutôt que dans le payload de drag (lu de façon synchrone au drop, même
  // page/contexte JS).
  const itemDragueRef = useRef(null);

  const ajouterFichiersDansSlot = (slotIndex, fileList) => {
    const nouvelles = Array.from(fileList)
      .filter((f) => f.type.startsWith('image/'))
      .map((file) => ({ file, previewUrl: URL.createObjectURL(file) }));
    if (nouvelles.length === 0) return;
    setSlots((prev) => {
      const suivant = prev.map((s) => [...s]);
      suivant[slotIndex] = [...suivant[slotIndex], ...nouvelles];
      return suivant;
    });
  };

  const demarrerDragSlot = (e, slotIndex, itemIndex) => {
    itemDragueRef.current = { slotIndex, itemIndex };
    // Requis par certains navigateurs (Firefox) pour autoriser le drag même
    // sans donnée réellement utile transmise via dataTransfer.
    e.dataTransfer.setData('text/plain', '');
  };

  const deposerDansSlot = (e, slotIndex) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      ajouterFichiersDansSlot(slotIndex, e.dataTransfer.files);
      return;
    }
    const origine = itemDragueRef.current;
    itemDragueRef.current = null;
    if (!origine || origine.slotIndex === slotIndex) return;
    setSlots((prev) => {
      const suivant = prev.map((s) => [...s]);
      const [item] = suivant[origine.slotIndex].splice(origine.itemIndex, 1);
      if (item) suivant[slotIndex] = [...suivant[slotIndex], item];
      return suivant;
    });
  };

  const retirerDuSlot = (slotIndex, itemIndex) => {
    setSlots((prev) => {
      const suivant = prev.map((s) => [...s]);
      const [item] = suivant[slotIndex].splice(itemIndex, 1);
      if (item) URL.revokeObjectURL(item.previewUrl);
      return suivant;
    });
  };

  const validerSlot = async (slotIndex) => {
    const items = slots[slotIndex];
    if (items.length === 0) return;

    setSlotsEnCours((prev) => ({ ...prev, [slotIndex]: true }));
    setSlotsErreurs((prev) => ({ ...prev, [slotIndex]: null }));
    try {
      await preparerAnnoncesService.creerLotDepuisFichiers(items.map((i) => i.file), slotsDressing[slotIndex]);

      items.forEach((i) => URL.revokeObjectURL(i.previewUrl));
      setSlots((prev) => {
        const suivant = prev.map((s) => [...s]);
        suivant[slotIndex] = [];
        return suivant;
      });
      await rafraichirLots();
    } catch (e) {
      setSlotsErreurs((prev) => ({ ...prev, [slotIndex]: e.message || "Échec de la création du lot." }));
    } finally {
      setSlotsEnCours((prev) => ({ ...prev, [slotIndex]: false }));
    }
  };

  const nbLotsAValider = lots.filter((l) => l.status === 'pending_validation').length;

  // Auto-scroll du conteneur de la page quand on drag un groupe de photos
  // (déplacement entre lots) et qu'on approche du haut/bas de l'écran --
  // sinon impossible d'atteindre un lot situé hors du viewport pendant le drag.
  useEffect(() => {
    const conteneur = { current: null };
    let dernierY = null;
    let enCoursDeDrag = false;
    let rafId = null;

    const boucleAutoScroll = () => {
      if (enCoursDeDrag && dernierY !== null && conteneur.current) {
        const rect = conteneur.current.getBoundingClientRect();
        const distanceBas = rect.bottom - dernierY;
        const distanceHaut = dernierY - rect.top;

        if (distanceBas >= 0 && distanceBas < AUTOSCROLL_ZONE_PX) {
          const intensite = 1 - distanceBas / AUTOSCROLL_ZONE_PX;
          conteneur.current.scrollTop += intensite * AUTOSCROLL_VITESSE_MAX_PX;
        } else if (distanceHaut >= 0 && distanceHaut < AUTOSCROLL_ZONE_PX) {
          const intensite = 1 - distanceHaut / AUTOSCROLL_ZONE_PX;
          conteneur.current.scrollTop -= intensite * AUTOSCROLL_VITESSE_MAX_PX;
        }
      }
      rafId = requestAnimationFrame(boucleAutoScroll);
    };

    const gererDragOver = (e) => {
      if (!enCoursDeDrag) {
        enCoursDeDrag = true;
        conteneur.current = trouverConteneurScrollable(racineRef.current);
      }
      dernierY = e.clientY;
    };

    const arreterAutoScroll = () => {
      enCoursDeDrag = false;
      dernierY = null;
    };

    document.addEventListener('dragover', gererDragOver);
    document.addEventListener('dragend', arreterAutoScroll);
    document.addEventListener('drop', arreterAutoScroll);
    rafId = requestAnimationFrame(boucleAutoScroll);

    return () => {
      document.removeEventListener('dragover', gererDragOver);
      document.removeEventListener('dragend', arreterAutoScroll);
      document.removeEventListener('drop', arreterAutoScroll);
      cancelAnimationFrame(rafId);
    };
  }, []);

  const handleValiderTout = async () => {
    if (!window.confirm(`Valider les ${nbLotsAValider} lot(s) en attente et lancer la génération Gemini pour chacun ?`)) {
      return;
    }
    setValidationGlobaleEnCours(true);
    setErreur(null);
    try {
      await preparerAnnoncesService.validerTousLesLots();
    } catch (e) {
      setErreur(e.message);
    } finally {
      setValidationGlobaleEnCours(false);
    }
  };

  const rafraichirStatut = useCallback(async () => {
    try {
      const statut = await preparerAnnoncesService.getWatcherStatus();
      setWatcherActif(statut.actif);
      setScanInitialEnCours(statut.scan_initial_en_cours);
      setPhotosEnAttente(statut.photos_en_attente);
    } catch (e) {
      // Silencieux -- ne pas spammer l'utilisateur si un poll échoue ponctuellement
    }
  }, []);

  const rafraichirLots = useCallback(async () => {
    try {
      const data = await preparerAnnoncesService.getLots(50);
      setLots(data);
    } catch (e) {
      // Idem, silencieux sur un échec de poll isolé
    }
  }, []);

  const rafraichirStatutBrouillons = useCallback(async () => {
    try {
      const statut = await preparerAnnoncesService.getStatutBrouillons();
      setStatutBrouillons(statut);
    } catch (e) {
      // Silencieux -- même logique que les autres polls
    }
  }, []);

  useEffect(() => {
    rafraichirStatut();
    rafraichirLots();
    rafraichirStatutBrouillons();

    const interval = setInterval(() => {
      rafraichirStatut();
      rafraichirLots();
      rafraichirStatutBrouillons();
    }, INTERVALLE_POLLING_MS);

    return () => clearInterval(interval);
  }, [rafraichirStatut, rafraichirLots, rafraichirStatutBrouillons]);

  const handleLancerBrouillons = async () => {
    if (!window.confirm(
      "Chaque lot utilise le profil Chrome de son dressing (choisi sur sa carte), partagé avec " +
      "l'automatisation Clemz. Si une republication ou baisse de prix est en cours sur ce dressing, " +
      "la création de ses brouillons attendra qu'elle se termine avant de démarrer.\n\n" +
      "Lancer la création des brouillons maintenant ?"
    )) {
      return;
    }
    setLancementEnCours(true);
    setErreur(null);
    try {
      await preparerAnnoncesService.lancerCreationBrouillons();
      await rafraichirStatutBrouillons();
    } catch (e) {
      setErreur(e.message);
    } finally {
      setLancementEnCours(false);
    }
  };

  const handleToggle = async () => {
    setToggling(true);
    setErreur(null);
    try {
      if (watcherActif) {
        await preparerAnnoncesService.stopWatcher();
      } else {
        await preparerAnnoncesService.startWatcher();
      }
      await rafraichirStatut();
    } catch (e) {
      setErreur(e.message);
    } finally {
      setToggling(false);
    }
  };

  const handleClearHistory = async () => {
    if (!window.confirm(`Supprimer les ${lots.length} lot(s) de l'historique ? Les photos archivées sur le disque ne seront pas touchées, seulement l'affichage du dashboard.`)) {
      return;
    }
    setVidage(true);
    setErreur(null);
    try {
      await preparerAnnoncesService.clearHistory();
      await rafraichirLots();
    } catch (e) {
      setErreur(e.message);
    } finally {
      setVidage(false);
    }
  };

  return (
    <div className="p-8" ref={racineRef}>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-black text-slate-800">Préparer Annonces</h1>
          <p className="text-sm text-slate-400 mt-1">
            Détection automatique des photos et génération des brouillons Vinted.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {nbLotsAValider > 0 && (
            <button
              onClick={handleValiderTout}
              disabled={validationGlobaleEnCours}
              className="flex items-center gap-1.5 text-xs font-semibold text-white bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 px-3 py-2 rounded-lg transition-colors"
            >
              <CheckSquare size={14} />
              {validationGlobaleEnCours ? 'Lancement...' : `Valider tout (${nbLotsAValider})`}
            </button>
          )}

                    {lots.some((l) => l.status === 'generation_ok') && (
            <button
              onClick={handleLancerBrouillons}
              disabled={lancementEnCours || statutBrouillons.en_cours}
              className="flex items-center gap-1.5 text-xs font-semibold text-white bg-slate-500 hover:bg-slate-600 disabled:opacity-50 px-3 py-2 rounded-lg transition-colors"
            >
              {statutBrouillons.en_cours
                ? `⏳ Lot ${statutBrouillons.lot_actuel}/${statutBrouillons.total_lots}...`
                : 'Créer les brouillons'}
            </button>
          )}

          {lots.length > 0 && (
            <button
              onClick={handleClearHistory}
              disabled={vidage}
              className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-red-600 disabled:opacity-50 px-3 py-2 rounded-lg hover:bg-red-50 transition-colors"
            >
              <Trash2 size={14} /> Vider l'historique
            </button>
          )}

          <button
            onClick={() => setModeVrac((v) => !v)}
            className={`flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg transition-colors ${
              modeVrac
                ? 'bg-orange-500 text-white hover:bg-orange-600'
                : 'bg-white text-slate-500 border border-slate-200 hover:border-orange-300 hover:text-orange-600'
            }`}
          >
            <LayoutGrid size={14} />
            {modeVrac ? 'Quitter le regroupement manuel' : 'Regrouper manuellement'}
          </button>

        <div className="flex items-center gap-4 bg-white rounded-xl border border-slate-100 shadow-sm px-5 py-3">
          <div className="text-right">
            <p className="text-sm font-semibold text-slate-700">
              Écouteur {watcherActif ? 'actif' : 'inactif'}
            </p>
            {scanInitialEnCours && (
              <p className="text-xs text-cyan-600 font-medium">🔍 Analyse des photos en cours...</p>
            )}
            {watcherActif && !scanInitialEnCours && (
              <p className="text-xs text-slate-400">{photosEnAttente} photo(s) en attente</p>
            )}
          </div>
          <ToggleSwitch actif={watcherActif} onToggle={handleToggle} disabled={toggling} />
        </div>
        </div>
      </div>

      {erreur && (
        <div className="mb-6 bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-lg">
          {erreur}
        </div>
      )}

      {modeVrac ? (
        <>
          <p className="mb-4 text-xs text-slate-400">
            Glisse des photos directement depuis l'explorateur de fichiers dans une zone ci-dessous.
            Valide une zone pour créer le lot -- elle se vide aussitôt et redevient disponible.
          </p>

          <div className="grid grid-cols-3 gap-4">
            {slots.map((slotItems, i) => (
              <div
                key={i}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => deposerDansSlot(e, i)}
                className={`flex flex-col bg-white border-2 border-dashed rounded-xl p-3 min-h-[220px] transition-colors ${
                  slotItems.length > 0 ? 'border-slate-300' : 'border-slate-200'
                }`}
              >
                <div className="flex-1 flex flex-wrap content-start gap-1.5">
                  {slotItems.length === 0 ? (
                    <div className="w-full flex-1 flex items-center justify-center text-slate-300 text-xs py-8 text-center px-2">
                      Dépose des photos ici
                    </div>
                  ) : (
                    slotItems.map((item, itemIndex) => (
                      <div key={item.previewUrl} className="relative w-14 h-14">
                        <img
                          draggable="true"
                          onDragStart={(e) => demarrerDragSlot(e, i, itemIndex)}
                          src={item.previewUrl}
                          alt=""
                          className="w-14 h-14 object-cover rounded-md cursor-grab active:cursor-grabbing"
                        />
                        <button
                          onClick={() => retirerDuSlot(i, itemIndex)}
                          className="absolute -top-1.5 -right-1.5 bg-slate-700 hover:bg-red-600 text-white rounded-full w-4 h-4 flex items-center justify-center"
                        >
                          <X size={10} />
                        </button>
                      </div>
                    ))
                  )}
                </div>
                <select
                  value={slotsDressing[i]}
                  onChange={(e) => {
                    const valeur = e.target.value;
                    setSlotsDressing((prev) => {
                      const suivant = [...prev];
                      suivant[i] = valeur;
                      return suivant;
                    });
                  }}
                  disabled={slotsEnCours[i]}
                  className="mt-2 w-full text-[11px] font-medium border border-slate-200 rounded-lg px-2 py-1 text-slate-600 disabled:opacity-50"
                >
                  <option value="Dressing 1">Dressing 1</option>
                  <option value="Dressing 2">Dressing 2</option>
                </select>
                <button
                  onClick={() => validerSlot(i)}
                  disabled={slotItems.length === 0 || slotsEnCours[i]}
                  className="mt-1.5 flex items-center justify-center gap-1.5 text-xs font-bold text-white bg-emerald-500 hover:bg-emerald-600 disabled:opacity-30 disabled:cursor-not-allowed px-3 py-1.5 rounded-lg transition-colors"
                >
                  <CheckSquare size={13} />
                  {slotsEnCours[i] ? 'Création...' : `Valider (${slotItems.length})`}
                </button>
                {slotsErreurs[i] && <p className="text-[10px] text-red-500 mt-1">{slotsErreurs[i]}</p>}
              </div>
            ))}
          </div>
        </>
      ) : lots.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-slate-300">
          <Camera size={48} />
          <p className="mt-4 text-sm text-slate-400">
            {watcherActif
              ? "En attente de tes premières photos..."
              : "Active l'écouteur pour commencer à détecter tes photos."}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {lots.map((lot) => (
            <LotCard
              key={lot.id}
              lot={lot}
              allLots={lots}
              onRefresh={rafraichirLots}
              statutBrouillons={statutBrouillons}
              onRafraichirStatutBrouillons={rafraichirStatutBrouillons}
            />
          ))}
        </div>
      )}
    </div>
  );
}