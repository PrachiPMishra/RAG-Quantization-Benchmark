import json
import os
from collections import defaultdict

import httpx

PREVIOUS_RESULTS_FILE = "results_rag.json"
RESULTS_FILE = "results_rag_v2.json"

QUESTIONS = [
    "Where in the plant cell does photosynthesis occur?",
    "What are the two main stages of photosynthesis and where does each take place?",
    "What byproduct is released during the light-dependent reactions, and where does it come from?",
    "What does the Calvin cycle use to convert carbon dioxide into glucose?",
    "Why is photosynthesis essential to life on Earth?",
    "Write out the overall chemical equation for photosynthesis exactly as given, including all coefficients.",
    "List every specific chemical compound or molecule mentioned in the passage, in the order they appear.",
    "If a plant's stroma were damaged but its thylakoids remained fully functional, which specific products would the plant still be able to produce, and which would it lose the ability to make?",
    "Based on the passage, if oxygen production suddenly stopped in a leaf, which specific reaction and which specific molecule would most likely be malfunctioning, and why?",
]

MODEL_VARIANTS = [
    "qwen3:4b-instruct-2507-q4_K_M",
    "qwen3:4b-instruct-2507-q8_0",
    "qwen3:4b-instruct-2507-fp16",
]

CHAT_URL = "http://localhost:8001/chat"
PS_URL = "http://localhost:11434/api/ps"


def get_ram_mb(client: httpx.Client, model_variant: str) -> float | None:
    resp = client.get(PS_URL, timeout=10)
    resp.raise_for_status()
    for m in resp.json().get("models", []):
        if m.get("model") == model_variant or m.get("name") == model_variant:
            size = m.get("size_vram", m.get("size"))
            return size / (1024 * 1024) if size is not None else None
    return None


def run() -> list[dict]:
    results = []
    with httpx.Client(timeout=None) as client:
        for question in QUESTIONS:
            for model_variant in MODEL_VARIANTS:
                resp = client.post(
                    CHAT_URL,
                    json={
                        "question": question,
                        "model_variant": model_variant,
                        "top_k": 3,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                ram_mb = get_ram_mb(client, model_variant)

                word_count = len(data["answer"].split())
                results.append(
                    {
                        "question": question,
                        "model_variant": model_variant,
                        "answer": data["answer"],
                        "latency_ms": data["latency_ms"],
                        "ram_mb": ram_mb,
                        "retrieved_chunks": data["retrieved_chunks"],
                        "word_count": word_count,
                    }
                )
                ram_str = f"{ram_mb:.0f}MB" if ram_mb is not None else "RAM unknown"
                print(
                    f"[{model_variant}] {question[:40]!r} -> "
                    f"{data['latency_ms']:.0f}ms, {word_count}w, {ram_str}"
                )
    return results


def print_summary(results: list[dict]) -> None:
    by_model = defaultdict(list)
    for r in results:
        by_model[r["model_variant"]].append(r)

    print("\n=== Summary by model_variant ===")
    print(f"{'model_variant':35} {'avg_latency_ms':>15} {'avg_words':>10} {'avg_ram_mb':>12}")
    for model_variant, rows in by_model.items():
        avg_latency = sum(r["latency_ms"] for r in rows) / len(rows)
        avg_words = sum(r["word_count"] for r in rows) / len(rows)
        ram_values = [r["ram_mb"] for r in rows if r["ram_mb"] is not None]
        avg_ram = sum(ram_values) / len(ram_values) if ram_values else None
        avg_ram_str = f"{avg_ram:.0f}" if avg_ram is not None else "n/a"
        print(f"{model_variant:35} {avg_latency:15.1f} {avg_words:10.1f} {avg_ram_str:>12}")

    print("\n=== Answers per question ===")
    by_question = defaultdict(list)
    for r in results:
        by_question[r["question"]].append(r)
    for question, rows in by_question.items():
        print(f"\nQ: {question}")
        print("  Retrieved chunks:")
        for i, chunk in enumerate(rows[0]["retrieved_chunks"]):
            print(f"    [{i}] {chunk[:200]!r}")
        for r in rows:
            print(f"  [{r['model_variant']}] {r['answer']}")


def print_comparison(results: list[dict]) -> None:
    if not os.path.exists(PREVIOUS_RESULTS_FILE):
        return
    with open(PREVIOUS_RESULTS_FILE) as f:
        previous = json.load(f)
    if not previous:
        return

    def averages(rows: list[dict]) -> dict[str, tuple[float, float]]:
        by_model = defaultdict(list)
        for r in rows:
            by_model[r["model_variant"]].append(r)
        return {
            m: (
                sum(r["latency_ms"] for r in rs) / len(rs),
                sum(r.get("word_count", len(r["answer"].split())) for r in rs) / len(rs),
            )
            for m, rs in by_model.items()
        }

    prev_avg = averages(previous)
    new_avg = averages(results)

    print("\n=== vs. previous run (results_rag.json) ===")
    print(f"{'model_variant':35} {'latency_ms':>22} {'words':>18}")
    for model_variant in new_avg:
        old_latency, old_words = prev_avg.get(model_variant, (None, None))
        new_latency, new_words = new_avg[model_variant]
        lat_str = f"{old_latency:.0f} -> {new_latency:.0f}" if old_latency else f"n/a -> {new_latency:.0f}"
        words_str = f"{old_words:.1f} -> {new_words:.1f}" if old_words else f"n/a -> {new_words:.1f}"
        print(f"{model_variant:35} {lat_str:>22} {words_str:>18}")


def main() -> None:
    results = run()

    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print_summary(results)
    print_comparison(results)


if __name__ == "__main__":
    main()
