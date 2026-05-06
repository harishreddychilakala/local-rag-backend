from fastapi import FastAPI
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel
import os

app = FastAPI()

# ---------------- GROQ CLIENT ----------------
client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

# ---------------- CORS ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- REQUEST MODEL ----------------
class QueryRequest(BaseModel):
    question: str

# ---------------- STEP 1: LOAD PDFs ----------------
files = ["DBMS-RAG.pdf", "OS-RAG.pdf", "data.pdf"]

text = ""

for file in files:
    reader = PdfReader(file)

    for page in reader.pages:
        content = page.extract_text()

        if content:
            text += content

# ---------------- STEP 2: SPLIT INTO CHUNKS ----------------
chunks = text.split("Chunk")

chunks = [
    "Chunk " + chunk.strip()
    for chunk in chunks
    if chunk.strip() != ""
]

print(f"✅ RAG system ready with {len(chunks)} chunks")

# ---------------- STEP 3: EMBEDDINGS ----------------
model = SentenceTransformer("all-MiniLM-L6-v2")

embeddings = model.encode(chunks)

embedding_array = np.array(embeddings).astype("float32")

# ---------------- STEP 4: FAISS ----------------
dimension = embedding_array.shape[1]

index = faiss.IndexFlatL2(dimension)

index.add(embedding_array)

# ---------------- STEP 5: SMALL TALK FILTER ----------------
def is_small_talk(q):

    q = q.lower()

    small_words = [
        "hi",
        "hello",
        "thanks",
        "thank you",
        "bye"
    ]

    return any(word in q for word in small_words)

# ---------------- STEP 6: GENERATE ANSWER ----------------
def generate_answer(context_chunks, question):

    context = "\n\n".join(context_chunks)

    prompt = f"""
You are an expert Computer Science tutor.

Instructions:
- If the context contains relevant information, answer using it.
- If the context does NOT contain the answer, use your general knowledge.
- NEVER repeat the instructions or context
- ONLY answer the question directly

Rules:
- Give a clear, detailed explanation
- Use simple language
- Add examples if possible
- Do NOT mention "context"
- Do NOT mention where the answer came from

Context:
{context}

Question:
{question}

Answer:
"""

    try:

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            max_tokens=300
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"Error: {str(e)}"

# ---------------- STEP 7: API ROUTE ----------------
@app.post("/ask")
def ask(data: QueryRequest):

    q = data.question

    # ---------------- SMALL TALK ----------------
    if is_small_talk(q):

        return {
            "question": q,
            "answer": "You're welcome 😊! Ask me anything about your documents."
        }

    # ---------------- QUERY EMBEDDING ----------------
    query_embedding = model.encode([q]).astype("float32")

    # ---------------- FAISS SEARCH ----------------
    k = 5

    distances, indices = index.search(query_embedding, k)

    # ---------------- FILTER RELEVANT CHUNKS ----------------
    threshold = 1.5

    filtered_chunks = []

    for idx, dist in zip(indices[0], distances[0]):

        if dist < threshold:
            filtered_chunks.append(chunks[idx])

    # ---------------- IF NO RELEVANT DATA ----------------
    if not filtered_chunks:

        general_answer = generate_answer([], q)

        return {
            "question": q,
            "answer": "I couldn't find relevant information in your documents.\n\n" + general_answer
        }

    # ---------------- LIMIT CHUNKS ----------------
    filtered_chunks = filtered_chunks[:3]

    # ---------------- GENERATE FINAL ANSWER ----------------
    answer = generate_answer(filtered_chunks, q)

    return {
        "question": q,
        "answer": answer,
        "sources": filtered_chunks
    }

# ---------------- ROOT ROUTE ----------------
@app.get("/")
def root():
    return {
        "message": "RAG Backend Running Successfully 🚀"
    }