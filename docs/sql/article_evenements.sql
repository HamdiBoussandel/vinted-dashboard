-- Journal par article (28/10/2026) -- l'équivalent du "Journal" de Bleam.
-- Une ligne par événement de la vie d'un article : baisse, republication,
-- erreur Clemz, article écarté, vente... avec son origine et sa raison.
--
-- À exécuter UNE FOIS dans Supabase : SQL Editor > New query > coller > Run.
-- Le script ne touche à aucune table existante.

create table if not exists public.article_evenements (
    id           uuid primary key default gen_random_uuid(),
    article_id   uuid references public.articles(id) on delete set null,
    nom          text not null,               -- nom de l'article au moment de l'événement
    dressing     text,                        -- "Dressing 1" / "Dressing 2"
    type         text not null,               -- baisse, republication, suppression, erreur_clemz,
                                              -- ecarte, echec, vente, prix_change
    origine      text,                        -- prix_boucher, mauvaise_performance, panier_manuel,
                                              -- republication, validation_manuelle, ancien_script,
                                              -- liquidation, inconnue...
    prix_avant   numeric(10, 2),
    prix_apres   numeric(10, 2),
    detail       text,                        -- raison lisible ("−10 % (40 € → 36 €)", motif d'écart...)
    source       text not null,               -- d'où vient la ligne : price_drops, task_history, ventes
    source_ref   text not null unique,        -- clé de la ligne d'origine : rend l'import rejouable sans doublon
    survenu_le   timestamptz not null,        -- date réelle de l'événement
    cree_le      timestamptz not null default now()
);

create index if not exists article_evenements_article_idx
    on public.article_evenements (article_id, survenu_le desc);

-- Sécurité par ligne activée SANS règle : seul le backend (clé service_role,
-- qui passe outre) peut lire et écrire ; une clé publique (anon) n'a accès à rien.
alter table public.article_evenements enable row level security;
