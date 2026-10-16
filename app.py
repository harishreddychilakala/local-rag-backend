import os
import sys
import re
import json
import urllib.parse
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
import faiss
import numpy as np
from pydantic import BaseModel, EmailStr
from pypdf import PdfReader
import requests
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

import database
from database import (
    get_db,
    init_db,
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    User,
    ChatSession,
    ChatMessage,
    save_message_to_db,
    get_user_chat_sessions,
    delete_chat_session_from_db,
    sync_document_chunks_to_db,
)

# Configure UTF-8 stdout encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

app = FastAPI(title="Local RAG Backend with Neon Postgres", version="1.2.0")

# Initialize Neon PostgreSQL tables on startup
database_connected = init_db()

# ---------------- CORS ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- GROQ CLIENT HELPER ----------------
def get_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from groq import Groq
        return Groq(api_key=api_key)
    except Exception as e:
        print(f"[WARN] Error initializing Groq client: {e}")
        return None


# ---------------- AUTH DEPENDENCY ----------------
def get_current_user_optional(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    user_id = payload["sub"]
    if not db:
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_current_user_required(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
        )
    return user


# ---------------- STEP 1 & 2: PERSISTENT RAG INGESTION & FAISS ----------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

INDEX_PATH = os.path.join(DATA_DIR, "faiss_index_norm.bin")
CHUNKS_PATH = os.path.join(DATA_DIR, "chunks_cache.json")

files = ["DBMS-RAG.pdf", "OS-RAG.pdf", "data.pdf"]
chunks: List[str] = []
chunks_metadata: List[dict] = []

model = SentenceTransformer("all-MiniLM-L6-v2")
dimension = 384

# Check if cached FAISS index and chunks exist
if os.path.exists(INDEX_PATH) and os.path.exists(CHUNKS_PATH):
    try:
        index = faiss.read_index(INDEX_PATH)
        with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
            chunks = cached_data.get("chunks", [])
            chunks_metadata = cached_data.get("metadata", [])
        print(f"[OK] Loaded persistent FAISS index ({index.ntotal} vectors) and {len(chunks)} chunks from {INDEX_PATH}")
    except Exception as e:
        print(f"[WARN] Failed to load cached index, rebuilding: {e}")
        index = None
else:
    index = None

# If not cached, extract from PDFs and build index
if index is None or len(chunks) == 0:
    chunks = []
    chunks_metadata = []
    loaded_files = []

    for file in files:
        if os.path.exists(file):
            try:
                reader = PdfReader(file)
                file_text = ""
                for page in reader.pages:
                    content = page.extract_text()
                    if content:
                        file_text += content + "\n"

                file_chunks = [
                    "Chunk " + c.strip()
                    for c in file_text.split("Chunk")
                    if c.strip() != ""
                ]

                for idx, c in enumerate(file_chunks):
                    chunks.append(c)
                    chunks_metadata.append({"source": file, "index": idx, "content": c})

                loaded_files.append(file)
            except Exception as e:
                print(f"[WARN] Error reading {file}: {e}")
        else:
            print(f"[WARN] PDF file not found: {file}")

    print(f"[OK] RAG system extracted {len(chunks)} chunks from {loaded_files}")

    if chunks:
        embeddings = model.encode(chunks, normalize_embeddings=True)
        embedding_array = np.array(embeddings).astype("float32")
        index = faiss.IndexFlatIP(embedding_array.shape[1])
        index.add(embedding_array)

        # Persist index to disk
        try:
            faiss.write_index(index, INDEX_PATH)
            with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
                json.dump({"chunks": chunks, "metadata": chunks_metadata}, f)
            print(f"[OK] Persisted normalized FAISS index and chunks cache to {DATA_DIR}")
        except Exception as e:
            print(f"[WARN] Could not persist FAISS index: {e}")
    else:
        index = faiss.IndexFlatIP(dimension)

# Sync document chunks to Neon PostgreSQL on startup
if database.DB_ENABLED and database.SessionLocal and chunks_metadata:
    try:
        with database.SessionLocal() as session:
            sync_document_chunks_to_db(session, chunks_metadata)
    except Exception as e:
        print(f"[WARN] Failed syncing chunks to DB: {e}")


# ---------------- STEP 3: SMALL TALK HANDLER ----------------
def handle_small_talk(q: str) -> Optional[str]:
    ql = q.lower().strip()
    if any(w in ql for w in ["hi", "hello", "hey", "good morning", "good evening"]):
        return "Hello! 👋 I am your Computer Science RAG assistant. Ask me anything about DBMS, Operating Systems, or your indexed documents!"
    if any(w in ql for w in ["thank", "thanks"]):
        return "You're welcome 😊! Feel free to ask anything else."
    if any(w in ql for w in ["bye", "goodbye", "see you"]):
        return "Goodbye! Have a great day ahead! 👋"
    return None


# ---------------- STEP 4: STRUCTURED DOCUMENT CHUNK PARSER & SYNTHESIS ----------------
def parse_single_chunk(raw_text: str) -> dict:
    """Parses a structured document chunk into title, definition, explanation, key points, example, and interview tip."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    data = {
        "title": "",
        "definition": "",
        "explanation": "",
        "key_points": [],
        "example": "",
        "interview_tip": "",
        "other": []
    }
    
    current_section = "other"
    for line in lines:
        m = re.match(r"^chunk\s*\d*[:\s]*(.*)$", line, re.IGNORECASE)
        if m:
            title_text = m.group(1).strip()
            if title_text and not title_text.startswith("🔹") and not title_text.startswith("🔸"):
                data["title"] = title_text
            continue
        
        low = line.lower()
        if low.startswith("title:"):
            title_content = line.split(":", 1)[1].strip()
            if title_content:
                data["title"] = title_content
            current_section = "other"
            continue
        elif low.startswith("definition:"):
            current_section = "definition"
            content = line.split(":", 1)[1].strip()
            if content:
                data["definition"] = content
            continue
        elif low.startswith("explanation:"):
            current_section = "explanation"
            content = line.split(":", 1)[1].strip()
            if content:
                data["explanation"] = content
            continue
        elif low.startswith("key points:") or low.startswith("key features:") or low.startswith("benefits:") or low.startswith("components:"):
            current_section = "key_points"
            content = line.split(":", 1)[1].strip()
            if content:
                data["key_points"].append(content)
            continue
        elif low.startswith("example:") or low.startswith("examples:") or low.startswith("use case:"):
            current_section = "example"
            content = line.split(":", 1)[1].strip()
            if content:
                data["example"] = content
            continue
        elif low.startswith("interview tip:") or low.startswith("interview tips:") or low.startswith("guidelines:"):
            current_section = "interview_tip"
            content = line.split(":", 1)[1].strip()
            if content:
                data["interview_tip"] = content
            continue
        
        # Section accumulation
        if current_section == "definition":
            data["definition"] = (data["definition"] + " " + line).strip()
        elif current_section == "explanation":
            data["explanation"] = (data["explanation"] + " " + line).strip()
        elif current_section == "key_points":
            cleaned_bullet = re.sub(r"^[•\*\-\uf0b7\s]+", "", line).strip()
            if cleaned_bullet:
                data["key_points"].append(cleaned_bullet)
        elif current_section == "example":
            data["example"] = (data["example"] + " " + line).strip()
        elif current_section == "interview_tip":
            data["interview_tip"] = (data["interview_tip"] + " " + line).strip()
        else:
            if not data["title"] and not line.endswith(":"):
                data["title"] = line
            else:
                data["other"].append(line)
                
    return data


def format_rich_document_answer(chunks: List[str], question: str) -> str:
    """Formats one or more document chunks into an elite, structured, textbook-grade markdown answer."""
    if not chunks:
        return "I couldn't find relevant information in the database."

    parsed_items = [parse_single_chunk(c) for c in chunks]
    parsed_items = [p for p in parsed_items if p["title"] or p["definition"] or p["other"]]

    # Filter out generic interview question lists unless user explicitly asked for them
    ql = question.lower()
    if "interview" not in ql:
        non_qa = [p for p in parsed_items if not p["title"].lower().startswith("interview question")]
        if non_qa:
            parsed_items = non_qa

    if not parsed_items:
        return clean_chunk_text(chunks[0])

    sections = []
    for p in parsed_items:
        parts = []
        title = p["title"] or "Core Concept Overview"
        parts.append(f"## 📌 {title}\n")
        
        if p["definition"]:
            parts.append(f"**Definition:**  \n{p['definition']}\n")
        if p["explanation"]:
            parts.append(f"**How It Works & Mechanism:**  \n{p['explanation']}\n")
        if p["key_points"]:
            bullet_lines = "\n".join(
                f"- **{pt.split('→')[0].strip()}**" + (f" → {pt.split('→', 1)[1].strip()}" if '→' in pt else f": {pt}")
                if not pt.startswith("-") and not pt.startswith("•") else f"- {pt.lstrip('•- ')}"
                for pt in p["key_points"]
            )
            parts.append(f"**Key Concepts & Architecture:**  \n{bullet_lines}\n")
        if p["example"]:
            parts.append(f"**Real-World Example:**  \n`{p['example']}`\n")
        if p["interview_tip"]:
            parts.append(f"> 💡 **Interview & Exam Pro-Tip:**  \n> {p['interview_tip']}\n")
        if p["other"]:
            other_text = "\n".join(f"- {line}" if not line.startswith("-") else line for line in p["other"])
            parts.append(f"**Additional Details:**  \n{other_text}\n")
            
        sections.append("\n".join(parts).strip())

    joined = "\n\n---\n\n".join(sections)
    
    # Contextual synthesis for multi-topic queries (e.g., Paging + Virtual Memory)
    ql = question.lower()
    if ("paging" in ql and "virtual memory" in ql) or len(parsed_items) > 1:
        synthesis = (
            "\n\n---\n\n"
            "### 🔗 Key Architectural Relationship & Synthesis\n\n"
            "In modern Operating Systems, **Paging** is the foundational physical memory virtualization mechanism, while **Virtual Memory** is the overarching architectural abstraction:\n"
            "- **The Abstraction (Virtual Memory)**: Gives each process the illusion of having a vast, contiguous address space that can far exceed available physical RAM.\n"
            "- **The Implementation (Paging)**: Breaks this virtual address space into fixed-size **pages** (typically 4 KB) and maps them into non-contiguous physical RAM **frames** via **Page Tables**.\n"
            "- **Demand Paging**: Pages are loaded from disk into RAM only when actively accessed. If a page is not present in RAM, a **page fault** occurs, triggering the OS to swap it in from secondary storage.\n\n"
            "> 🎯 **Key Interview Takeaway:** Virtual Memory enables running programs larger than physical memory; Paging is the mechanism that avoids external fragmentation and makes demand paging feasible."
        )
        joined += synthesis

    return joined


def clean_chunk_text(raw_text: str) -> str:
    lines = raw_text.splitlines()
    cleaned = []
    skip_section = False
    for line in lines:
        s = line.strip()
        if not s:
            if not skip_section:
                cleaned.append("")
            continue
        if re.match(r"^chunk\s+\d+[:\s]*", s, re.IGNORECASE):
            parts = s.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                cleaned.append(f"### {parts[1].strip()}\n")
            continue
        if s.lower().startswith("interview tip"):
            skip_section = True
            continue
        if skip_section:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# ---------------- MASTER SYSTEM PROMPT FOR AI TUTOR ----------------
SYSTEM_PROMPT = """You are an elite Computer Science Professor, Principal Systems Architect, and Master Technical Mentor.
Your mission is to provide world-class, exceptionally clear, structured, and pedagogical explanations.

### PEDAGOGICAL & FORMATTING GUIDELINES:
1. **Direct Core Definition / Executive Summary**:
   - Begin with an immediate, high-impact definition explaining what the concept is, its primary purpose, and why it exists.
   - If the user asks about multiple concepts (such as "What is paging and virtual memory in OS?"), introduce each concept clearly and articulate the exact conceptual bridge connecting them.

2. **Deep-Dive Architecture & Underlying Mechanics ("How It Works")**:
   - Detail the internal mechanism step-by-step (e.g., virtual address translation, page tables, page faults, demand paging, TLB caching, frames vs pages).
   - Use clean Markdown tables or formatted lists to make technical trade-offs and flows intuitive.

3. **Key Characteristics & Components**:
   - Use organized bullet points with **bold terms** to highlight critical properties, formulas, rules, or data structures.

4. **Concrete Real-World Examples & Analogies**:
   - Provide relatable analogies or practical industry examples (e.g. Linux kernel demand paging, Windows pagefile.sys, 64-bit architecture address spaces).

5. **Trade-offs, Comparisons & Edge Cases**:
   - Explicitly highlight trade-offs (e.g. internal vs. external fragmentation, page table memory overhead vs. lookup speed).
   - When comparing concepts (e.g., Paging vs Segmentation, or Process vs Thread), provide a clear, structured comparison.

6. **Interview / Exam Pro-Tip**:
   - Conclude with an actionable "💡 Interview & Exam Pro-Tip" summarizing what technical interviewers look for.

### STYLE & FORMATTING RULES:
- Format exclusively in clean GitHub-flavored Markdown using `##`, `###`, bullet lists, bold text, and code blocks.
- Answer authoritatively and fluently. NEVER use robotic meta-phrases like "Based on the provided context", "According to chunk 1", or "As an AI model".
- Ensure the answer is comprehensive, well-spaced, and easy to read."""


# ---------------- STEP 5: ONLINE KNOWLEDGE SEARCH ----------------
def fetch_online_knowledge(query: str) -> Optional[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    cleaned_query = query.lower().strip()
    prefixes = [
        "what is mean by ", "what is meant by ", "what do you mean by ",
        "what is the meaning of ", "meaning of ",
        "what is ", "what are ", "explain ", "tell me about ",
        "define ", "who is ", "how does ", "what does ", "describe "
    ]
    for prefix in prefixes:
        if cleaned_query.startswith(prefix):
            cleaned_query = cleaned_query[len(prefix):]
            break
    cleaned_query = cleaned_query.strip(" ?.:;,")
    if not cleaned_query:
        cleaned_query = query.strip(" ?.")

    # 1. Wikipedia Search API + Summary
    try:
        search_url = (
            "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch="
            + urllib.parse.quote(cleaned_query)
            + "&format=json&utf8=1"
        )
        r = requests.get(search_url, headers=headers, timeout=5)
        if r.ok:
            results = r.json().get("query", {}).get("search", [])
            if results:
                best_title = results[0]["title"]
                summary_url = (
                    "https://en.wikipedia.org/api/rest_v1/page/summary/"
                    + urllib.parse.quote(best_title)
                )
                sr = requests.get(summary_url, headers=headers, timeout=5)
                if sr.ok:
                    data = sr.json()
                    extract = data.get("extract")
                    if extract:
                        return {"title": best_title, "text": extract, "source": "Wikipedia"}
    except Exception as e:
        print(f"[INFO] Wikipedia lookup note: {e}")

    # 2. DuckDuckGo Instant Answer API fallback
    try:
        ddg_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote(cleaned_query)
            + "&format=json"
        )
        r = requests.get(ddg_url, headers=headers, timeout=5)
        if r.ok:
            data = r.json()
            if data.get("AbstractText"):
                return {
                    "title": data.get("Heading", cleaned_query.title()),
                    "text": data.get("AbstractText"),
                    "source": "DuckDuckGo",
                }
    except Exception as e:
        print(f"[INFO] DDG lookup note: {e}")

    return None


# ---------------- STEP 6: GENERATE ANSWER ----------------
def generate_answer(context_chunks: List[str], question: str) -> str:
    client = get_groq_client()
    context = "\n\n".join(context_chunks)

    if client:
        user_prompt = f"""Question: {question}

Context Information from Course Documents:
\"\"\"
{context}
\"\"\"

Please provide an expert, complete, and pedagogical explanation addressing the question."""
        try:
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                max_tokens=1024,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[WARN] Groq generation error: {e}")

    # Fallback to local rich document synthesis
    if context_chunks:
        return format_rich_document_answer(context_chunks, question)

    return "I couldn't find relevant information in the database."


def generate_intelligence_answer(question: str, online_info: Optional[dict] = None) -> tuple:
    """
    Answers questions not found in document chunks using AI intelligence (Groq LLaMA 3.1)
    and live online knowledge sources (Wikipedia/DuckDuckGo).
    """
    client = get_groq_client()
    sources = []

    if client:
        ref_context = (
            f"\n\nContext Information from Online Reference:\n\"\"\"\n{online_info['text']}\n\"\"\""
            if online_info else ""
        )
        user_prompt = f"""Question: {question}{ref_context}

Please provide an expert, complete, and pedagogical explanation addressing the question."""
        try:
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                max_tokens=1024,
            )
            answer = response.choices[0].message.content.strip()
            if online_info:
                sources = [
                    f"Online Knowledge: {online_info['title']} ({online_info['source']})",
                    "AI Intelligence (Groq LLaMA 3.1)",
                ]
            else:
                sources = ["AI Intelligence (Groq LLaMA 3.1)"]
            return answer, sources
        except Exception as e:
            print(f"[WARN] Groq intelligence generation failed: {e}")

    # Fallback when Groq API key is not configured or fails
    if online_info:
        title = online_info.get("title", "")
        text = online_info.get("text", "")
        source = online_info.get("source", "Online Knowledge")
        heading = f"### 🌐 {title}\n\n" if title else ""
        answer = (
            f"{heading}{text}\n\n"
            f"> 💡 *Answered from Live Knowledge Base ({source}). For deep AI synthesis and customized analysis, configure your free `GROQ_API_KEY` in `.env`.*"
        )
        sources = [f"Online ({source}): {title}"]
        return answer, sources

    fallback_answer = (
        "### 🔍 Information Not Found\n\n"
        "I could not locate detailed material on this topic in the local indexed documents or online knowledge bases.\n\n"
        "> 💡 **Tip:** Add a free Groq API key (`GROQ_API_KEY`) to your `.env` file to enable unlimited AI general intelligence responses with LLaMA 3.1 for any topic!"
    )
    return fallback_answer, []



# ── REQUEST & RESPONSE SCHEMAS ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    session_title: Optional[str] = None


class SignupRequest(BaseModel):
    email: EmailStr
    username: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SessionCreateRequest(BaseModel):
    id: str
    title: Optional[str] = "New Chat"


# ── AUTHENTICATION ROUTES ─────────────────────────────────────────────────────

@app.post("/api/auth/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if not db:
        raise HTTPException(status_code=503, detail="Database service currently unavailable.")

    # Check if user already exists
    existing = db.query(User).filter(User.email == req.email.lower().strip()).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    hashed_pw = hash_password(req.password)
    new_user = User(
        email=req.email.lower().strip(),
        username=req.username.strip(),
        hashed_password=hashed_pw,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token({"sub": new_user.id, "email": new_user.email})
    return {
        "token": token,
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "username": new_user.username,
        },
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    if not db:
        raise HTTPException(status_code=503, detail="Database service currently unavailable.")

    user = db.query(User).filter(User.email == req.email.lower().strip()).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = create_access_token({"sub": user.id, "email": user.email})
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
        },
    }


@app.get("/api/auth/me")
def get_me(current_user: User = Depends(get_current_user_required)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
    }


# ── CHAT SESSION & HISTORY ROUTES (NEON DB PERSISTENCE) ─────────────────────────

@app.get("/api/sessions")
def get_sessions(
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    user_id = current_user.id if current_user else None
    return get_user_chat_sessions(db, user_id=user_id)


@app.post("/api/sessions")
def create_session(
    req: SessionCreateRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if not db:
        return {"id": req.id, "title": req.title or "New Chat", "messages": []}

    user_id = current_user.id if current_user else None
    sess = db.query(ChatSession).filter(ChatSession.id == req.id).first()
    if not sess:
        sess = ChatSession(id=req.id, user_id=user_id, title=req.title or "New Chat")
        db.add(sess)
        db.commit()
    return {"id": sess.id, "title": sess.title, "messages": []}


@app.delete("/api/sessions/{session_id}")
def delete_session(
    session_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    user_id = current_user.id if current_user else None
    deleted = delete_chat_session_from_db(db, session_id, user_id=user_id)
    return {"deleted": deleted}


# ── MAIN QUERY ROUTE (/ask) ───────────────────────────────────────────────────

@app.post("/ask")
def ask(
    data: QueryRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    q = data.question.strip()
    session_id = data.session_id
    user_id = current_user.id if current_user else None

    # 1. Handle Small Talk
    small_talk_reply = handle_small_talk(q)
    if small_talk_reply:
        if session_id and db:
            save_message_to_db(db, session_id, "user", q, user_id=user_id, session_title=data.session_title)
            save_message_to_db(db, session_id, "bot", small_talk_reply, user_id=user_id)
        return {
            "question": q,
            "answer": small_talk_reply,
            "sources": [],
        }

    # 2. Local Document FAISS Retrieval (Cosine Similarity with Normalized Embeddings)
    filtered_chunks = []
    if chunks and index:
        query_embedding = model.encode([q], normalize_embeddings=True).astype("float32")
        k = min(5, len(chunks))
        scores, indices = index.search(query_embedding, k)

        # 0.48 cosine similarity threshold requires genuine semantic relevance to course documents
        similarity_threshold = 0.48
        for idx, score in zip(indices[0], scores[0]):
            if idx >= 0 and score >= similarity_threshold:
                filtered_chunks.append(chunks[idx])

    # 3. If Match Found in Local Documents: Answer from Chunks
    if filtered_chunks:
        answer = generate_answer(filtered_chunks[:3], q)
        sources = filtered_chunks[:3]
        if session_id and db:
            save_message_to_db(db, session_id, "user", q, user_id=user_id, session_title=data.session_title)
            save_message_to_db(db, session_id, "bot", answer, sources=sources, user_id=user_id)
        return {
            "question": q,
            "answer": answer,
            "sources": sources,
        }

    # 4. If Query Not Related to Local Chunks: Answer From Intelligence
    online_info = fetch_online_knowledge(q)
    answer, sources = generate_intelligence_answer(q, online_info=online_info)
    if session_id and db:
        save_message_to_db(db, session_id, "user", q, user_id=user_id, session_title=data.session_title)
        save_message_to_db(db, session_id, "bot", answer, sources=sources, user_id=user_id)
    return {
        "question": q,
        "answer": answer,
        "sources": sources,
    }


# ── ROOT & HEALTH CHECK ROUTES ────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status": "online",
        "message": "Local RAG Backend with Neon Postgres Running Successfully",
        "chunks_indexed": len(chunks),
        "groq_configured": bool(os.getenv("GROQ_API_KEY")),
        "database_connected": database.DB_ENABLED and database.engine is not None,
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "chunks": len(chunks),
        "groq_configured": bool(os.getenv("GROQ_API_KEY")),
        "database_connected": database.DB_ENABLED and database.engine is not None,
    }