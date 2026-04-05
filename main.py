"""
Le Centre F - Backend API pour l'Assistant IA Formation
========================================================
Architecture RAG simplifiée :
1. Chunks pré-indexés depuis les supports PDF/PPTX/DOCX (fichier JSON embarqué)
2. Recherche par mots-clés (BM25-style) - pas besoin de GPU ni d'embeddings
3. Génération : API Claude avec contexte + sources
"""

import os
import json
import gzip
import hashlib
import math
import re
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from collections import Counter

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# ============================================================
# CONFIGURATION
# ============================================================

class Settings(BaseSettings):
    anthropic_api_key: str = "sk-ant-VOTRE-CLE-ICI"
    claude_model: str = "claude-sonnet-4-20250514"
    top_k_results: int = 5

    # Auth
    secret_key: str = "CHANGEZ-MOI-EN-PRODUCTION-clef-secrete-64-chars"
    access_token_expire_minutes: int = 1440  # 24h

    class Config:
        env_file = ".env"

settings = Settings()


# ============================================================
# CHUNKS DE SECOURS (intégrés au code)
# ============================================================

FALLBACK_CHUNKS = {
    "001": [
        {
            "s": "Autorisation de détention.pdf",
            "p": 2,
            "c": "Page 2/11 Direction générale de la sûreté nucléaire et de la radioprotection6, place du Colonel Bourgoin - 75572 Paris Cedex 12 www.asn.gouv.frAUTORISATION POUR LA DÉTECTION DE PLOMB DANS LES PEINTURE"
        },
        {
            "s": "NF X 46-030.pdf",
            "p": 22,
            "c": "— 21 — NF X 46-030 4 Présentation des résultats Afin de faciliter la localisation des me sures, l'auteur du constat divise chaque local en plusieurs zones, auxquelles il attribue une lettre (A, B, C …"
        },
        {
            "s": "Autorisation de détention.pdf",
            "p": 6,
            "c": "détenus et , pour chacun d’eux, leur localisation. 11 - Un document (étude de poste…) présentant une estimation de la dose efficace annuelle qui sera reçue par le travailleur le plus exposé, les doses"
        }
    ],
    "002": [
        {
            "s": "amiante-protection-travailleurs (1).pdf",
            "p": 2,
            "c": "Mise à jour 2 mai 2016 Page 2 SOMMAIRE Introduction ................................ ................................ ................................ ............... 4 Décret n° 2012 -639 du 4 mai 2"
        },
        {
            "s": "GUIDE_amiante_donneurs_d_ordre.pdf",
            "p": 32,
            "c": "323 arrêté du 19 août 2011 relatif aux conditions d’accréditation des organismes procédant aux mesures d’empoussièrement en fibres d’amiante dans les immeubles bâtis, et arrêté du 14 août 2012 relatif"
        },
        {
            "s": "GUIDE_amiante_donneurs_d_ordre.pdf",
            "p": 7,
            "c": "Haut Conseil de la santé publique, l’amiante pourrait en- traîner entre 68 000 et 100 000 décès par cancer en France, de 2009 à 2050, et aurait été à l’origine de 61 300 à 118 400 décès entre 1955 et "
        }
    ],
    "003": [
        {
            "s": "AMAIANTE DTA 21 12 2012.pdf",
            "p": 5,
            "c": "30 décembre 2012 JOURNAL OFFICIEL DE LA RÉPUBLIQUE FRANÇAISE Texte 51 sur 168 . .ANNEXE II MODÈLE DE FICHE RÉCAPITULATIVE DU DOSSIER TECHNIQUE « AMIANTE » Cette fiche présente les informations minimal"
        },
        {
            "s": "AMIANTE LISTE C 12 12 2012.pdf",
            "p": 3,
            "c": "6 juillet 2013 JOURNAL OFFICIEL DE LA RÉPUBLIQUE FRANÇAISE Texte 14 sur 134 . .9oLes plans ou croquis à jour permettant de localiser les matériaux et produits contenant de l’amiante ; 10oLa signature "
        },
        {
            "s": "001 SUPPORT DE FORMATION A DIFFUSER.pptx",
            "p": 12,
            "c": "Le CENTRE F AMIANTE MENTION 202101 REV 03 12 Commanditaire toute personne physique ou morale qui commande l’opération d’examen visuel externe. Il s’agit, généralement, du ou des propriétaires, du synd"
        }
    ],
    "004": [
        {
            "s": "Ccorrigé exercice 5 lot autre d'habitati",
            "p": 3,
            "c": "ANZ FORMATION | 9 ruelle du maitre d'école 77500 CHELLES | Tél. : 0663573165 N°SIREN : 948520630 | Compagnie d'assurance : KLARITY n° CDIAGK001066 3/4 Dossier 24/IMO/0125 Rapport du : 12/06/2024Diagno"
        },
        {
            "s": "DPE sans mention 2024 REV 00.pptx",
            "p": 694,
            "c": "En termes juridiques, un immeuble est un bien non susceptible d'être déplacé. Il peut donc s'agir d'un bâtiment mais également d'une maison, d'un terrain, d'une propriété agricole… Un bien qui ne peut"
        },
        {
            "s": "DPE sans mention 2024 REV 00.pptx",
            "p": 493,
            "c": "Le Système Split Cette autre version se compose de deux blocs indépendants. Le premier correspond à l’unité intérieure et a pour rôle de rafraîchir les lieux, il sera donc installé dans la pièce souha"
        }
    ],
    "005": [
        {
            "s": "corrigé exercice 3 Usage autre qu'habita",
            "p": 3,
            "c": "ANZ FORMATION | 9 ruelle du maitre d'école 77500 CHELLES | Tél. : 0663573165 N°SIREN : 948520630 | Compagnie d'assurance : KLARITY n° CDIAGK001066 3/4 Dossier 24/IMO/0127 Rapport du : 12/06/2024Diagno"
        },
        {
            "s": "Plans maison Clos des Bleuets.pdf",
            "p": 5,
            "c": "HAUTEUR maxi FAITAGE / TN 4.68 mPENTE 35 %PIGNONS 0.40 MFACADES 0.40 MDEBORD DE TOITURE Plans non destinés à l'éxécution des travaux, mais réservés à l'obtention des autorisations administratives de c"
        },
        {
            "s": "QCM 1 ENERGIE MENTION CORRIGE.pdf",
            "p": 8,
            "c": "Une chaudière équipées de brûleurs à air pulsé 38) Le chauffage d'une CTA peut être assuré par :* Des batteries chaudes électriques Des batteries chaudes hydroliques Des aérothermes 39) Quelles sont l"
        }
    ],
    "006": [
        {
            "s": "NFP 03200.pdf",
            "p": 7,
            "c": "NF P 03- 200 5 Sommaire Introduction ................................................................................................................................................................ . "
        },
        {
            "s": "NFP 03200.pdf",
            "p": 14,
            "c": "NF P 03- 200 12  références cadastrales ;  n° des lots ; informations collectées auprès du donneur d'ordre relatives à des traitements antérieurs contre les agents de dégradations biologiques du boi"
        },
        {
            "s": "NFP 03201 (termites).pdf",
            "p": 22,
            "c": "NF P 03-201 ( P 03-201 ) Page 21 Bibliographie [1] NF P 03-200, Agents de dégradation biologique du bois – Constat de l'état parasitaire dans les immeubles bâtis et non bâtis. [2] FD P 20-651, Durabil"
        }
    ],
    "007": [
        {
            "s": "FD C 16-600.pdf",
            "p": 10,
            "c": "FD C 16 -600 − 8 − B.5 Fiche de contrôle N° 5 – Présence d’une LIAISON EQUIPOTENTIELLE supplémentaire (LES) dans chaque local contenant une baignoire ou une douche ...................................."
        },
        {
            "s": "NF C 15-100.pdf",
            "p": 21,
            "c": "NF C 15-100 Index - XII - 2002Courant différentiel -résiduel ..................... 233.7 Définition 411.5.1 Schéma TN 411.5.2 Schéma TT 531.2 Choix DDR Courant d'emploi ..............................."
        },
        {
            "s": "NF C 15-100.pdf",
            "p": 14,
            "c": "NF C 15-100 - V - 2002TABLEAU I CORRESPONDANCE ENTRE LA NORME NF C 15-100 ET LES PUBLICATIONS INTERNATIONALES Norme NF C 15-100 Document d'Harmonisation du CENELECPublication CEI TITRE 1 60364-1 TITRE"
        }
    ],
    "008": [
        {
            "s": "NF DTU 24 1 P1 MàJ 20.02.06 FS.pdf",
            "p": 10,
            "c": "— 9 — NF DTU 24.1 P1 Sommaire (suite) Page 12.4 Carneaux en béton ............................................................................................................. ............... 80 12.4."
        },
        {
            "s": "NF P 45-500.pdf",
            "p": 31,
            "c": "— 29 — NF P 45-500 Pour le cas des tiges après compteur et en maison individuelle, l’organe de coupure supplémentaire doit être accessible. La présence d’un dispositif de manœuvre doit être vérifiée. "
        },
        {
            "s": "NF P 45-500.pdf",
            "p": 17,
            "c": "— 15 — NF P 45-500 Annexe B (normative) Grille de contrôle (voir 4.2) Init numérotation des tableaux d’annexe [B]!!! Init numérotation des figures d’annexe [B]!!! Init numérotation des équations d’ann"
        }
    ],
    "009": [
        {
            "s": "TABLEAUX PARASITES.pdf",
            "p": 1,
            "c": "Pas de trous de sorties Souvent aspect feuilleté Trous de sorties + vermoulures dans ou sur le bois Trous de sorties + copeaux Pas de trous de sorties Aspect feuilleté ou Galeries ouvertes Catégories "
        },
        {
            "s": "GUIDE-PRATIQUE-DROM-COM-2022.pdf",
            "p": 15,
            "c": "Réglementation diagnostic & traitement Dans le neuf Les articles L 112-17 et R 112-2 à 4 du Code de la construction et de l’Habitation et leur arrêté d’application du 27 juin 2006 prévoient notamment "
        },
        {
            "s": "GUIDE-PRATIQUE-DROM-COM-2022.pdf",
            "p": 12,
            "c": "Une lutte efficace. Deux méthodes sous certification : le traitement au moyen de produits biocides et le traitement par la chaleur. Les techniques de préservation des bois en œuvre Chaque situation né"
        }
    ],
    "010": [
        {
            "s": "2020-06-08-RTG_guide_revJ (2).pdf",
            "p": 45,
            "c": "Comprendre et appliquer la RTG 2020 45 4.6.6.1 Caractéristiques thermiques, énergéti ques et lumineuses des baies et de leurs protections mobiles La RTG2020 introduit un modèle dynamique d’ouverture d"
        },
        {
            "s": "2020-06-08-RTG_guide_revJ (2).pdf",
            "p": 6,
            "c": "intégrée de man ière performantielle à la délibération du Calcul RTG au travers du nouvel indicateur PRECS ; • Plateforme de calcul RTG/DPEG : la région Guadeloupe met à disposition gratuitement un no"
        },
        {
            "s": "Cours DPEG-J1.pdf",
            "p": 20,
            "c": "Rtg 2020 – principes et évolutions Conditions de conformité Suppression des exigences minimales Approche 100% performantielle Art. 16 : étanchéité à l’air des baies performantiel Art. 17 : surface d’o"
        }
    ],
    "011": [
        {
            "s": "DTG PPPT  ITEM 3  DTG -PPPT.pptx",
            "p": 5,
            "c": "La loi ALUR ou loi Duflot II du 24 mars 2014 Analyse de l'état apparent des parties communes: Parties de bâtiments (couloirs, chaudière, canalisation, garde-corps...) et des terrains (jardins, parcs.."
        },
        {
            "s": "DTG PPPT  ITEM 2 COPROPRIETE.pptx",
            "p": 32,
            "c": "Carnet d’entretien Le carnet d'entretien doit mentionner au minimum les éléments suivants : Adresse de l'immeuble Identité de l'actuel syndic de copropriété Références des contrats d'assurance souscri"
        },
        {
            "s": "DTG PPPT ITEM 1 CONNAISSSANCE  DU BATI.p",
            "p": 32,
            "c": "Isolation ITE Les isolants naturels et écologiques Liège : Les panneaux de liège sont un choix écologique pour l’ITE. Ils sont durables, résistants aux intempéries et peuvent être fixés sur les murs a"
        }
    ]
}


# ============================================================
# MODELES PYDANTIC
# ============================================================

class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    module_id: str = Field(..., pattern=r"^\d{3}$")
    conversation_id: Optional[str] = None

class QuestionResponse(BaseModel):
    answer: str
    sources: list
    conversation_id: str
    module_id: str
    processing_time_ms: int


# ============================================================
# MODULES DE FORMATION
# ============================================================

MODULES = {
    "001": {"name": "Plomb (CREP)", "description": "Constat de Risque d'Exposition au Plomb"},
    "002": {"name": "Amiante sans mention", "description": "Diagnostic amiante - niveau de base"},
    "003": {"name": "Amiante avec mention", "description": "Diagnostic amiante - niveau avancé"},
    "004": {"name": "Énergie sans mention", "description": "DPE - Diagnostic de Performance Énergétique"},
    "005": {"name": "Énergie avec mention", "description": "DPE - niveau avancé (tertiaire/ERP)"},
    "006": {"name": "Termites Métropole", "description": "Diagnostic termites France métropolitaine"},
    "007": {"name": "Électricité", "description": "Diagnostic installation électrique"},
    "008": {"name": "Gaz", "description": "Diagnostic installation gaz"},
    "009": {"name": "Termites DROM", "description": "Diagnostic termites DOM-ROM"},
    "010": {"name": "DPEG", "description": "Diagnostic de Performance Énergétique Global"},
    "011": {"name": "DTG / PPT", "description": "Diagnostic Technique Global & Plan Pluriannuel de Travaux"},
}


# ============================================================
# RECHERCHE PAR MOTS-CLÉS (BM25-STYLE)
# ============================================================

# Stopwords français pour la recherche
STOPWORDS = set("le la les un une des de du d l à au aux en et ou mais si car ni ne pas que qui quoi dont où ce ces cette cet son sa ses leur leurs mon ma mes ton ta tes il elle on nous vous ils elles je tu me te se lui y a est sont été être avoir fait faire peut plus très tout tous toute toutes autre autres même aussi bien par pour avec sans dans sur entre chez vers quel quelle quels quelles comme comment quand encore déjà".split())

def tokenize(text: str) -> list[str]:
    """Tokenise un texte en mots normalisés."""
    text = text.lower()
    text = re.sub(r'[^a-zàâäéèêëïîôùûüÿçœæ0-9\s-]', ' ', text)
    words = text.split()
    return [w for w in words if w not in STOPWORDS and len(w) > 2]

class ChunkIndex:
    """Index de recherche BM25 sur les chunks pré-extraits."""

    def __init__(self):
        self.chunks = {}  # module_id -> list of chunks
        self.idf = {}     # module_id -> {term: idf_score}
        self.doc_tokens = {}  # module_id -> list of token lists
        self.avg_dl = {}  # module_id -> average doc length

    def load_from_json(self, filepath: str):
        """Charge les chunks depuis un fichier JSON (ou .json.gz)."""
        try:
            if filepath.endswith('.gz'):
                with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
        except Exception as e:
            print(f"ERREUR lors du chargement de {filepath}: {e}")
            print("Utilisation des chunks de secours intégrés...")
            data = FALLBACK_CHUNKS

        self._index_data(data)

    def load_from_dict(self, data: dict):
        """Charge les chunks depuis un dictionnaire Python."""
        self._index_data(data)

    def _index_data(self, data: dict):
        """Indexe les chunks depuis un dictionnaire."""
        for module_id, chunks in data.items():
            self.chunks[module_id] = chunks
            # Pré-calculer les tokens pour chaque chunk
            tokens_list = [tokenize(c["c"]) for c in chunks]
            self.doc_tokens[module_id] = tokens_list

            # Calculer IDF pour ce module
            n = len(chunks)
            if n == 0:
                continue
            df = Counter()
            for tokens in tokens_list:
                unique = set(tokens)
                for t in unique:
                    df[t] += 1
            self.idf[module_id] = {
                t: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
                for t, freq in df.items()
            }
            self.avg_dl[module_id] = sum(len(t) for t in tokens_list) / n

        total = sum(len(v) for v in self.chunks.values())
        print(f"Index chargé : {total} chunks pour {len(self.chunks)} modules")

    def search(self, query: str, module_id: str, top_k: int = 5) -> list[dict]:
        """Recherche BM25 des chunks les plus pertinents."""
        if module_id not in self.chunks or not self.chunks[module_id]:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        k1 = 1.5
        b = 0.75
        idf = self.idf.get(module_id, {})
        avg_dl = self.avg_dl.get(module_id, 1)
        tokens_list = self.doc_tokens[module_id]

        scores = []
        for i, doc_tokens in enumerate(tokens_list):
            dl = len(doc_tokens)
            tf = Counter(doc_tokens)
            score = 0.0
            for qt in query_tokens:
                if qt in tf:
                    freq = tf[qt]
                    idf_val = idf.get(qt, 0)
                    numerator = freq * (k1 + 1)
                    denominator = freq + k1 * (1 - b + b * dl / avg_dl)
                    score += idf_val * numerator / denominator
            if score > 0:
                scores.append((i, score))

        # Trier par score décroissant
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:top_k]:
            chunk = self.chunks[module_id][idx]
            # Normaliser le score entre 0 et 1
            max_score = scores[0][1] if scores else 1
            norm_score = round(score / max_score, 4) if max_score > 0 else 0
            results.append({
                "content": chunk["c"],
                "source": chunk["s"],
                "page": chunk["p"],
                "section": "",
                "similarity": norm_score
            })

        return results


# ============================================================
# GÉNÉRATION IA (Claude API)
# ============================================================

async def generate_answer(question: str, context_chunks: list[dict], module_name: str) -> dict:
    """Génère une réponse avec l'API Claude en mode RAG."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    if context_chunks:
        # Mode RAG : réponse basée sur les documents indexés
        context_parts = []
        for i, chunk in enumerate(context_chunks):
            source_info = f"[Source: {chunk['source']}, Page {chunk['page']}"
            if chunk.get('section'):
                source_info += f", Section: {chunk['section']}"
            source_info += f", Pertinence: {chunk['similarity']}]"
            context_parts.append(f"--- Extrait {i+1} {source_info} ---\n{chunk['content']}")

        context = "\n\n".join(context_parts)

        system_prompt = f"""Tu es l'assistant IA de formation du Centre F, spécialisé dans les diagnostics immobiliers.
Tu réponds aux questions des apprenants du module "{module_name}".

RÈGLES STRICTES :
1. Réponds en te basant PRINCIPALEMENT sur les extraits de documents fournis ci-dessous.
2. Tu peux compléter avec tes connaissances réglementaires si les extraits sont insuffisants, mais précise-le.
3. Cite TOUJOURS tes sources (nom du document, page) pour les informations issues des extraits.
4. Mentionne les textes réglementaires pertinents (arrêtés, normes NF, Code de la Santé Publique, etc.).
5. Utilise un langage professionnel mais accessible.
6. Structure ta réponse avec des paragraphes clairs.
7. Mets en gras les éléments clés avec **texte**.

EXTRAITS DES SUPPORTS DE FORMATION DU CENTRE F :
{context}"""
    else:
        # Mode connaissances générales (fallback)
        system_prompt = f"""Tu es l'assistant IA de formation du Centre F, spécialisé dans les diagnostics immobiliers.
Tu réponds aux questions des apprenants du module "{module_name}".

Réponds en te basant sur la réglementation française en vigueur concernant les diagnostics immobiliers.
Mentionne les textes réglementaires pertinents (arrêtés, normes NF, Code de la Santé Publique, etc.).
Utilise un langage professionnel mais accessible.
Structure ta réponse avec des paragraphes clairs.
Mets en gras les éléments clés avec **texte**."""

    message = client.messages.create(
        model=settings.claude_model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": question}]
    )

    answer_text = message.content[0].text

    # Extraire les sources utilisées (uniquement en mode RAG)
    sources = []
    if context_chunks:
        seen = set()
        for chunk in context_chunks:
            key = f"{chunk['source']}_{chunk['page']}"
            if key not in seen and chunk['similarity'] > 0.2:
                seen.add(key)
                source_type = "Support de formation"
                name = chunk['source'].lower()
                if "nf " in name or "norme" in name:
                    source_type = "Norme"
                elif "arrêté" in name or "décret" in name or "arreté" in name or "arrete" in name:
                    source_type = "Réglementation"
                elif "code" in name or "loi" in name:
                    source_type = "Loi"

                sources.append({
                    "document": chunk['source'],
                    "page": chunk['page'],
                    "section": chunk.get('section', ''),
                    "type": source_type,
                    "relevance": chunk['similarity']
                })

    return {"answer": answer_text, "sources": sources[:5]}


# ============================================================
# APPLICATION FASTAPI
# ============================================================

# Charger l'index au démarrage
chunk_index = ChunkIndex()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - charger les chunks
    print("Le Centre F - Assistant IA Backend v2.1")
    print(f"Modèle IA : {settings.claude_model}")

    # Chercher le fichier chunks
    loaded = False
    for path in ["chunks.json.gz", "chunks.json", "data/chunks.json.gz", "data/chunks.json"]:
        if os.path.exists(path):
            try:
                chunk_index.load_from_json(path)
                loaded = True
                break
            except Exception as e:
                print(f"Erreur avec {path}: {e}")
                continue

    if not loaded:
        print("Aucun fichier externe trouvé, chargement des chunks de secours...")
        chunk_index.load_from_dict(FALLBACK_CHUNKS)

    yield
    print("Arrêt du serveur...")

app = FastAPI(
    title="Le Centre F - Assistant IA Formation",
    description="API backend pour l'assistant IA de formation aux diagnostics immobiliers",
    version="2.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def root():
    total_chunks = sum(len(v) for v in chunk_index.chunks.values())
    return {
        "service": "Le Centre F - Assistant IA",
        "status": "online",
        "version": "2.1.0",
        "modules": len(MODULES),
        "chunks_indexés": total_chunks
    }

@app.get("/api/health")
async def health():
    total_chunks = sum(len(v) for v in chunk_index.chunks.values())
    return {"status": "ok", "chunks": total_chunks}

@app.get("/api/modules")
async def list_modules():
    """Liste tous les modules de formation disponibles."""
    result = []
    for k, v in MODULES.items():
        chunk_count = len(chunk_index.chunks.get(k, []))
        result.append({"id": k, **v, "chunk_count": chunk_count})
    return result

@app.post("/api/ask", response_model=QuestionResponse)
async def ask_question(req: QuestionRequest):
    """Pose une question à l'assistant IA sur un module."""
    import time
    start = time.time()

    if req.module_id not in MODULES:
        raise HTTPException(status_code=400, detail=f"Module {req.module_id} inconnu")

    module = MODULES[req.module_id]

    # 1. Recherche BM25 des chunks pertinents
    chunks = chunk_index.search(req.question, req.module_id, settings.top_k_results)

    # 2. Génération de la réponse avec Claude
    result = await generate_answer(req.question, chunks, module["name"])

    elapsed = int((time.time() - start) * 1000)

    return QuestionResponse(
        answer=result["answer"],
        sources=result["sources"],
        conversation_id=req.conversation_id or hashlib.md5(str(time.time()).encode()).hexdigest()[:12],
        module_id=req.module_id,
        processing_time_ms=elapsed
    )

@app.get("/api/stats")
async def get_stats():
    """Statistiques de la base de connaissances."""
    by_module = {}
    for mid in MODULES:
        count = len(chunk_index.chunks.get(mid, []))
        if count > 0:
            by_module[mid] = count
    return {
        "total_chunks": sum(by_module.values()),
        "modules_indexés": len(by_module),
        "by_module": by_module
    }


# ============================================================
# POINT D'ENTRÉE
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
