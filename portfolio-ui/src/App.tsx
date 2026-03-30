import { useState } from "react";
import ReactMarkdown from "react-markdown";

const exampleQuestions = [
  "What sectors did the fund increase investment in?",
  "What were the top 5 new positions by value?",
  "How much did the fund's investment in the Equity sector grow?",
  "Which companies had the largest decreases?",
  "What companies had the largest increases?",
  "What is the current portfolio concentration?",
  "List all companies in the Healthcare sector",
  "How does the Equity sector compare to last quarter?",
  "What was the portfolio turnover rate?",
  "Show me the biggest position changes.",
];

const periodOptions = [
  { label: "2023 Q1", value: "2023-03-31" },
  { label: "2023 Q2", value: "2023-06-30" },
  { label: "2023 Q3", value: "2023-09-30" },
  { label: "2023 Q4", value: "2023-12-31" },
  { label: "2024 Q1", value: "2024-03-31" },
  { label: "2024 Q2", value: "2024-06-30" },
  { label: "2024 Q3", value: "2024-09-30" },
  { label: "2024 Q4", value: "2024-12-31" },
  { label: "2025 Q1", value: "2025-03-31" },
  { label: "2025 Q2", value: "2025-06-30" },
  { label: "2025 Q3", value: "2025-09-30" },
  { label: "2025 Q4", value: "2025-12-31" },
  { label: "2026 Q1", value: "2026-03-31" },
];

function App() {
  const [geminiApiKey, setGeminiApiKey] = useState("");
  const [secEmail, setSecEmail] = useState("");
  const [cik, setCik] = useState("");
  const [periodPrev, setPeriodPrev] = useState("");
  const [periodCurr, setPeriodCurr] = useState("");
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async () => {
    setLoading(true);
    setResponse(null);
    setError("");

    if (!geminiApiKey.trim()) {
      setError("Please enter your Gemini API key.");
      setLoading(false);
      return;
    }

    if (!secEmail.trim()) {
      setError("Please enter your email address.");
      setLoading(false);
      return;
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(secEmail.trim())) {
      setError("Please enter a valid email address.");
      setLoading(false);
      return;
    }

    if (!cik.trim()) {
      setError("Please enter the company's CIK.");
      setLoading(false);
      return;
    }

    if (!/^\d+$/.test(cik.trim())) {
      setError("CIK must contain digits only.");
      setLoading(false);
      return;
    }

    if (!periodPrev || !periodCurr) {
      setError("Please select both previous and current periods.");
      setLoading(false);
      return;
    }

    if (periodPrev >= periodCurr) {
      setError("Current period must be later than previous period.");
      setLoading(false);
      return;
    }

    if (!question.trim()) {
      setError("Please enter a question.");
      setLoading(false);
      return;
    }

    try {
      const res = await fetch(
        "https://portfolio-analysis-ai-agent-production.up.railway.app/analyze",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            gemini_api_key: geminiApiKey,
            sec_email: secEmail,
            cik,
            period_prev: periodPrev,
            period_curr: periodCurr,
            question,
          }),
        }
      );

      const data = await res.json();

      if (!res.ok) {
        setError(data.detail || "Something went wrong while running the analysis.");
        setLoading(false);
        return;
      }

      setResponse(data);
    } catch (err) {
      console.error(err);
      setError("Could not connect to the server. Please try again.");
    }

    setLoading(false);
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        width: "100%",
        background: "#f5f7fb",
        padding: "20px",
        boxSizing: "border-box",
        fontFamily: "Arial, sans-serif",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "1800px",
          margin: "0 auto",
          display: "grid",
          gridTemplateColumns: "1.35fr 0.65fr",
          gap: "20px",
          alignItems: "start",
        }}
      >
        <div
          style={{
            background: "#ffffff",
            borderRadius: "18px",
            padding: "28px",
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
          }}
        >
          <h1
            style={{
              marginTop: 0,
              marginBottom: "20px",
              fontSize: "38px",
              lineHeight: 1.05,
              textAlign: "center",
            }}
          >
            13F Portfolio Analysis Agent
          </h1>

          <div style={{ display: "grid", gap: "18px" }}>
            <div>
              <label
                style={{ display: "block", marginBottom: "6px", fontWeight: 600 }}
              >
                Gemini API Key
              </label>
              <input
                type="password"
                placeholder="Enter your Gemini API key"
                value={geminiApiKey}
                onChange={(e) => setGeminiApiKey(e.target.value)}
                style={inputStyle}
              />
              <div style={helperTextStyle}>
                Get a Gemini API key from Google AI Studio and paste it here.
              </div>
              <div style={helperTextStyle}>
                Your API key is used only for this request and is not stored.
              </div>
            </div>

            <div>
              <label
                style={{ display: "block", marginBottom: "6px", fontWeight: 600 }}
              >
                Email Address
              </label>
              <input
                type="email"
                placeholder="Enter your email (required by SEC)"
                value={secEmail}
                onChange={(e) => setSecEmail(e.target.value)}
                style={inputStyle}
              />
            </div>

            <div>
              <label
                style={{ display: "block", marginBottom: "6px", fontWeight: 600 }}
              >
                Company's CIK
              </label>
              <input
                placeholder="Enter 10-digit CIK"
                value={cik}
                onChange={(e) => setCik(e.target.value)}
                style={inputStyle}
              />
            </div>

            <div
              style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}
            >
              <div>
                <label
                  style={{ display: "block", marginBottom: "6px", fontWeight: 600 }}
                >
                  Previous Period
                </label>
                <select
                  value={periodPrev}
                  onChange={(e) => setPeriodPrev(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">Select previous period</option>
                  {periodOptions.map((period) => (
                    <option key={period.value} value={period.value}>
                      {period.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  style={{ display: "block", marginBottom: "6px", fontWeight: 600 }}
                >
                  Current Period
                </label>
                <select
                  value={periodCurr}
                  onChange={(e) => setPeriodCurr(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">Select current period</option>
                  {periodOptions.map((period) => (
                    <option key={period.value} value={period.value}>
                      {period.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label
                style={{ display: "block", marginBottom: "6px", fontWeight: 600 }}
              >
                Question
              </label>
              <textarea
                placeholder="Ask a question about the portfolio"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={4}
                style={{
                  ...inputStyle,
                  resize: "vertical",
                  minHeight: "100px",
                }}
              />
            </div>

            {error && (
              <div
                style={{
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  color: "#b91c1c",
                  padding: "12px 14px",
                  borderRadius: "10px",
                  fontSize: "14px",
                  lineHeight: 1.5,
                }}
              >
                {error}
              </div>
            )}

            <button
              onClick={handleSubmit}
              disabled={loading}
              style={{
                background: "#0f172a",
                color: "#ffffff",
                border: "none",
                borderRadius: "10px",
                padding: "14px 18px",
                fontSize: "16px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {loading ? "Running..." : "Run Analysis"}
            </button>
          </div>

          <div
            style={{
              marginTop: "24px",
              padding: "22px",
              borderRadius: "12px",
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
            }}
          >
            <h2 style={{ marginTop: 0, marginBottom: "12px" }}>Answer</h2>

            {!response && (
              <div style={{ color: "#64748b", lineHeight: 1.6 }}>
                Your generated answer will appear here after you run the analysis.
              </div>
            )}

            {response && (
              <div
                style={{
                  lineHeight: 1.8,
                  color: "#0f172a",
                  fontSize: "16px",
                }}
              >
                <ReactMarkdown>{response.answer}</ReactMarkdown>
              </div>
            )}
          </div>
        </div>

        <div
          style={{
            background: "#ffffff",
            borderRadius: "18px",
            padding: "24px",
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            position: "sticky",
            top: "20px",
            maxHeight: "calc(100vh - 40px)",
            overflowY: "auto",
          }}
        >
          <h2 style={{ marginTop: 0, marginBottom: "10px", textAlign: "center" }}>
            Example Questions
          </h2>
          <div
            style={{
              color: "#475569",
              marginBottom: "16px",
              lineHeight: 1.5,
              textAlign: "center",
            }}
          >
            Click any example below to populate the question box.
          </div>

          <div
            style={{
              marginBottom: "18px",
              padding: "12px 14px",
              background: "#eff6ff",
              border: "1px solid #bfdbfe",
              borderRadius: "10px",
              color: "#1e3a8a",
              fontSize: "14px",
              lineHeight: 1.5,
            }}
          >
            <strong>How to get a Gemini API key:</strong>
            <br />
            Open Google AI Studio, create an API key, and paste it into the field
            on the left.
          </div>

          <div style={{ display: "grid", gap: "10px" }}>
            {exampleQuestions.map((item, index) => (
              <button
                key={index}
                onClick={() => setQuestion(item)}
                style={{
                  textAlign: "left",
                  background: "#f8fafc",
                  border: "1px solid #dbeafe",
                  borderRadius: "10px",
                  padding: "12px 14px",
                  cursor: "pointer",
                  color: "#0f172a",
                  lineHeight: 1.4,
                }}
              >
                {index + 1}. {item}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "12px 14px",
  borderRadius: "10px",
  border: "1px solid #cbd5e1",
  fontSize: "14px",
  boxSizing: "border-box",
  background: "#ffffff",
};

const helperTextStyle: React.CSSProperties = {
  marginTop: "6px",
  fontSize: "12px",
  color: "#64748b",
};

export default App;