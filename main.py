"""
Le Centre F - Backend API pour l'Assistant IA Formation
========================================================
Architecture RAG simplifiÃÂ©e :
1. Chunks prÃÂ©-indexÃÂ©s depuis les supports PDF/PPTX/DOCX (fichier JSON embarquÃÂ©)
2. Recherche par mots-clÃÂ©s (BM25-style) - pas besoin de GPU ni d'embeddings
3. GÃÂ©nÃÂ©ration : API Claude avec contexte + sources
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

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
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
# CHUNKS DE SECOURS (intÃÂ©grÃÂ©s au code)
# ============================================================

FALLBACK_CHUNKS = {
    "001": [
        {
            "s": "Autorisation de dÃÂ©tention.pdf",
            "p": 2,
            "c": "Page 2/11 Direction gÃÂ©nÃÂ©rale de la sÃÂ»retÃÂ© nuclÃÂ©aire et de la radioprotection6, place du Colonel Bourgoin - 75572 Paris Cedex 12 www.asn.gouv.frAUTORISATION POUR LA DÃÂTECTION DE PLOMB DANS LES PEINTURE"
        },
        {
            "s": "NF X 46-030.pdf",
            "p": 22,
            "c": "Ã¢ÂÂ 21 Ã¢ÂÂ NF X 46-030 4 PrÃÂ©sentation des rÃÂ©sultats Afin de faciliter la localisation des me sures, l'auteur du constat divise chaque local en plusieurs zones, auxquelles il attribue une lettre (A, B, C Ã¢ÂÂ¦"
        },
        {
            "s": "Autorisation de dÃÂ©tention.pdf",
            "p": 6,
            "c": "dÃÂ©tenus et , pour chacun dÃ¢ÂÂeux, leur localisation. 11 - Un document (ÃÂ©tude de posteÃ¢ÂÂ¦) prÃÂ©sentant une estimation de la dose efficace annuelle qui sera reÃÂ§ue par le travailleur le plus exposÃÂ©, les doses"
        }
    ],
    "002": [
        {
            "s": "amiante-protection-travailleurs (1).pdf",
            "p": 2,
            "c": "Mise ÃÂ  jour 2 mai 2016 Page 2 SOMMAIRE Introduction ................................ ................................ ................................ ............... 4 DÃÂ©cret nÃÂ° 2012 -639 du 4 mai 2"
        },
        {
            "s": "GUIDE_amiante_donneurs_d_ordre.pdf",
            "p": 32,
            "c": "323 arrÃÂªtÃÂ© du 19 aoÃÂ»t 2011 relatif aux conditions dÃ¢ÂÂaccrÃÂ©ditation des organismes procÃÂ©dant aux mesures dÃ¢ÂÂempoussiÃÂ¨rement en fibres dÃ¢ÂÂamiante dans les immeubles bÃÂ¢tis, et arrÃÂªtÃÂ© du 14 aoÃÂ»t 2012 relatif"
        },
        {
            "s": "GUIDE_amiante_donneurs_d_ordre.pdf",
            "p": 7,
            "c": "Haut Conseil de la santÃÂ© publique, lÃ¢ÂÂamiante pourrait en- traÃÂ®ner entre 68 000 et 100 000 dÃÂ©cÃÂ¨s par cancer en France, de 2009 ÃÂ  2050, et aurait ÃÂ©tÃÂ© ÃÂ  lÃ¢ÂÂorigine de 61 300 ÃÂ  118 400 dÃÂ©cÃÂ¨s entre 1955 et "
        }
    ],
    "003": [
        {
            "s": "AMAIANTE DTA 21 12 2012.pdf",
            "p": 5,
            "c": "30 dÃÂ©cembre 2012 JOURNAL OFFICIEL DE LA RÃÂPUBLIQUE FRANÃÂAISE Texte 51 sur 168 . .ANNEXE II MODÃÂLE DE FICHE RÃÂCAPITULATIVE DU DOSSIER TECHNIQUE ÃÂ« AMIANTE ÃÂ» Cette fiche prÃÂ©sente les informations minimal"
        },
        {
            "s": "AMIANTE LISTE C 12 12 2012.pdf",
            "p": 3,
            "c": "6 juillet 2013 JOURNAL OFFICIEL DE LA RÃÂPUBLIQUE FRANÃÂAISE Texte 14 sur 134 . .9oLes plans ou croquis ÃÂ  jour permettant de localiser les matÃÂ©riaux et produits contenant de lÃ¢ÂÂamiante ; 10oLa signature "
        },
        {
            "s": "001 SUPPORT DE FORMATION A DIFFUSER.pptx",
            "p": 12,
            "c": "Le CENTRE F AMIANTE MENTION 202101 REV 03 12 Commanditaire toute personne physique ou morale qui commande lÃ¢ÂÂopÃÂ©ration dÃ¢ÂÂexamen visuel externe. Il sÃ¢ÂÂagit, gÃÂ©nÃÂ©ralement, du ou des propriÃÂ©taires, du synd"
        }
    ],
    "004": [
        {
            "s": "CcorrigÃÂ© exercice 5 lot autre d'habitati",
            "p": 3,
            "c": "ANZ FORMATION | 9 ruelle du maitre d'ÃÂ©cole 77500 CHELLES | TÃÂ©l. : 0663573165 NÃÂ°SIREN : 948520630 | Compagnie d'assurance : KLARITY nÃÂ° CDIAGK001066 3/4 Dossier 24/IMO/0125 Rapport du : 12/06/2024Diagno"
        },
        {
            "s": "DPE sans mention 2024 REV 00.pptx",
            "p": 694,
            "c": "En termes juridiques, un immeuble est un bien non susceptible d'ÃÂªtre dÃÂ©placÃÂ©. Il peut donc s'agir d'un bÃÂ¢timent mais ÃÂ©galement d'une maison, d'un terrain, d'une propriÃÂ©tÃÂ© agricoleÃ¢ÂÂ¦ Un bien qui ne peut"
        },
        {
            "s": "DPE sans mention 2024 REV 00.pptx",
            "p": 493,
            "c": "Le SystÃÂ¨me Split Cette autre version se compose de deux blocs indÃÂ©pendants. Le premier correspond ÃÂ  lÃ¢ÂÂunitÃÂ© intÃÂ©rieure et a pour rÃÂ´le de rafraÃÂ®chir les lieux, il sera donc installÃÂ© dans la piÃÂ¨ce souha"
        }
    ],
    "005": [
        {
            "s": "corrigÃÂ© exercice 3 Usage autre qu'habita",
            "p": 3,
            "c": "ANZ FORMATION | 9 ruelle du maitre d'ÃÂ©cole 77500 CHELLES | TÃÂ©l. : 0663573165 NÃÂ°SIREN : 948520630 | Compagnie d'assurance : KLARITY nÃÂ° CDIAGK001066 3/4 Dossier 24/IMO/0127 Rapport du : 12/06/2024Diagno"
        },
        {
            "s": "Plans maison Clos des Bleuets.pdf",
            "p": 5,
            "c": "HAUTEUR maxi FAITAGE / TN 4.68 mPENTE 35 %PIGNONS 0.40 MFACADES 0.40 MDEBORD DE TOITURE Plans non destinÃÂ©s ÃÂ  l'ÃÂ©xÃÂ©cution des travaux, mais rÃÂ©servÃÂ©s ÃÂ  l'obtention des autorisations administratives de c"
        },
        {
            "s": "QCM 1 ENERGIE MENTION CORRIGE.pdf",
            "p": 8,
            "c": "Une chaudiÃÂ¨re ÃÂ©quipÃÂ©es de brÃÂ»leurs ÃÂ  air pulsÃÂ© 38) Le chauffage d'une CTA peut ÃÂªtre assurÃÂ© par :* Des batteries chaudes ÃÂ©lectriques Des batteries chaudes hydroliques Des aÃÂ©rothermes 39) Quelles sont l"
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
            "c": "NF P 03- 200 12 Ã¯ÂÂ¾ rÃÂ©fÃÂ©rences cadastrales ; Ã¯ÂÂ¾ nÃÂ° des lots ; informations collectÃÂ©es auprÃÂ¨s du donneur d'ordre relatives ÃÂ  des traitements antÃÂ©rieurs contre les agents de dÃÂ©gradations biologiques du boi"
        },
        {
            "s": "NFP 03201 (termites).pdf",
            "p": 22,
            "c": "NF P 03-201 ( P 03-201 ) Page 21 Bibliographie [1] NF P 03-200, Agents de dÃÂ©gradation biologique du bois Ã¢ÂÂ Constat de l'ÃÂ©tat parasitaire dans les immeubles bÃÂ¢tis et non bÃÂ¢tis. [2] FD P 20-651, Durabil"
        }
    ],
    "007": [
        {
            "s": "FD C 16-600.pdf",
            "p": 10,
            "c": "FD C 16 -600 Ã¢ÂÂ 8 Ã¢ÂÂ B.5 Fiche de contrÃÂ´le NÃÂ° 5 Ã¢ÂÂ PrÃÂ©sence dÃ¢ÂÂune LIAISON EQUIPOTENTIELLE supplÃÂ©mentaire (LES) dans chaque local contenant une baignoire ou une douche ...................................."
        },
        {
            "s": "NF C 15-100.pdf",
            "p": 21,
            "c": "NF C 15-100 Index - XII - 2002Courant diffÃÂ©rentiel -rÃÂ©siduel ..................... 233.7 DÃÂ©finition 411.5.1 SchÃÂ©ma TN 411.5.2 SchÃÂ©ma TT 531.2 Choix DDR Courant d'emploi ..............................."
        },
        {
            "s": "NF C 15-100.pdf",
            "p": 14,
            "c": "NF C 15-100 - V - 2002TABLEAU I CORRESPONDANCE ENTRE LA NORME NF C 15-100 ET LES PUBLICATIONS INTERNATIONALES Norme NF C 15-100 Document d'Harmonisation du CENELECPublication CEI TITRE 1 60364-1 TITRE"
        }
    ],
    "008": [
        {
            "s": "NF DTU 24 1 P1 MÃÂ J 20.02.06 FS.pdf",
            "p": 10,
            "c": "Ã¢ÂÂ 9 Ã¢ÂÂ NF DTU 24.1 P1 Sommaire (suite) Page 12.4 Carneaux en bÃÂ©ton ............................................................................................................. ............... 80 12.4."
        },
        {
            "s": "NF P 45-500.pdf",
            "p": 31,
            "c": "Ã¢ÂÂ 29 Ã¢ÂÂ NF P 45-500 Pour le cas des tiges aprÃÂ¨s compteur et en maison individuelle, lÃ¢ÂÂorgane de coupure supplÃÂ©mentaire doit ÃÂªtre accessible. La prÃÂ©sence dÃ¢ÂÂun dispositif de manÃÂuvre doit ÃÂªtre vÃÂ©rifiÃÂ©e. "
        },
        {
            "s": "NF P 45-500.pdf",
            "p": 17,
            "c": "Ã¢ÂÂ 15 Ã¢ÂÂ NF P 45-500 Annexe B (normative) Grille de contrÃÂ´le (voir 4.2) Init numÃÂ©rotation des tableaux dÃ¢ÂÂannexe [B]!!! Init numÃÂ©rotation des figures dÃ¢ÂÂannexe [B]!!! Init numÃÂ©rotation des ÃÂ©quations dÃ¢ÂÂann"
        }
    ],
    "009": [
        {
            "s": "TABLEAUX PARASITES.pdf",
            "p": 1,
            "c": "Pas de trous de sorties Souvent aspect feuilletÃÂ© Trous de sorties + vermoulures dans ou sur le bois Trous de sorties + copeaux Pas de trous de sorties Aspect feuilletÃÂ© ou Galeries ouvertes CatÃÂ©gories "
        },
        {
            "s": "GUIDE-PRATIQUE-DROM-COM-2022.pdf",
            "p": 15,
            "c": "RÃÂ©glementation diagnostic & traitement Dans le neuf Les articles L 112-17 et R 112-2 ÃÂ  4 du Code de la construction et de lÃ¢ÂÂHabitation et leur arrÃÂªtÃÂ© dÃ¢ÂÂapplication du 27 juin 2006 prÃÂ©voient notamment "
        },
        {
            "s": "GUIDE-PRATIQUE-DROM-COM-2022.pdf",
            "p": 12,
            "c": "Une lutte efficace. Deux mÃÂ©thodes sous certification : le traitement au moyen de produits biocides et le traitement par la chaleur. Les techniques de prÃÂ©servation des bois en ÃÂuvre Chaque situation nÃÂ©"
        }
    ],
    "010": [
        {
            "s": "2020-06-08-RTG_guide_revJ (2).pdf",
            "p": 45,
            "c": "Comprendre et appliquer la RTG 2020 45 4.6.6.1 CaractÃÂ©ristiques thermiques, ÃÂ©nergÃÂ©ti ques et lumineuses des baies et de leurs protections mobiles La RTG2020 introduit un modÃÂ¨le dynamique dÃ¢ÂÂouverture d"
        },
        {
            "s": "2020-06-08-RTG_guide_revJ (2).pdf",
            "p": 6,
            "c": "intÃÂ©grÃÂ©e de man iÃÂ¨re performantielle ÃÂ  la dÃÂ©libÃÂ©ration du Calcul RTG au travers du nouvel indicateur PRECS ; Ã¢ÂÂ¢ Plateforme de calcul RTG/DPEG : la rÃÂ©gion Guadeloupe met ÃÂ  disposition gratuitement un no"
        },
        {
            "s": "Cours DPEG-J1.pdf",
            "p": 20,
            "c": "Rtg 2020 Ã¢ÂÂ principes et ÃÂ©volutions Conditions de conformitÃÂ© Suppression des exigences minimales Approche 100% performantielle Art. 16 : ÃÂ©tanchÃÂ©itÃÂ© ÃÂ  lÃ¢ÂÂair des baies performantiel Art. 17 : surface dÃ¢ÂÂo"
        }
    ],
    "011": [
        {
            "s": "DTG PPPT  ITEM 3  DTG -PPPT.pptx",
            "p": 5,
            "c": "La loi ALUR ou loi Duflot II du 24 mars 2014 Analyse de l'ÃÂ©tat apparent des parties communes: Parties de bÃÂ¢timents (couloirs, chaudiÃÂ¨re, canalisation, garde-corps...) et des terrains (jardins, parcs.."
        },
        {
            "s": "DTG PPPT  ITEM 2 COPROPRIETE.pptx",
            "p": 32,
            "c": "Carnet dÃ¢ÂÂentretien Le carnet d'entretien doit mentionner au minimum les ÃÂ©lÃÂ©ments suivants : Adresse de l'immeuble IdentitÃÂ© de l'actuel syndic de copropriÃÂ©tÃÂ© RÃÂ©fÃÂ©rences des contrats d'assurance souscri"
        },
        {
            "s": "DTG PPPT ITEM 1 CONNAISSSANCE  DU BATI.p",
            "p": 32,
            "c": "Isolation ITE Les isolants naturels et ÃÂ©cologiques LiÃÂ¨ge : Les panneaux de liÃÂ¨ge sont un choix ÃÂ©cologique pour lÃ¢ÂÂITE. Ils sont durables, rÃÂ©sistants aux intempÃÂ©ries et peuvent ÃÂªtre fixÃÂ©s sur les murs a"
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
    "003": {"name": "Amiante avec mention", "description": "Diagnostic amiante - niveau avancÃÂ©"},
    "004": {"name": "ÃÂnergie sans mention", "description": "DPE - Diagnostic de Performance ÃÂnergÃÂ©tique"},
    "005": {"name": "ÃÂnergie avec mention", "description": "DPE - niveau avancÃÂ© (tertiaire/ERP)"},
    "006": {"name": "Termites MÃÂ©tropole", "description": "Diagnostic termites France mÃÂ©tropolitaine"},
    "007": {"name": "ÃÂlectricitÃÂ©", "description": "Diagnostic installation ÃÂ©lectrique"},
    "008": {"name": "Gaz", "description": "Diagnostic installation gaz"},
    "009": {"name": "Termites DROM", "description": "Diagnostic termites DOM-ROM"},
    "010": {"name": "DPEG", "description": "Diagnostic de Performance ÃÂnergÃÂ©tique Global"},
    "011": {"name": "DTG / PPT", "description": "Diagnostic Technique Global & Plan Pluriannuel de Travaux"},
}


# ============================================================
# RECHERCHE PAR MOTS-CLÃÂS (BM25-STYLE)
# ============================================================

# Stopwords franÃÂ§ais pour la recherche
STOPWORDS = set("le la les un une des de du d l ÃÂ  au aux en et ou mais si car ni ne pas que qui quoi dont oÃÂ¹ ce ces cette cet son sa ses leur leurs mon ma mes ton ta tes il elle on nous vous ils elles je tu me te se lui y a est sont ÃÂ©tÃÂ© ÃÂªtre avoir fait faire peut plus trÃÂ¨s tout tous toute toutes autre autres mÃÂªme aussi bien par pour avec sans dans sur entre chez vers quel quelle quels quelles comme comment quand encore dÃÂ©jÃÂ ".split())

def tokenize(text: str) -> list[str]:
    """Tokenise un texte en mots normalisÃÂ©s."""
    text = text.lower()
    text = re.sub(r'[^a-zÃÂ ÃÂ¢ÃÂ¤ÃÂ©ÃÂ¨ÃÂªÃÂ«ÃÂ¯ÃÂ®ÃÂ´ÃÂ¹ÃÂ»ÃÂ¼ÃÂ¿ÃÂ§ÃÂÃÂ¦0-9\s-]', ' ', text)
    words = text.split()
    return [w for w in words if w not in STOPWORDS and len(w) > 2]

class ChunkIndex:
    """Index de recherche BM25 sur les chunks prÃÂ©-extraits."""

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
            print("Utilisation des chunks de secours intÃÂ©grÃÂ©s...")
            data = FALLBACK_CHUNKS

        self._index_data(data)

    def load_from_dict(self, data: dict):
        """Charge les chunks depuis un dictionnaire Python."""
        self._index_data(data)

    def _index_data(self, data: dict):
        """Indexe les chunks depuis un dictionnaire."""
        for module_id, chunks in data.items():
            self.chunks[module_id] = chunks
            # PrÃÂ©-calculer les tokens pour chaque chunk
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
        print(f"Index chargÃÂ© : {total} chunks pour {len(self.chunks)} modules")

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

        # Trier par score dÃÂ©croissant
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
# GÃÂNÃÂRATION IA (Claude API)
# ============================================================

async def generate_answer(question: str, context_chunks: list[dict], module_name: str) -> dict:
    """GÃÂ©nÃÂ¨re une rÃÂ©ponse avec l'API Claude en mode RAG."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    if context_chunks:
        # Mode RAG : rÃÂ©ponse basÃÂ©e sur les documents indexÃÂ©s
        context_parts = []
        for i, chunk in enumerate(context_chunks):
            source_info = f"[Source: {chunk['source']}, Page {chunk['page']}"
            if chunk.get('section'):
                source_info += f", Section: {chunk['section']}"
            source_info += f", Pertinence: {chunk['similarity']}]"
            context_parts.append(f"--- Extrait {i+1} {source_info} ---\n{chunk['content']}")

        context = "\n\n".join(context_parts)

        system_prompt = f"""Tu es l'assistant IA de formation du Centre F, spÃÂ©cialisÃÂ© dans les diagnostics immobiliers.
Tu rÃÂ©ponds aux questions des apprenants du module "{module_name}".

RÃÂGLES STRICTES :
1. RÃÂ©ponds en te basant PRINCIPALEMENT sur les extraits de documents fournis ci-dessous.
2. Tu peux complÃÂ©ter avec tes connaissances rÃÂ©glementaires si les extraits sont insuffisants, mais prÃÂ©cise-le.
3. Cite TOUJOURS tes sources (nom du document, page) pour les informations issues des extraits.
4. Mentionne les textes rÃÂ©glementaires pertinents (arrÃÂªtÃÂ©s, normes NF, Code de la SantÃÂ© Publique, etc.).
5. Utilise un langage professionnel mais accessible.
6. Structure ta rÃÂ©ponse avec des paragraphes clairs.
7. Mets en gras les ÃÂ©lÃÂ©ments clÃÂ©s avec **texte**.

EXTRAITS DES SUPPORTS DE FORMATION DU CENTRE F :
{context}"""
    else:
        # Mode connaissances gÃÂ©nÃÂ©rales (fallback)
        system_prompt = f"""Tu es l'assistant IA de formation du Centre F, spÃÂ©cialisÃÂ© dans les diagnostics immobiliers.
Tu rÃÂ©ponds aux questions des apprenants du module "{module_name}".

RÃÂ©ponds en te basant sur la rÃÂ©glementation franÃÂ§aise en vigueur concernant les diagnostics immobiliers.
Mentionne les textes rÃÂ©glementaires pertinents (arrÃÂªtÃÂ©s, normes NF, Code de la SantÃÂ© Publique, etc.).
Utilise un langage professionnel mais accessible.
Structure ta rÃÂ©ponse avec des paragraphes clairs.
Mets en gras les ÃÂ©lÃÂ©ments clÃÂ©s avec **texte**."""

    message = client.messages.create(
        model=settings.claude_model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": question}]
    )

    answer_text = message.content[0].text

    # Extraire les sources utilisÃÂ©es (uniquement en mode RAG)
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
                elif "arrÃÂªtÃÂ©" in name or "dÃÂ©cret" in name or "arretÃÂ©" in name or "arrete" in name:
                    source_type = "RÃÂ©glementation"
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

# Charger l'index au dÃÂ©marrage
chunk_index = ChunkIndex()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - charger les chunks
    print("Le Centre F - Assistant IA Backend v2.1")
    print(f"ModÃÂ¨le IA : {settings.claude_model}")

    # Chercher le fichier chunks
    loaded = False
    for path in ["chunks_uploaded.json.gz", "chunks.json", "data/chunks.json", "chunks.json.gz", "data/chunks.json.gz"]:
        if os.path.exists(path):
            try:
                chunk_index.load_from_json(path)
                loaded = True
                break
            except Exception as e:
                print(f"Erreur avec {path}: {e}")
                continue

    if not loaded:
        print("Aucun fichier externe trouvÃÂ©, chargement des chunks de secours...")
        chunk_index.load_from_dict(FALLBACK_CHUNKS)

    yield
    print("ArrÃÂªt du serveur...")

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
        "chunks_indexÃÂ©s": total_chunks
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
    """Pose une question ÃÂ  l'assistant IA sur un module."""
    import time
    start = time.time()

    if req.module_id not in MODULES:
        raise HTTPException(status_code=400, detail=f"Module {req.module_id} inconnu")

    module = MODULES[req.module_id]

    # 1. Recherche BM25 des chunks pertinents
    chunks = chunk_index.search(req.question, req.module_id, settings.top_k_results)

    # 2. GÃÂ©nÃÂ©ration de la rÃÂ©ponse avec Claude
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
        "modules_indexÃÂ©s": len(by_module),
        "by_module": by_module
    }


# ============================================================
# POINT D'ENTRÃÂE
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


# ============================================================
# Admin: Upload de chunks via formulaire web
# ============================================================

ADMIN_KEY = os.getenv("ADMIN_KEY", "centref2026")

@app.get("/admin/upload", response_class=HTMLResponse)
async def admin_upload_form():
    """Formulaire HTML pour uploader des chunks."""
    return """<!DOCTYPE html>
<html><head><title>Admin - Upload Chunks</title>
<style>body{font-family:sans-serif;max-width:600px;margin:50px auto;padding:20px}
h1{color:#339933}button{background:#339933;color:white;padding:10px 20px;border:none;cursor:pointer;font-size:16px}
#status{margin-top:20px;padding:10px;border-radius:4px}</style></head>
<body><h1>Le Centre F - Admin Upload</h1>
<p>Uploader un fichier <code>chunks.json.gz</code> ou <code>chunks.json</code></p>
<input type="password" id="key" placeholder="Cl\u00e9 admin" style="padding:8px;width:200px"><br><br>
<input type="file" id="file" accept=".json,.gz"><br><br>
<button onclick="upload()">Envoyer</button>
<div id="status"></div>
<script>
async function upload(){
  const key=document.getElementById('key').value;
  const file=document.getElementById('file').files[0];
  if(!file||!key){document.getElementById('status').textContent='Fichier et cl\u00e9 requis';return}
  const formData=new FormData();
  formData.append('file',file);
  formData.append('admin_key',key);
  document.getElementById('status').textContent='Envoi en cours...';
  try{
    const r=await fetch('/api/admin/upload-chunks',{method:'POST',body:formData});
    const d=await r.json();
    document.getElementById('status').textContent=JSON.stringify(d);
    document.getElementById('status').style.background=r.ok?'#d4edda':'#f8d7da';
  }catch(e){document.getElementById('status').textContent='Erreur: '+e.message}
}
</script></body></html>"""

from fastapi.responses import HTMLResponse

@app.post("/api/admin/upload-chunks")
async def admin_upload_chunks(file: UploadFile = File(...), admin_key: str = ""):
    """Upload de chunks via fichier (admin uniquement)."""
    if admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Cl\u00e9 admin invalide")
    
    try:
        raw = await file.read()
        
        # D\u00e9tecter si c'est du gzip
        if file.filename.endswith('.gz') or raw[:2] == b'\x1f\x8b':
            import io
            with gzip.open(io.BytesIO(raw), 'rt', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = json.loads(raw.decode('utf-8'))
        
        # Recharger l'index
        chunk_index.chunks = {}
        chunk_index.doc_tokens = {}
        chunk_index.idf = {}
        chunk_index.avg_dl = {}
        chunk_index._index_data(data)
        
        total = sum(len(v) for v in chunk_index.chunks.values())
        
        # Sauvegarder sur disque pour persistance au red\u00e9marrage
        with open('chunks_uploaded.json.gz', 'wb') as f:
            f.write(gzip.compress(json.dumps(data, ensure_ascii=False).encode('utf-8')))
        
        return {"status": "ok", "chunks_loaded": total, "modules": len(chunk_index.chunks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ============================================
# Chunked Base64 Upload (for large files)
# ============================================
_b64_chunks = {}
_UPLOAD_DIR = "_upload_chunks"

@app.post("/api/admin/upload-b64-chunk")
async def upload_b64_chunk(request: Request):
    body = await request.json()
    if body.get("admin_key") != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    idx = body["index"]
    data = body["data"]
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(_UPLOAD_DIR, f"chunk_{idx:04d}.txt"), "w") as f:
        f.write(data)
    chunks = [c for c in os.listdir(_UPLOAD_DIR) if c.startswith("chunk_")]
    total_bytes = sum(os.path.getsize(os.path.join(_UPLOAD_DIR, c)) for c in chunks)
    return {"status": "ok", "chunk_index": idx, "chunks_received": len(chunks), "total_b64_bytes": total_bytes}

@app.post("/api/admin/finalize-b64-upload")
async def finalize_b64_upload(request: Request):
    body = await request.json()
    if body.get("admin_key") != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    files = sorted([f for f in os.listdir(_UPLOAD_DIR) if f.startswith("chunk_")])
    combined = ""
    for fname in files:
        with open(os.path.join(_UPLOAD_DIR, fname), "r") as f:
            combined += f.read()
    import base64 as b64mod
    binary_data = b64mod.b64decode(combined)
    json_bytes = gzip.decompress(binary_data)
    chunks_data = json.loads(json_bytes)
    with gzip.open("chunks_uploaded.json.gz", "wb") as f:
        f.write(json.dumps(chunks_data, ensure_ascii=False).encode("utf-8"))
    global chunk_index
    chunk_index.chunks = {}
    chunk_index.doc_tokens = {}
    chunk_index.idf = {}
    chunk_index.avg_dl = {}
    chunk_index._index_data(chunks_data)
    total = sum(len(v) for v in chunk_index.chunks.values())
    import shutil
    shutil.rmtree(_UPLOAD_DIR, ignore_errors=True)
    _b64_chunks.clear()
    return {"status": "ok", "total_chunks": total, "modules": len(chunk_index.chunks)}

@app.get("/api/admin/upload-status")
async def upload_status():
    if os.path.isdir(_UPLOAD_DIR):
        chunks = [c for c in os.listdir(_UPLOAD_DIR) if c.startswith("chunk_")]
        total_bytes = sum(os.path.getsize(os.path.join(_UPLOAD_DIR, c)) for c in chunks)
        return {"chunks_received": len(chunks), "total_b64_bytes": total_bytes}
    return {"chunks_received": 0, "total_b64_bytes": 0}
