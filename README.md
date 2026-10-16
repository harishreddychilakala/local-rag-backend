# Local RAG Assistant with Neon PostgreSQL & Authentication

A complete, production-grade Retrieval-Augmented Generation (RAG) system with a **FastAPI** backend, **FAISS** vector search, **SentenceTransformers (`all-MiniLM-L6-v2`)**, live **Wikipedia/DuckDuckGo online search fallback**, **Groq (LLaMA 3.1)** integration, **Neon PostgreSQL** persistence, **JWT User Authentication (Sign Up & Login)**, and a modern **React (Vite)** frontend.

---

## 🏛️ Project Architecture

```text
                               +---------------------------------------------+
                               |            React + Vite Frontend            |
                               | (Chat UI, Sessions, JWT Auth, Status Badge) |
                               +---------------------------------------------+
                                       |                             |
                                       | HTTP / REST                 | Auth Header (Bearer)
                                       v                             v
+---------------------------------------------------------------------------------------------------+
|                                      FastAPI Backend Service                                      |
|                                                                                                   |
|  +---------------------------+  +-------------------------------+  +---------------------------+  |
|  |     Auth & Sessions       |  |      RAG Retrieval Pipeline   |  |   Online Knowledge Search |  |
|  |  - /api/auth/signup       |  |  - FAISS Vector Search (384d) |  |  - Wikipedia REST API     |  |
|  |  - /api/auth/login        |  |  - SentenceTransformer Model  |  |  - DuckDuckGo Instant API |  |
|  |  - /api/sessions          |  |  - Clean Chunk Parser         |  |  - Live Search Fallback   |  |
|  +---------------------------+  +-------------------------------+  +---------------------------+  |
+---------------------------------------------------------------------------------------------------+
             |                                    |                                    |
             v                                    v                                    v
+------------------------+           +------------------------+           +------------------------+
|    Neon PostgreSQL     |           |   Persistent Storage   |           |    Groq Cloud API      |
| (Users, Sessions,      |           |     (`./data` Volume)  |           | (LLaMA 3.1-8b-instant  |
|  Messages, Chunks)     |           | - `faiss_index.bin`    |           |  tutor response        |
|                        |           | - `chunks_cache.json`  |           |  synthesis)            |
+------------------------+           +------------------------+           +------------------------+
```

---

## 📋 Prerequisites

- **Python**: 3.10+ (tested through 3.14)
- **Node.js**: v18+ or v20+
- **Docker & Docker Compose**: (Optional, for containerized deployment)
- **Neon PostgreSQL**: Cloud serverless Postgres database (or any PostgreSQL 15+ database)
- **Groq API Key**: Optional, for LLaMA 3.1 answer generation ([console.groq.com/keys](https://console.groq.com/keys))

---

## 🗄️ Neon PostgreSQL Setup

1. Create a free account at [neon.tech](https://neon.tech) and create a new project.
2. Under **Connection Details**, copy your connection string (choose **Pooled connection** with `sslmode=require`).
3. Add the connection string to your `.env` file under `DATABASE_URL`:
   ```env
   DATABASE_URL=postgresql+psycopg2://neondb_owner:YOUR_PASSWORD@ep-YOUR-ENDPOINT-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. **Automatic Table Creation**: The application automatically creates all necessary tables (`users`, `chat_sessions`, `chat_messages`, `document_chunks`) on initial startup via SQLAlchemy. No manual SQL scripts are required!

---

## 🔐 Environment Variables Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `DATABASE_URL` | Neon PostgreSQL pooled connection string | `postgresql+psycopg2://neondb_owner:password@ep-...neon.tech/neondb?sslmode=require` |
| `JWT_SECRET` | Secret key used to sign and verify JWT tokens | `local-rag-jwt-secret-key-change-in-production-2026` |
| `GROQ_API_KEY` | Groq API key for LLaMA 3.1 LLM generation *(optional)* | `gsk_...` |
| `VITE_API_URL` | Base API URL accessible by the browser frontend | `http://localhost:8000` |

---

## 💻 Local Development Instructions

### 1. Backend Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Run the FastAPI server
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

The backend starts at **`http://127.0.0.1:8000`**.

### 2. Frontend Setup

```bash
cd frontend

# Install Node dependencies
npm install

# Start Vite development server
npm run dev
```

The frontend interface will open at **`http://localhost:5173`**.

---

## 🐳 Docker Setup & Startup Commands

### Run Everything with Docker Compose

```bash
# Build and run containers in background
docker compose up --build -d

# Check running container status
docker compose ps

# View live backend logs
docker compose logs -f backend

# Stop all containers
docker compose down
```

### 🌐 Service URLs
* **Frontend Web Application**: [http://localhost:3000](http://localhost:3000) (Production Nginx)
* **Backend API Root**: [http://localhost:8000](http://localhost:8000)
* **API Documentation (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Backend Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 🔄 Database Migrations & Schema

The schema is defined in `database.py` using SQLAlchemy:
* **`users`**: User identity, email (unique index), hashed bcrypt password.
* **`chat_sessions`**: User chat threads with timestamps.
* **`chat_messages`**: Questions and answers tagged with role (`user` / `bot`) and retrieved source chunks.
* **`document_chunks`**: Document chunks from ingested PDFs with chunk index and source file tags.

If you ever need to reset or migrate tables programmatically:
```python
import database
# Drops all tables and recreates them
database.Base.metadata.drop_all(bind=database.engine)
database.Base.metadata.create_all(bind=database.engine)
```

---

## 📚 How PDF Ingestion & Retrieval Work

1. **Document Loading**: On startup, `pypdf` extracts text from `DBMS-RAG.pdf`, `OS-RAG.pdf`, and `data.pdf`.
2. **Chunking**: Text is split into structured chunks based on section delimiters.
3. **Embedding Generation**: `all-MiniLM-L6-v2` encodes text chunks into 384-dimensional dense vectors.
4. **Vector Persistence**: Vectors are stored in a FAISS FlatL2 index and saved to `./data/faiss_index.bin` along with `./data/chunks_cache.json`. Subsequent boots load from disk in under 10ms, surviving container restarts.
5. **Retrieval**:
   * For a user query, FAISS retrieves the top $k=5$ closest chunks.
   * If similarity distance is under the relevance threshold ($< 1.35$), the local document chunk is returned as a formatted direct answer.
   * If no local chunk matches, the system **automatically falls back to live online knowledge retrieval** (Wikipedia REST API + DuckDuckGo fallback).
6. **LLM Synthesis**: If a `GROQ_API_KEY` is provided, LLaMA 3.1 synthesizes the retrieved context into a tutor-level response. If not configured, the system cleanly formats the extract directly without warning banners or debug metadata.

---

## 🧪 Automated Testing

Run the included automated test suite:

```bash
python tests/test_suite.py
```

Tests verify:
- ✅ Password hashing with Bcrypt and JWT token generation / decoding.
- ✅ Neon PostgreSQL live connectivity, table schemas, and message persistence.
- ✅ FAISS vector index disk serialization and cache survival.
- ✅ Groq response synthesis with mocks.
- ✅ Wikipedia & DuckDuckGo live online search fallback.

---

## 🔧 Troubleshooting Common Errors

| Error | Cause | Solution |
| :--- | :--- | :--- |
| `UnicodeEncodeError: 'charmap' codec can't encode...` | Windows console code page (cp1252) failing to print emojis. | Handled automatically in `app.py` with `sys.stdout.reconfigure(encoding='utf-8')`. |
| `ConnectionRefusedError: [WinError 10061]` | Backend server is not running on port 8000. | Start backend: `python -m uvicorn app:app --port 8000`. |
| `AttributeError: module 'bcrypt' has no attribute '__about__'` | Passlib bug with bcrypt 4+. | Direct `bcrypt.hashpw` is used in `database.py` to ensure complete compatibility. |
| `password cannot be longer than 72 bytes` | Passlib wrap bug check. | Handled by using direct `bcrypt` calls rather than passlib internals. |
| `could not connect to server: Connection refused` (Neon) | Missing SSL query parameter. | Ensure `?sslmode=require` is present at the end of `DATABASE_URL`. |

---

## ☁️ Deployment Considerations for AWS EC2

When deploying to AWS EC2 in the future:
1. **Security Groups**:
   * Allow Inbound port `80` (HTTP) and `443` (HTTPS) from anywhere (`0.0.0.0/0`).
   * Keep port `8000` internal if using Nginx reverse proxy, or expose it only if the frontend directly talks to port `8000`.
2. **Environment Configuration**:
   * Set `VITE_API_URL=http://<EC2-PUBLIC-IP>:8000` or configure Nginx on EC2 to reverse proxy `/api` and `/ask` to `http://localhost:8000`.
3. **Persistent Storage**:
   * Mount `./data` to an attached Amazon EBS volume so your FAISS vector cache is preserved across EC2 stop/start events.
4. **SSL / HTTPS**:
   * Use Certbot with Let's Encrypt for free SSL certificates on custom domain names.
