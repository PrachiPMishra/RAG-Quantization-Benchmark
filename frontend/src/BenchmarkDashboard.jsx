// Real numbers from results_rag_v2.json (bench.py), 9 questions x 3 tiers,
// Qwen3-4B-Instruct. Same source the README's benchmark table is built from.
// No token counts exist in that dataset, so throughput is words/sec (real,
// derived) rather than an estimated tokens/sec.
const TIERS = [
  { key: "q4", label: "q4_K_M", ramMb: 2761, latencyMs: 4332, wordsPerSec: 5.63 },
  { key: "q8", label: "q8_0", ramMb: 4462, latencyMs: 6210, wordsPerSec: 3.41 },
  { key: "fp16", label: "fp16", ramMb: 8058, latencyMs: 9805, wordsPerSec: 1.88 },
];

const METRICS = [
  { key: "ramMb", label: "RAM USAGE", unit: "MB", format: (v) => v.toFixed(0) },
  { key: "latencyMs", label: "AVG LATENCY", unit: "ms", format: (v) => v.toFixed(0) },
  { key: "wordsPerSec", label: "THROUGHPUT", unit: "words/s", format: (v) => v.toFixed(2) },
];

export default function BenchmarkDashboard() {
  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h1>Benchmark Dashboard</h1>
        <p>Aggregate results across 9 benchmark questions per tier, Qwen3-4B-Instruct, real RAG retrieval.</p>
      </div>

      <div className="dashboard-callout">
        <strong>q4_K_M matches fp16 on answer accuracy</strong> while using ~34% of the RAM
        and running ~2.3x faster — quantization cost nothing here.
      </div>

      <div className="dashboard-metrics">
        {METRICS.map((metric) => {
          const max = Math.max(...TIERS.map((t) => t[metric.key]));
          return (
            <div className="metric-group" key={metric.key}>
              <h2>{metric.label}</h2>
              {TIERS.map((tier) => (
                <div className="metric-row" key={tier.key} style={{ "--tier-accent": `var(--tier-${tier.key})` }}>
                  <span className="metric-row-label">{tier.label}</span>
                  <span className="metric-row-bar">
                    <span style={{ width: `${(tier[metric.key] / max) * 100}%` }} />
                  </span>
                  <span className="metric-row-value">
                    {metric.format(tier[metric.key])} {metric.unit}
                  </span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
