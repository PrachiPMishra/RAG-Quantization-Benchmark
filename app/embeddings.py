from sentence_transformers import SentenceTransformer

_model = SentenceTransformer("all-MiniLM-L6-v2")

CHUNK_TOKENS = 200
OVERLAP_TOKENS = 40


def embed(text: str) -> list[float]:
    return _model.encode(text, normalize_embeddings=True).tolist()


def chunk_text(text: str) -> list[str]:
    tokenizer = _model.tokenizer
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    step = CHUNK_TOKENS - OVERLAP_TOKENS

    chunks = []
    for start in range(0, len(offsets), step):
        window = offsets[start : start + CHUNK_TOKENS]
        if not window:
            break
        char_start, char_end = window[0][0], window[-1][1]
        chunks.append(text[char_start:char_end])
    return chunks


def to_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(map(str, vector)) + "]"


def truncate_tokens(text: str, max_tokens: int) -> str:
    tokenizer = _model.tokenizer
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    if len(offsets) <= max_tokens:
        return text
    return text[: offsets[max_tokens - 1][1]]
