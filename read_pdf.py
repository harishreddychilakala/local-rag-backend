from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss
import requests

# -------- STEP 1: READ MULTIPLE PDFs --------

files = ["DBMS-RAG.pdf", "OS-RAG.pdf"]

text = ""

for file in files:
    reader = PdfReader(file)
    for page in reader.pages:
        if page.extract_text():
            text += page.extract_text()

# -------- STEP 2: SPLIT INTO CHUNKS --------

chunks = text.split("🔹")
chunks = [chunk.strip() for chunk in chunks if chunk.strip() != ""]

# -------- STEP 3: LOAD MODEL --------

model = SentenceTransformer('all-MiniLM-L6-v2')

# -------- STEP 4: CREATE EMBEDDINGS --------

embeddings = model.encode(chunks)
embedding_array = np.array(embeddings).astype('float32')

print("\nEmbedding shape:", embedding_array.shape)

# -------- STEP 5: FAISS INDEX --------

dimension = embedding_array.shape[1]
index = faiss.IndexFlatL2(dimension)
index.add(embedding_array)

print("\nFAISS index created with", index.ntotal, "chunks")

# -------- STEP 6: GENERATE ANSWER --------

def generate_answer(context_chunks, question):
    context = "\n".join(context_chunks)

    prompt = f"""
You are a helpful assistant.

Answer the question using ONLY the context below.

Context:
{context}

Question:
{question}

Answer:
"""

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        }
    )

    result = response.json()
     

    return result.get("response", "No response from model")

# -------- QUERY PART --------

query = input("\nAsk a question: ")

query_embedding = model.encode([query]).astype('float32')

k = 2
distances, indices = index.search(query_embedding, k)

retrieved_chunks = [chunks[i] for i in indices[0]]

answer = generate_answer(retrieved_chunks, query)

print("\nFinal Answer:\n")
print(answer)