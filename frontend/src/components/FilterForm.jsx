// components/FilterForm.jsx
import React, { useState, useEffect, useMemo, useRef } from 'react';
import axios from 'axios';
import { X, Search, Save } from 'lucide-react';
import CustomCheckbox from './CustomCheckbox';

const API_URL = 'http://localhost:8000/api';

// --- Petit composant générique : champ recherche + liste cochable ---
const MultiSelectSearch = ({ label, options, selectedIds, onChange, required }) => {
    const [query, setQuery] = useState('');

    const filtered = useMemo(() => {
        if (!query.trim()) return options.slice(0, 50);
        const q = query.toLowerCase();
        return options.filter(o => o.label.toLowerCase().includes(q)).slice(0, 50);
    }, [query, options]);

    const toggle = (id) => {
        const idStr = String(id);
        onChange(
            selectedIds.includes(idStr)
                ? selectedIds.filter(i => i !== idStr)
                : [...selectedIds, idStr]
        );
    };

    return (
        <div className="mb-5">
            <label className="block text-[11px] font-black text-slate-500 uppercase tracking-wide mb-2">
                {label} {required && <span className="text-red-500">*</span>}
                {selectedIds.length > 0 && (
                    <span className="ml-2 text-cyan-600">({selectedIds.length} sélectionné{selectedIds.length > 1 ? 's' : ''})</span>
                )}
            </label>

            <div className="relative mb-2">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                    type="text"
                    placeholder={`Rechercher ${label.toLowerCase()}...`}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="w-full pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-400"
                />
            </div>

            <div className="max-h-48 overflow-y-auto border border-slate-100 rounded-xl divide-y divide-slate-50">
                {filtered.length === 0 && (
                    <p className="text-xs text-slate-400 italic p-3">Aucun résultat</p>
                )}
                {filtered.map((opt) => (
                    <label
                        key={opt.id}
                        className="flex items-center justify-between gap-3 px-3 py-2 hover:bg-slate-50 cursor-pointer text-sm"
                    >
                        <span className="flex items-center gap-2">
                            {opt.hex && (
                                <span
                                    className="w-3 h-3 rounded-full border border-slate-200 shrink-0"
                                    style={{ backgroundColor: `#${opt.hex}` }}
                                />
                            )}
                            {opt.label}
                        </span>
                        <CustomCheckbox
                            id={`opt-${label}-${opt.id}`}
                            checked={selectedIds.includes(String(opt.id))}
                            onChange={() => toggle(opt.id)}
                        />
                    </label>
                ))}
            </div>
        </div>
    );
};

// --- Aplatissement de l'arborescence catégories (Hommes uniquement) ---
const flattenHommesCategories = (categories) => {
    const result = [];
    const hommes = categories?.Hommes;
    if (!hommes) return result;

    const walk = (node, path) => {
        if (!node || typeof node !== 'object') return;
        const nid = node.id;
        if (nid && path) {
            result.push({ id: String(nid), label: path, sizeId: node.size_id ?? null });
        }
        const children = node.children;
        if (children && typeof children === 'object') {
            for (const [k, v] of Object.entries(children)) {
                walk(v, path ? `${path} > ${k}` : k);
            }
        }
        for (const [k, v] of Object.entries(node)) {
            if (!['id', 'slug', 'size_id', 'children'].includes(k) && typeof v === 'object' && v !== null) {
                walk(v, path ? `${path} > ${k}` : k);
            }
        }
    };

    walk(hommes, "Hommes");
    return result;
};

export default function FilterForm({ existingFilter, onClose, onSaved }) {
    const [referentiel, setReferentiel] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    const [nom, setNom] = useState(existingFilter?.nom || '');
    const [prixMax, setPrixMax] = useState(existingFilter?.prix_max || '');
    const [prixMin, setPrixMin] = useState(existingFilter?.prix_min || '');

    const [marqueIds, setMarqueIds] = useState(existingFilter?.marque_ids || []);
    const [categorieIds, setCategorieIds] = useState(existingFilter?.categorie_ids || []);
    const [tailleIds, setTailleIds] = useState(existingFilter?.taille_ids || []);
    const [couleurIds, setCouleurIds] = useState(existingFilter?.couleur_ids || []);
    const [etatIds, setEtatIds] = useState(existingFilter?.etat_ids || []);
    const [matiereIds, setMatiereIds] = useState(existingFilter?.matiere_ids || []);
    const [motifIds, setMotifIds] = useState(existingFilter?.motif_ids || []);

    useEffect(() => {
        const loadReferentiel = async () => {
            try {
                const res = await axios.get(`${API_URL}/sourcing/referentiel`);
                setReferentiel(res.data);
            } catch (err) {
                console.error('Erreur chargement référentiel', err);
                setError('Impossible de charger le référentiel Vinted.');
            } finally {
                setLoading(false);
            }
        };
        loadReferentiel();
    }, []);

    const marqueOptions = useMemo(() => {
        if (!referentiel?.marques) return [];
        return Object.entries(referentiel.marques).map(([nom, id]) => ({
            id: typeof id === 'object' ? id.id : id,
            label: nom,
        }));
    }, [referentiel]);

    const categorieOptions = useMemo(() => {
        if (!referentiel?.categories) return [];
        return flattenHommesCategories(referentiel.categories);
    }, [referentiel]);

    // Map catégorie id -> size_id (groupe de tailles associé), pour résoudre
    // dynamiquement les tailles en fonction des catégories cochées.
    const categorieSizeIdMap = useMemo(() => {
        const map = {};
        categorieOptions.forEach(opt => {
            if (opt.sizeId) map[opt.id] = opt.sizeId;
        });
        return map;
    }, [categorieOptions]);

    // Tailles : dépend du/des groupe(s) de tailles associés aux catégories cochées
    // (chaque catégorie a son propre size_id -- cf. flattenHommesCategories). Sans
    // catégorie sélectionnée, on ne sait pas quel groupe afficher.
    const tailleOptions = useMemo(() => {
        if (!referentiel?.tailles || categorieIds.length === 0) return [];

        const sizeGroupIds = new Set(
            categorieIds.map(cid => categorieSizeIdMap[cid]).filter(Boolean)
        );
        if (sizeGroupIds.size === 0) return [];

        // Fusionne les tailles de tous les groupes concernés (dédupliquées par id Vinted)
        const merged = {};
        for (const groupId of sizeGroupIds) {
            const group = referentiel.tailles[String(groupId)] || {};
            Object.entries(group).forEach(([label, id]) => { merged[id] = label; });
        }
        return Object.entries(merged).map(([id, label]) => ({ id: String(id), label }));
    }, [referentiel, categorieIds, categorieSizeIdMap]);

    const couleurOptions = useMemo(() => {
        if (!referentiel?.couleurs) return [];
        return Object.entries(referentiel.couleurs).map(([nom, info]) => ({
            id: info.id,
            label: nom,
            hex: info.hex,
        }));
    }, [referentiel]);

    const etatOptions = useMemo(() => {
        if (!referentiel?.etats) return [];
        return Object.entries(referentiel.etats).map(([nom, id]) => ({ id, label: nom }));
    }, [referentiel]);

    const matiereOptions = useMemo(() => {
        if (!referentiel?.matieres) return [];
        return Object.entries(referentiel.matieres).map(([nom, id]) => ({ id, label: nom }));
    }, [referentiel]);

    const motifOptions = useMemo(() => {
        if (!referentiel?.motifs) return [];
        return Object.entries(referentiel.motifs).map(([nom, id]) => ({ id, label: nom }));
    }, [referentiel]);

    const handleSubmit = async () => {
        setError(null);

        if (!nom.trim()) {
            setError('Le nom du filtre est obligatoire.');
            return;
        }
        if (categorieIds.length === 0) {
            setError('Le filtre doit cibler au moins une catégorie.');
            return;
        }

        const payload = {
            nom: nom.trim(),
            actif: true,
            marque_ids: marqueIds,
            categorie_ids: categorieIds,
            taille_ids: tailleIds,
            couleur_ids: couleurIds,
            etat_ids: etatIds,
            matiere_ids: matiereIds,
            motif_ids: motifIds,
            prix_max: prixMax ? parseFloat(prixMax) : null,
            prix_min: prixMin ? parseFloat(prixMin) : null,
        };

        setSaving(true);
        try {
            let res;
            if (existingFilter?.id) {
                res = await axios.put(`${API_URL}/sourcing/filters/${existingFilter.id}`, payload);
            } else {
                res = await axios.post(`${API_URL}/sourcing/filters`, payload);
            }

            if (res.data?.status === 'error') {
                setError(res.data.message || "Erreur lors de l'enregistrement.");
                return;
            }

            onSaved(res.data);
            onClose();
        } catch (err) {
            console.error('Erreur sauvegarde filtre', err);
            setError("Erreur lors de l'enregistrement du filtre.");
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
            <div className="bg-white rounded-3xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto p-8">
                <div className="flex items-center justify-between mb-6">
                    <h2 className="text-lg font-black text-slate-800">
                        {existingFilter ? 'Modifier le filtre' : 'Nouveau filtre de sourcing'}
                    </h2>
                    <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
                        <X size={20} />
                    </button>
                </div>

                {loading ? (
                    <p className="text-sm text-slate-400 italic">Chargement du référentiel Vinted...</p>
                ) : (
                    <>
                        <div className="mb-5">
                            <label className="block text-[11px] font-black text-slate-500 uppercase tracking-wide mb-2">
                                Nom du filtre <span className="text-red-500">*</span>
                            </label>
                            <input
                                type="text"
                                placeholder="Ex: Spécial Été"
                                value={nom}
                                onChange={(e) => setNom(e.target.value)}
                                className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-400"
                            />
                        </div>

                        <div className="grid grid-cols-2 gap-4 mb-5">
                            <div>
                                <label className="block text-[11px] font-black text-slate-500 uppercase tracking-wide mb-2">
                                    Prix minimum (€)
                                </label>
                                <input
                                    type="number"
                                    placeholder="Ex: 5"
                                    value={prixMin}
                                    onChange={(e) => setPrixMin(e.target.value)}
                                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-400"
                                />
                            </div>
                            <div>
                                <label className="block text-[11px] font-black text-slate-500 uppercase tracking-wide mb-2">
                                    Prix maximum (€)
                                </label>
                                <input
                                    type="number"
                                    placeholder="Ex: 15"
                                    value={prixMax}
                                    onChange={(e) => setPrixMax(e.target.value)}
                                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-400"
                                />
                            </div>
                        </div>

                        <MultiSelectSearch
                            label="Catégories (Hommes)"
                            options={categorieOptions}
                            selectedIds={categorieIds}
                            onChange={setCategorieIds}
                            required
                        />

                        <MultiSelectSearch
                            label="Marques"
                            options={marqueOptions}
                            selectedIds={marqueIds}
                            onChange={setMarqueIds}
                        />

                        <MultiSelectSearch
                            label="Tailles"
                            options={tailleOptions}
                            selectedIds={tailleIds}
                            onChange={setTailleIds}
                        />

                        <MultiSelectSearch
                            label="Couleurs"
                            options={couleurOptions}
                            selectedIds={couleurIds}
                            onChange={setCouleurIds}
                        />

                        <MultiSelectSearch
                            label="États"
                            options={etatOptions}
                            selectedIds={etatIds}
                            onChange={setEtatIds}
                        />

                        <MultiSelectSearch
                            label="Matières"
                            options={matiereOptions}
                            selectedIds={matiereIds}
                            onChange={setMatiereIds}
                        />

                        <MultiSelectSearch
                            label="Motifs"
                            options={motifOptions}
                            selectedIds={motifIds}
                            onChange={setMotifIds}
                        />

                        {error && (
                            <p className="text-xs text-red-600 font-bold bg-red-50 border border-red-100 rounded-xl px-3 py-2 mb-4">
                                {error}
                            </p>
                        )}

                        <button
                            onClick={handleSubmit}
                            disabled={saving}
                            className="w-full flex items-center justify-center gap-2 py-2 bg-slate-900 text-white rounded-lg font-black text-xs uppercase tracking-wide hover:bg-slate-800 disabled:opacity-50 transition-all"
                        >
                            <Save size={16} />
                            {saving ? 'Enregistrement...' : 'Enregistrer le filtre'}
                        </button>
                    </>
                )}
            </div>
        </div>
    );
}
