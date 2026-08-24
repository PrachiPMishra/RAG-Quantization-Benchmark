import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Gear,
  PaperPlaneTilt,
  UploadSimple,
  Sun,
  Moon,
  Trash,
  FilePdf,
  X,
} from "@phosphor-icons/react";
import BenchmarkDashboard from "./BenchmarkDashboard";

const API_BASE = "http://localhost:8001";

const MODEL_VARIANTS = [
  "qwen3:4b-instruct-2507-q4_K_M",
  "qwen3:4b-instruct-2507-q8_0",
  "qwen3:4b-instruct-2507-fp16",
];

function tierKey(variant) {
  const label = variant.split("-").pop();
  if (label.startsWith("q4")) return "q4";
  if (label.startsWith("q8")) return "q8";
  return "fp16";
}

const MODEL_TIERS = MODEL_VARIANTS.map((variant) => ({
  variant,
  label: variant.split("-").pop(),
  key: tierKey(variant),
}));

// Fixed scale each stat bar fills against — not the max ever seen, just a
// stable reference so bars are comparable across answers and tiers.
const READOUT_MAX = { latency: 20000, tokens: 900, ram: 8200 };

function readoutPct(value, max) {
  if (value === null || value === undefined) return 0;
  return Math.min(100, (value / max) * 100);
}

// Document identity colors — deliberately disjoint from the tier palette
// (cyan/amber/violet already mean model precision). Assigned by document id
// so the mapping is stable regardless of upload/removal order.
const DOCUMENT_COLORS = ["#14b8a6", "#fb7185", "#64748b", "#a16207"];

function colorForDocId(id) {
  return DOCUMENT_COLORS[id % DOCUMENT_COLORS.length];
}

const SOURCE_TAG_RE = /\[Source:\s*([^\]]+)\]/g;

// The model sometimes echoes the "[Source: filename]" tags we prefix onto
// retrieved chunks. Split the raw answer around those tags and render each
// as a colored chip instead of literal bracket text, keeping markdown intact
// for the surrounding text segments.
function renderAnswerWithSources(answer, activeDocs) {
  const nodes = [];
  let lastIndex = 0;
  let key = 0;
  const re = new RegExp(SOURCE_TAG_RE.source, "g");
  let match;
  while ((match = re.exec(answer)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(
        <ReactMarkdown key={key++} remarkPlugins={[remarkGfm]}>
          {answer.slice(lastIndex, match.index)}
        </ReactMarkdown>
      );
    }
    const filename = match[1].trim();
    const doc = activeDocs.find((d) => d.filename === filename);
    const color = doc ? colorForDocId(doc.id) : "var(--color-muted-foreground)";
    nodes.push(
      <span key={key++} className="source-chip" style={{ "--source-color": color }}>
        <span className="source-chip-dot" aria-hidden="true" />
        {filename}
      </span>
    );
    lastIndex = re.lastIndex;
  }
  if (lastIndex < answer.length) {
    nodes.push(
      <ReactMarkdown key={key++} remarkPlugins={[remarkGfm]}>
        {answer.slice(lastIndex)}
      </ReactMarkdown>
    );
  }
  return nodes;
}

const MAX_HISTORY = 50;

const ACCENTS = [
  { name: "Violet", value: "#7C3AED" },
  { name: "Cyan", value: "#0891B2" },
  { name: "Emerald", value: "#059669" },
  { name: "Rose", value: "#E11D48" },
];

function useTheme() {
  const [accent, setAccent] = useState(
    () => localStorage.getItem("rqb-accent") || ACCENTS[0].value
  );
  const [mode, setMode] = useState(() => localStorage.getItem("rqb-mode") || "dark");

  useEffect(() => {
    document.documentElement.style.setProperty("--color-accent", accent);
    localStorage.setItem("rqb-accent", accent);
  }, [accent]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("rqb-mode", mode);
  }, [mode]);

  return { accent, setAccent, mode, setMode };
}

export default function App() {
  const { accent, setAccent, mode, setMode } = useTheme();
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [view, setView] = useState("chat");

  const [file, setFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("");
  const [uploadError, setUploadError] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [activeIds, setActiveIds] = useState([]);
  const [autoSelect, setAutoSelect] = useState(true);

  const [question, setQuestion] = useState("");
  const [modelVariant, setModelVariant] = useState(MODEL_VARIANTS[0]);
  const [modelManuallySet, setModelManuallySet] = useState(false);
  const [asking, setAsking] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [history, setHistory] = useState([]);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, asking]);

  useEffect(() => {
    if (activeIds.length > 1 && !modelManuallySet) {
      setModelVariant(MODEL_VARIANTS[0]);
    }
  }, [activeIds, modelManuallySet]);

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setUploadStatus("");
    setUploadError(false);

    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_BASE}/ingest`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const data = await res.json();
      setDocuments((prev) => [...prev, { id: data.document_id, filename: data.filename }]);
      if (autoSelect) setActiveIds([data.document_id]);
      setUploadStatus(`Processed: ${data.chunks_created} chunks created`);
    } catch (err) {
      setUploadError(true);
      setUploadStatus(`Error: ${err.message}`);
    } finally {
      setUploading(false);
    }
  }

  function toggleActive(id) {
    setAutoSelect(false);
    setActiveIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }

  const noActiveDocs = documents.length > 0 && activeIds.length === 0;

  async function handleAsk() {
    if (!question.trim() || noActiveDocs) return;
    setAsking(true);

    const askedQuestion = question;
    const askedVariant = modelVariant;
    // Snapshot which documents were active for this question so citation
    // colors stay correct even if uploads/selection change afterward.
    const askedDocs = documents.filter((d) => activeIds.includes(d.id));
    setQuestion("");
    setPendingQuestion(askedQuestion);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: askedQuestion,
          model_variant: askedVariant,
          top_k: 3,
          document_ids: activeIds,
        }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `Request failed (${res.status})`);
      }
      const data = await res.json();
      setHistory((prev) =>
        [
          ...prev,
          {
            question: askedQuestion,
            answer: data.answer,
            latencyMs: data.latency_ms,
            modelUsed: data.model_used,
            tokens: data.tokens ?? null,
            ramMb: data.ram_mb ?? null,
            activeDocs: askedDocs,
          },
        ].slice(-MAX_HISTORY)
      );
    } catch (err) {
      setHistory((prev) =>
        [
          ...prev,
          {
            question: askedQuestion,
            answer: `Error: ${err.message}`,
            latencyMs: null,
            modelUsed: askedVariant,
            isError: true,
          },
        ].slice(-MAX_HISTORY)
      );
    } finally {
      setAsking(false);
      setPendingQuestion("");
    }
  }

  function handleComposerKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">RAGQuantBench</div>

        <div className="view-toggle" role="tablist" aria-label="View">
          <button
            role="tab"
            aria-selected={view === "chat"}
            className={`view-tab ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
          <button
            role="tab"
            aria-selected={view === "dashboard"}
            className={`view-tab ${view === "dashboard" ? "active" : ""}`}
            onClick={() => setView("dashboard")}
          >
            Dashboard
          </button>
        </div>

        <div className="sidebar-section">
          <h2>Document</h2>
          <label className="file-drop">
            <FilePdf size={18} weight="regular" aria-hidden="true" />
            <span className="doc-name" title={file ? file.name : undefined}>
              {file ? file.name : "Choose a PDF"}
            </span>
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button
            className="btn btn-primary btn-block"
            onClick={handleUpload}
            disabled={!file || uploading}
          >
            <UploadSimple size={16} weight="bold" aria-hidden="true" />
            {uploading ? "Uploading..." : "Upload"}
          </button>
          {uploadStatus && (
            <p className={`status ${uploadError ? "error" : ""}`}>{uploadStatus}</p>
          )}

          {documents.length > 0 && (
            <>
              <ul className="document-list">
                {documents.map((doc) => (
                  <li
                    key={doc.id}
                    className={activeIds.includes(doc.id) ? "active" : ""}
                    style={{ "--doc-color": colorForDocId(doc.id) }}
                  >
                    <label>
                      <input
                        type="checkbox"
                        checked={activeIds.includes(doc.id)}
                        onChange={() => toggleActive(doc.id)}
                      />
                      <span className="doc-dot" aria-hidden="true" />
                      <span className="doc-name" title={doc.filename}>
                        {doc.filename}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <p className="status">
                {activeIds.length > 0
                  ? `Active: ${documents
                      .filter((d) => activeIds.includes(d.id))
                      .map((d) => d.filename)
                      .join(", ")}`
                  : "No documents active"}
              </p>
            </>
          )}
        </div>

        <div className="sidebar-section">
          <h2>Model tier</h2>
          <div className="tier-chips" role="radiogroup" aria-label="Model tier">
            {MODEL_TIERS.map(({ variant, label, key }) => {
              const isActive = modelVariant === variant;
              return (
                <button
                  key={variant}
                  type="button"
                  role="radio"
                  aria-checked={isActive}
                  className={`tier-chip tier-chip-${key} ${isActive ? "active" : ""}`}
                  onClick={() => {
                    setModelManuallySet(true);
                    setModelVariant(variant);
                  }}
                >
                  <span className="tier-chip-dot" aria-hidden="true" />
                  {label}
                  {asking && isActive && (
                    <span className="tier-chip-live" aria-label="Generating" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="sidebar-spacer" />

        <button
          className="btn btn-ghost btn-block"
          onClick={() => setHistory([])}
          disabled={history.length === 0}
        >
          <Trash size={16} weight="regular" aria-hidden="true" />
          Clear chat
        </button>
        <button
          className="btn btn-ghost btn-block"
          onClick={() => setCustomizeOpen(true)}
          aria-haspopup="dialog"
        >
          <Gear size={16} weight="regular" aria-hidden="true" />
          Customize
        </button>
      </aside>

      <main className="chat-main">
        {view === "dashboard" ? (
          <BenchmarkDashboard />
        ) : (
          <>
        <div className="messages">
          {history.length === 0 && !asking && (
            <div className="empty-state">
              <p>Upload a PDF, pick a model, then ask a question.</p>
            </div>
          )}

          {history.map((item, i) => {
            const tier = item.modelUsed ? tierKey(item.modelUsed) : null;
            const tierAccent = tier ? `var(--tier-${tier})` : undefined;
            return (
              <div className="message-pair" key={i}>
                <div className="bubble bubble-user">
                  <p>{item.question}</p>
                </div>
                <div
                  className={`bubble bubble-assistant ${item.isError ? "bubble-error" : ""}`}
                  style={tierAccent ? { "--tier-accent": tierAccent } : undefined}
                >
                  <div className="answer">
                    {renderAnswerWithSources(item.answer, item.activeDocs || [])}
                  </div>
                  <p className="meta">{item.modelUsed}</p>
                  {!item.isError && (
                    <div className="readout" style={tierAccent ? { "--tier-accent": tierAccent } : undefined}>
                      <div className="readout-cell">
                        <span className="readout-label">LATENCY</span>
                        <span className="readout-value">
                          {item.latencyMs != null ? `${(item.latencyMs / 1000).toFixed(1)}s` : "—"}
                        </span>
                        <span className="readout-bar">
                          <span style={{ width: `${readoutPct(item.latencyMs, READOUT_MAX.latency)}%` }} />
                        </span>
                      </div>
                      <div className="readout-cell">
                        <span className="readout-label">TOKENS</span>
                        <span className="readout-value">{item.tokens ?? "—"}</span>
                        <span className="readout-bar">
                          <span style={{ width: `${readoutPct(item.tokens, READOUT_MAX.tokens)}%` }} />
                        </span>
                      </div>
                      <div className="readout-cell">
                        <span className="readout-label">RAM</span>
                        <span className="readout-value">
                          {item.ramMb != null ? `${(item.ramMb / 1024).toFixed(1)}GB` : "—"}
                        </span>
                        <span className="readout-bar">
                          <span style={{ width: `${readoutPct(item.ramMb, READOUT_MAX.ram)}%` }} />
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {asking && (
            <div className="message-pair">
              <div className="bubble bubble-user">
                <p>{pendingQuestion}</p>
              </div>
              <div className="bubble bubble-assistant bubble-pending">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="composer-wrap">
          <div className="composer">
            <textarea
              className="composer-input"
              placeholder="Ask a question about the uploaded PDF..."
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleComposerKeyDown}
              rows={1}
            />
            <button
              className="btn btn-send"
              onClick={handleAsk}
              disabled={!question.trim() || asking || noActiveDocs}
              aria-label="Send question"
            >
              <PaperPlaneTilt size={18} weight="fill" aria-hidden="true" />
            </button>
          </div>
          {noActiveDocs ? (
            <p className="composer-hint error">Select at least one document to ask a question.</p>
          ) : (
            <p className="composer-hint">Tip: try /short or /detail before your question</p>
          )}
        </div>
          </>
        )}
      </main>

      {customizeOpen && (
        <div className="modal-overlay" onClick={() => setCustomizeOpen(false)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="Customize appearance"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h3>Customize</h3>
              <button
                className="btn btn-icon"
                onClick={() => setCustomizeOpen(false)}
                aria-label="Close"
              >
                <X size={18} weight="bold" aria-hidden="true" />
              </button>
            </div>

            <div className="modal-section">
              <h4>Theme</h4>
              <div className="mode-toggle">
                <button
                  className={`mode-btn ${mode === "dark" ? "active" : ""}`}
                  onClick={() => setMode("dark")}
                >
                  <Moon size={16} weight="regular" aria-hidden="true" />
                  Dark
                </button>
                <button
                  className={`mode-btn ${mode === "light" ? "active" : ""}`}
                  onClick={() => setMode("light")}
                >
                  <Sun size={16} weight="regular" aria-hidden="true" />
                  Light
                </button>
              </div>
            </div>

            <div className="modal-section">
              <h4>Accent color</h4>
              <div className="swatch-row">
                {ACCENTS.map((a) => (
                  <button
                    key={a.value}
                    className={`swatch ${accent === a.value ? "active" : ""}`}
                    style={{ background: a.value, color: a.value }}
                    onClick={() => setAccent(a.value)}
                    aria-label={`${a.name} accent`}
                    aria-pressed={accent === a.value}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
