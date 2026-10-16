from datetime import datetime, timedelta
import json
import os
import uuid
from typing import Optional, List, Dict, Any

import bcrypt
from dotenv import load_dotenv
from jose import jwt
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://neondb_owner:npg_CQKX2Aa3ySYz@ep-morning-band-b5c1beoc-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require",
)

# Ensure psycopg2 driver prefix if plain postgresql:// is provided
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

JWT_SECRET = os.getenv("JWT_SECRET", "local-rag-jwt-secret-key-change-in-production-2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7

# Initialize SQLAlchemy engine with connection pool resilience
try:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    DB_ENABLED = True
except Exception as e:
    print(f"[WARN] Database engine initialization failed: {e}")
    DB_ENABLED = False
    engine = None
    SessionLocal = None

Base = declarative_base()


# ── Database Models ───────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(255), default="New Chat", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user' | 'bot'
    content = Column(Text, nullable=False)
    sources = Column(Text, nullable=True)  # JSON-encoded array of source strings
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_file = Column(String(255), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Database Initialization ───────────────────────────────────────────────────

def init_db():
    if not DB_ENABLED or not engine:
        return False
    try:
        Base.metadata.create_all(bind=engine)
        print("[OK] Neon PostgreSQL database tables verified/created successfully.")
        return True
    except Exception as e:
        print(f"[WARN] Error creating database tables: {e}")
        return False


def get_db():
    if not DB_ENABLED or not SessionLocal:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Password & JWT Helpers ────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    # Use direct bcrypt for maximum compatibility
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    from datetime import timezone
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=JWT_EXPIRATION_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)



def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# ── Persistence Helpers ───────────────────────────────────────────────────────

def save_message_to_db(
    db: Session,
    session_id: str,
    role: str,
    content: str,
    sources: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    session_title: Optional[str] = None,
):
    if not db:
        return None
    try:
        # Verify or create session
        sess = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not sess:
            title = session_title or (content[:36] + "…" if len(content) > 36 else content)
            sess = ChatSession(id=session_id, user_id=user_id, title=title)
            db.add(sess)
            db.commit()
            db.refresh(sess)
        elif sess:
            if user_id and sess.user_id is None:
                sess.user_id = user_id
            if session_title and sess.title == "New Chat":
                sess.title = session_title
            sess.updated_at = func.now()
            db.commit()

        # Save message
        sources_json = json.dumps(sources) if sources else None
        msg = ChatMessage(session_id=session_id, role=role, content=content, sources=sources_json)
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg
    except Exception as e:
        db.rollback()
        print(f"[WARN] Failed to save message to Neon DB: {e}")
        return None


def get_user_chat_sessions(db: Session, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not db:
        return []
    try:
        query = db.query(ChatSession)
        if user_id:
            query = query.filter(ChatSession.user_id == user_id)
        else:
            # Unauthenticated requests must only see guest sessions, never registered users' chats
            query = query.filter(ChatSession.user_id.is_(None))
        sessions = query.order_by(ChatSession.updated_at.desc()).all()

        result = []
        for s in sessions:
            msgs = []
            for m in s.messages:
                msgs.append({
                    "id": m.id,
                    "type": m.role,
                    "text": m.content,
                    "sources": json.loads(m.sources) if m.sources else [],
                    "time": m.created_at.strftime("%I:%M %p") if m.created_at else "",
                })
            result.append({
                "id": s.id,
                "title": s.title,
                "date": s.created_at.strftime("%b %d") if s.created_at else "",
                "messages": msgs,
            })
        return result
    except Exception as e:
        print(f"[WARN] Error fetching sessions: {e}")
        return []


def delete_chat_session_from_db(db: Session, session_id: str, user_id: Optional[str] = None) -> bool:
    if not db:
        return False
    try:
        query = db.query(ChatSession).filter(ChatSession.id == session_id)
        if user_id:
            query = query.filter(ChatSession.user_id == user_id)
        else:
            query = query.filter(ChatSession.user_id.is_(None))
        sess = query.first()
        if sess:
            db.delete(sess)
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        print(f"[WARN] Error deleting session: {e}")
        return False


def sync_document_chunks_to_db(db: Session, chunks_with_sources: List[Dict[str, Any]]) -> int:
    """
    Persists parsed PDF chunks into Neon PostgreSQL if the document_chunks table is empty.
    """
    if not db:
        return 0
    try:
        count = db.query(DocumentChunk).count()
        if count > 0:
            return count

        new_chunks = []
        for item in chunks_with_sources:
            new_chunks.append(
                DocumentChunk(
                    source_file=item.get("source", "unknown"),
                    chunk_index=item.get("index", 0),
                    content=item.get("content", ""),
                )
            )
        db.bulk_save_objects(new_chunks)
        db.commit()
        print(f"[OK] Synced {len(new_chunks)} document chunks to Neon PostgreSQL.")
        return len(new_chunks)
    except Exception as e:
        db.rollback()
        print(f"[WARN] Failed to sync document chunks to DB: {e}")
        return 0
