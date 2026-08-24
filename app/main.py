import io
import os
import re
import time

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf import PdfReader

from db import get_connection
from embeddings import chunk_text, embed, to_vector_literal

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_URL = f"{OLLAMA_HOST}/api/generate"

CLASSIFIER_MODEL = "qwen3:4b-instruct-2507-q4_K_M"

# Safety net against runaway generation, not a length-shaping mechanism.
# Actual length comes from the prompt instruction + post-generation condensing.
UNIVERSAL_NUM_PREDICT = 900

# Broader per-document coverage for multi-doc summary questions, which skip
# map-reduce and answer from top-k retrieval instead.
MULTI_DOC_SUMMARY_TOP_K = 3

# Multi-doc answers should be selective, not exhaustive — keep generation short
# regardless of tier to bound latency across several documents' worth of context.
MULTI_DOC_NUM_PREDICT = 400
MULTI_DOC_CONDENSE_NUM_PREDICT = 300


def tier_instruction(command_length: str | None) -> str:
    if command_length == "short":
        return "Be brief and to the point."
    if command_length == "detailed":
        return "Answer thoroughly and comprehensively."
    return "Answer clearly with the needed detail."


async def maybe_condense(
    answer: str, tokens: int, model: str, command_length: str | None, is_multi_doc: bool = False
) -> tuple[str, int]:
    word_count = len(answer.split())
    if command_length == "short" and word_count > 80:
        limit = 60
    elif command_length is None and word_count > 200:
        limit = 150
    else:
        return answer, tokens

    num_predict = MULTI_DOC_CONDENSE_NUM_PREDICT if is_multi_doc else UNIVERSAL_NUM_PREDICT
    condensed, condensed_tokens = await ollama_generate(
        model,
        f"Condense this to under {limit} words while keeping the key facts: {answer}",
        num_predict,
    )
    return condensed.strip(), condensed_tokens


COMMANDS = [
    ("/detailed", "detailed"),
    ("/detail", "detailed"),
    ("/long", "detailed"),
    ("/short", "short"),
]


def parse_command(question: str) -> tuple[str, str | None]:
    stripped = question.strip()
    lower = stripped.lower()
    for cmd, length in COMMANDS:
        if lower.startswith(cmd):
            return stripped[len(cmd):].strip(), length
    return stripped, None


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str
    model_variant: str
    top_k: int = 3
    document_ids: list[int] | None = None


class ChatResponse(BaseModel):
    answer: str
    latency_ms: float
    model_used: str
    retrieved_chunks: list[str]
    tokens: int | None = None
    ram_mb: float | None = None


def require_document_ids(document_ids: list[int] | None) -> None:
    if document_ids is not None and len(document_ids) == 0:
        raise HTTPException(status_code=400, detail="Please select at least one document.")


def resolve_document_ids(cur, document_ids: list[int] | None) -> list[int]:
    if document_ids:
        return document_ids
    cur.execute("SELECT id FROM documents ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    return [row[0]] if row else []


async def ollama_generate(model: str, prompt: str, num_predict: int) -> tuple[str, int]:
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": num_predict},
            },
        )
    resp.raise_for_status()
    elapsed_ms = (time.perf_counter() - start) * 1000
    data = resp.json()
    print(
        f"[ollama] model={model} num_predict={num_predict} prompt_chars={len(prompt)} "
        f"took={elapsed_ms:.0f}ms",
        flush=True,
    )
    return data["response"], data.get("eval_count", 0)


async def get_ram_mb(model: str) -> float | None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{OLLAMA_HOST}/api/ps")
    resp.raise_for_status()
    for m in resp.json().get("models", []):
        if m.get("model") == model or m.get("name") == model:
            size = m.get("size_vram", m.get("size"))
            return size / (1024 * 1024) if size is not None else None
    return None


CLASSIFICATION_TOKEN_RE = re.compile(r"\b(SUMMARY|SPECIFIC)\b", re.IGNORECASE)


async def is_summary_query(question: str) -> bool:
    prompt = (
        "Classify each question as SUMMARY (asking for a broad overview) or SPECIFIC "
        "(asking about a particular fact).\n\n"
        "Examples:\n"
        "Q: whats in the pdf? -> SUMMARY\n"
        "Q: what is this document about -> SUMMARY\n"
        "Q: what does the Calvin cycle use? -> SPECIFIC\n"
        "Q: where does photosynthesis occur? -> SPECIFIC\n\n"
        "Now classify this question:\n"
        f"Q: {question} ->"
    )
    classification, _ = await ollama_generate(CLASSIFIER_MODEL, prompt, 20)
    match = CLASSIFICATION_TOKEN_RE.search(classification)
    token = match.group(1).upper() if match else classification.strip()
    is_summary = token == "SUMMARY"
    print(f"[classify] {question!r} -> {token!r} (summary={is_summary})", flush=True)
    return is_summary


SUMMARY_REDUCE_NUM_PREDICT = {"short": 200, "detailed": 700}
SUMMARY_REDUCE_DEFAULT_NUM_PREDICT = 350


async def summarize_document(
    chunks: list[str], model: str, command_length: str | None
) -> tuple[str, int]:
    chunk_summaries = []
    for chunk in chunks:
        summary, _ = await ollama_generate(
            model,
            "Summarize the key point of this section in your own words, in one "
            f"sentence, under 25 words. Do not copy phrases directly from the text — "
            f"paraphrase.\n\n{chunk}",
            60,
        )
        chunk_summaries.append(summary.strip())

    combined = "\n".join(chunk_summaries)
    final_prompt = (
        "Write a comprehensive but focused overview covering the main themes and "
        "topics from these section summaries, in your own words. Aim for 300-500 "
        "words — cover breadth over exhaustive detail. Do not simply list or repeat "
        f"every item verbatim; synthesize.\n\n{combined}"
    )

    # Fixed per-tier budgets, independent of chunk count — a summary should stay
    # a summary regardless of document size, not grow to "cover everything."
    num_predict = SUMMARY_REDUCE_NUM_PREDICT.get(command_length, SUMMARY_REDUCE_DEFAULT_NUM_PREDICT)
    return await ollama_generate(model, final_prompt, num_predict)


def retrieve_chunks(question: str, top_k: int, document_ids: list[int] | None = None) -> list[str]:
    query_vector = to_vector_literal(embed(question))

    chunks = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            doc_ids = resolve_document_ids(cur, document_ids)
            for doc_id in doc_ids:
                cur.execute(
                    "SELECT c.chunk_text, d.filename FROM chunks c "
                    "JOIN documents d ON d.id = c.document_id "
                    "WHERE c.document_id = %s "
                    "ORDER BY c.embedding <=> %s::vector LIMIT %s",
                    (doc_id, query_vector, top_k),
                )
                chunks.extend(f"[Source: {filename}]\n{chunk_text}" for chunk_text, filename in cur.fetchall())

    return chunks


def fetch_full_document(document_ids: list[int] | None) -> list[str]:
    chunks = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            doc_ids = resolve_document_ids(cur, document_ids)
            for doc_id in doc_ids:
                cur.execute(
                    "SELECT chunk_text FROM chunks WHERE document_id = %s ORDER BY chunk_index",
                    (doc_id,),
                )
                chunks.extend(row[0] for row in cur.fetchall())

    return chunks


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    require_document_ids(req.document_ids)
    start = time.perf_counter()

    question, command_length = parse_command(req.question)

    is_multi_doc = bool(req.document_ids and len(req.document_ids) > 1)
    is_summary = await is_summary_query(question)

    if is_summary and not is_multi_doc:
        retrieved_chunks = fetch_full_document(req.document_ids)
        answer, tokens = await summarize_document(retrieved_chunks, req.model_variant, command_length)
    else:
        # Multi-doc summaries skip map-reduce (too slow across several full documents)
        # and fall back to top-k retrieval with wider coverage per document instead.
        top_k = MULTI_DOC_SUMMARY_TOP_K if is_summary else req.top_k
        retrieved_chunks = retrieve_chunks(question, top_k, req.document_ids)
        context = "\n\n".join(retrieved_chunks)
        if is_multi_doc:
            instruction = (
                "The following context comes from multiple documents, each labeled with its "
                "source filename. When answering, be precise about which document each fact "
                "comes from, and cite it using the exact tag format [Source: filename] right "
                "after the relevant sentence or bullet — do not paraphrase the filename into "
                "prose (e.g. write '[Source: report.pdf]', not 'the document report.pdf'). If "
                "the question asks to compare, contrast, or find similarities/differences, "
                "explicitly address content from each relevant document, tagging each. If only "
                "one document actually contains relevant information for this question, answer "
                "from that one, tag it, and note that the other document didn't contain relevant "
                "content — don't force a comparison that isn't there. " + tier_instruction(command_length)
            )
        else:
            instruction = (
                "Answer the question directly using only the provided context. If the answer "
                "contains multiple items, steps, or a list (like a plan, a set of problems, or "
                "sequential steps), format it as a bulleted or numbered list rather than one "
                "dense paragraph. Bold key terms or item names for readability. Do not restate "
                "the question, do not add a 'Final Answer' section, and do not second-guess or "
                "re-derive your answer — give one clear response. " + tier_instruction(command_length)
            )
        prompt = f"{instruction}\n\nContext: {context}\n\nQuestion: {question}\n\nAnswer:"
        num_predict = MULTI_DOC_NUM_PREDICT if is_multi_doc else UNIVERSAL_NUM_PREDICT
        answer, tokens = await ollama_generate(req.model_variant, prompt, num_predict)

    answer, tokens = await maybe_condense(
        answer, tokens, req.model_variant, command_length, is_multi_doc
    )

    latency_ms = (time.perf_counter() - start) * 1000
    ram_mb = await get_ram_mb(req.model_variant)

    return ChatResponse(
        answer=answer,
        latency_ms=latency_ms,
        model_used=req.model_variant,
        retrieved_chunks=retrieved_chunks,
        tokens=tokens,
        ram_mb=ram_mb,
    )


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    pdf_bytes = await file.read()
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)

    chunks = chunk_text(text)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (filename) VALUES (%s) RETURNING id",
                (file.filename,),
            )
            document_id = cur.fetchone()[0]

            for index, chunk in enumerate(chunks):
                cur.execute(
                    "INSERT INTO chunks (document_id, chunk_text, embedding, chunk_index) "
                    "VALUES (%s, %s, %s::vector, %s)",
                    (document_id, chunk, to_vector_literal(embed(chunk)), index),
                )
        conn.commit()

    return {"document_id": document_id, "filename": file.filename, "chunks_created": len(chunks)}


class RetrieveRequest(BaseModel):
    question: str
    top_k: int
    document_ids: list[int] | None = None


@app.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    require_document_ids(req.document_ids)
    return {"chunks": retrieve_chunks(req.question, req.top_k, req.document_ids)}
