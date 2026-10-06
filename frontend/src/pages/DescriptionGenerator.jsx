import React, { useState, useRef, useEffect } from 'react';
import { Upload, Copy, Sparkles, X,AlertTriangle } from 'lucide-react';
import toast, { Toaster } from 'react-hot-toast';
import PriceEstimationPanel from '../components/PriceEstimationPanel';

export default function DescriptionGenerator() {
    const [productsFiles, setProductsFiles] = useState([[], [], [], [], []]);
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(false);
    
    const fileInputRefs = [useRef(null), useRef(null), useRef(null), useRef(null), useRef(null)];

    // Nettoyage des URLs de prévisualisation pour éviter les fuites mémoire
    useEffect(() => {
        return () => {
            productsFiles.flat().forEach(file => {
                if (file.preview) URL.revokeObjectURL(file.preview);
            });
        };
    }, []);

    const handleFileChange = (e, productIndex) => {
        const selectedFiles = Array.from(e.target.files).map(file => {
            // On attache la prévisualisation directement à l'objet file
            file.preview = URL.createObjectURL(file);
            return file;
        });

        setProductsFiles(prev => {
            const newFiles = [...prev];
            newFiles[productIndex] = [...newFiles[productIndex], ...selectedFiles];
            return newFiles;
        });
    };

    const removeFile = (productIndex, fileIndex) => {
        setProductsFiles(prev => {
            const newFiles = [...prev];
            const fileToRemove = newFiles[productIndex][fileIndex];
            if (fileToRemove.preview) URL.revokeObjectURL(fileToRemove.preview);
            
            newFiles[productIndex] = newFiles[productIndex].filter((_, i) => i !== fileIndex);
            return newFiles;
        });
    };

    const handleGenerateBatch = async () => {
        const totalFiles = productsFiles.reduce((acc, curr) => acc + curr.length, 0);
        if (totalFiles === 0) return toast.error("Ajoute des photos !");

        setLoading(true);
        const data = new FormData();
        productsFiles.forEach((files, index) => {
            files.forEach(file => data.append(`product_${index}`, file));
        });

        try {
            const response = await fetch('http://localhost:8000/api/generate-description-batch', {
                method: 'POST',
                body: data
            });

            if (!response.ok) throw new Error();
            const out = await response.json();
            
            const splitDescriptions = out.descriptions
                .split('---')
                .map(desc => desc.trim())
                .filter(desc => desc.length > 20);
            
            setResults(splitDescriptions);
            toast.success("Annonces générées !");
        } catch (err) {
            toast.error("Échec de la génération");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="p-6 max-w-7xl mx-auto">
            <Toaster position="bottom-right" />
            
            <div className="mb-8 flex justify-between items-end">
                <div>
                    <h1 className="text-2xl font-black tracking-tight text-slate-800 flex items-center gap-3">
                        <Sparkles className="text-indigo-500" size={32} />
                        GÉNÉRATEUR 5 ZONES
                    </h1>
                </div>
                
                <button 
                    onClick={handleGenerateBatch} disabled={loading}
                    className={`px-8 py-3 rounded-lg font-black uppercase tracking-widest transition-all shadow-lg ${
                        loading ? 'bg-slate-100 text-slate-400' : 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-indigo-200'
                    }`}
                >
                    {loading ? "GÉNÉRATION..." : "LANCER L'IA"}
                </button>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
                {/* ZONES DE DÉPÔT */}
                <div className="space-y-4">
                    {productsFiles.map((files, productIndex) => (
                        <div key={productIndex} className="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                            <div className="flex justify-between items-center mb-3 text-xs font-black uppercase">
                                <span>Produit {productIndex + 1}</span>
                                <span className="text-slate-400">{files.length} photos</span>
                            </div>

                            <div className="flex flex-wrap gap-2">
                                {files.map((file, fileIndex) => (
                                    <div key={fileIndex} className="relative w-16 h-16 rounded-lg overflow-hidden group border border-slate-100">
                                        <img src={file.preview} alt="preview" className="w-full h-full object-cover" />
                                        <button onClick={() => removeFile(productIndex, fileIndex)} className="absolute inset-0 bg-red-500/80 text-white opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                                            <X size={14} />
                                        </button>
                                    </div>
                                ))}
                                
                                <button 
                                    onClick={() => fileInputRefs[productIndex].current.click()}
                                    className="w-16 h-16 rounded-lg border-2 border-dashed border-indigo-200 flex items-center justify-center text-indigo-400 hover:bg-indigo-50 bg-slate-50"
                                >
                                    <Upload size={16} />
                                </button>
                                <input type="file" multiple hidden ref={fileInputRefs[productIndex]} onChange={(e) => handleFileChange(e, productIndex)} accept="image/*" />
                            </div>
                        </div>
                    ))}
                </div>

                {/* RÉSULTATS */}
                <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 min-h-[600px] flex flex-col">
                    {loading ? (
                        <div className="flex-grow flex flex-col items-center justify-center gap-4 text-indigo-300 uppercase text-[10px] font-black tracking-widest">
                            <div className="w-10 h-10 border-4 border-t-indigo-500 rounded-full animate-spin"></div>
                            Analyse Gemini en cours...
                        </div>
                    ) : results.length > 0 ? (
                        <div className="space-y-6 overflow-y-auto pr-2">
                            {results.map((fullText, i) => {
                                const [mainContent, pricingPart] = fullText.split('[PRICING]');
                                const [description, vigilance] = mainContent.split('⚠️ POINTS DE VIGILANCE');
                                return (
                                    <div key={i} className="border border-slate-200 rounded-xl overflow-hidden shadow-sm">
                                        <div className="p-5 bg-white">
                                            <div className="flex justify-between items-start mb-4">
                                                <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Annonce {i + 1}</span>
                                                <button onClick={() => { navigator.clipboard.writeText(description.trim()); toast.success("Copié !"); }} className="flex items-center gap-2 px-3 py-1 bg-indigo-50 text-indigo-600 rounded-lg text-xs font-bold">
                                                    <Copy size={12} /> Copier
                                                </button>
                                            </div>
                                            <pre className="whitespace-pre-wrap text-sm font-normal leading-relaxed">{description.trim()}</pre>
                                            {vigilance && (
                                                <div className="mt-4 p-3 bg-amber-50 border-l-4 border-amber-500 rounded text-amber-900">
                                                    <div className="flex items-center gap-2 mb-1">
                                                        <AlertTriangle size={16} className="text-amber-600" />
                                                        <span className="text-[10px] font-black uppercase tracking-widest">Alerte Expert</span>
                                                    </div>
                                                    <p className="text-xs font-medium italic">⚠️ POINTS DE VIGILANCE {vigilance.trim()}</p>
                                                </div>
                                            )}
                                        </div>
                                        {pricingPart && (
                                            <div className="bg-emerald-50 border-t border-emerald-100 p-4">
                                                <div className="grid grid-cols-2 gap-4">
                                                    <div className="bg-white p-2 rounded-lg border border-emerald-100">
                                                        <p className="text-[8px] text-emerald-600 uppercase font-bold">Revente</p>
                                                        <p className="text-md font-black text-emerald-900">{pricingPart.match(/POTENTIEL : (.*?) €/i)?.[1] || "---"} €</p>
                                                    </div>
                                                    <div className="bg-emerald-600 p-2 rounded-lg text-white">
                                                        <p className="text-[8px] text-emerald-100 uppercase font-bold">Mise en ligne</p>
                                                        <p className="text-md font-black">{pricingPart.match(/CONSEILLÉ : (.*?) €/i)?.[1] || "---"} €</p>
                                                    </div>
                                                    {/* 2. LE BLOC DE JUSTIFICATION (Ce qui manquait) */}
                                                    <div className="mt-2 p-3 bg-white/50 rounded-lg border border-dashed border-emerald-200">
                                                        <p className="text-[11px] text-emerald-900 leading-relaxed font-medium italic">
                                                            {/* On affiche tout le texte brut pour être sûr de ne rien rater */}
                                                            {pricingPart.trim()}
                                                        </p>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ); // <-- La parenthèse et le point-virgule manquants étaient ici
                            })}
                        </div>
                    ) : (
                        <div className="flex-grow flex flex-col items-center justify-center text-slate-300 gap-4 uppercase text-[10px] font-black">
                            <Sparkles size={48} className="opacity-20" />
                            Attente de génération...
                        </div>
                    )}
                </div>
                {/* ESTIMATION DE PRIX */}
                <PriceEstimationPanel />
            </div>
        </div>
    );
}