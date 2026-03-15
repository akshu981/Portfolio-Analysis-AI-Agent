"""
13F Portfolio Analysis Agent — FastAPI Backend
Wraps the notebook logic into a single POST /analyze endpoint.
Users supply their own Gemini API key; nothing is stored server-side.

Deploy to Railway / Render:
  railway up   OR   render deploy
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator
import requests
import pandas as pd
from bs4 import BeautifulSoup
import time
import json
import re
from datetime import datetime
from collections import defaultdict
import google.generativeai as genai

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="13F Portfolio Analysis Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Lock this down to your Lovable domain in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    gemini_api_key: str
    sec_email: EmailStr
    cik: str
    period_prev: str   # e.g. "2025-06-30"
    period_curr: str   # e.g. "2025-09-30"
    question: str

    @field_validator("cik")
    @classmethod
    def pad_cik(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if not digits:
            raise ValueError("CIK must contain digits")
        return digits.zfill(10)

    @field_validator("period_prev", "period_curr")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be YYYY-MM-DD")
        return v


class AnalyzeResponse(BaseModel):
    answer: str
    portfolio_summary: dict
    cik: str
    period_prev: str
    period_curr: str


# ---------------------------------------------------------------------------
# SEC helpers — ported directly from notebook
# ---------------------------------------------------------------------------

def make_headers(email: str) -> dict:
    return {"User-Agent": f"13F-Agent {email}"}


def get_filing_url(cik: str, period_date: str, headers: dict):
    """Find the 13F-HR filing accession + index URL for a given CIK and period."""
    cik = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    filings = data.get("filings", {}).get("recent", {})

    for i in range(len(filings.get("form", []))):
        if filings["form"][i] == "13F-HR":
            report_date = filings.get("reportDate", [None] * (i + 1))[i]
            if report_date == period_date:
                accession = filings["accessionNumber"][i]
                accession_dashed = "-".join([accession[:10], accession[10:12], accession[12:]])
                docs_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik.lstrip('0')}/{accession}/{accession_dashed}-index.htm"
                )
                return accession, docs_url

    raise HTTPException(
        status_code=404,
        detail=f"No 13F-HR filing found for CIK {cik} period {period_date}"
    )


def get_html_table_url(documents_url: str, headers: dict) -> str:
    """Find the HTML information table URL from the filing index page."""
    resp = requests.get(documents_url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.find("table", {"class": "tableFile"})
    if not table:
        raise HTTPException(status_code=404, detail="No documents table found in filing index")

    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) >= 4:
            doc_type = cols[3].get_text(strip=True).upper()
            if "INFORMATION TABLE" in doc_type:
                link = cols[2].find("a")
                if link:
                    return "https://www.sec.gov" + link["href"]

    # Fallback: look for .htm files that are likely the info table
    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) >= 3:
            filename = cols[2].get_text(strip=True).lower()
            if "infotable" in filename or "form13f" in filename:
                link = cols[2].find("a")
                if link:
                    return "https://www.sec.gov" + link["href"]

    raise HTTPException(status_code=404, detail="Could not find information table in filing")


def parse_number(text: str) -> int:
    try:
        clean = re.sub(r"[^\d]", "", text)
        return int(clean) if clean else 0
    except Exception:
        return 0


def parse_13f_html(html_url: str, headers: dict) -> list:
    """Download and parse the 13F HTML information table into a list of holding dicts."""
    resp = requests.get(html_url, headers=headers, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")
    all_tables = soup.find_all("table")

    target_table = None
    for table in all_tables:
        for row in table.find_all("tr")[:5]:
            cells = row.find_all(["td", "th"])
            texts = [c.get_text(strip=True).upper() for c in cells]
            if any("CUSIP" in t for t in texts) and any(
                "NAME OF ISSUER" in t or "ISSUER" in t for t in texts
            ):
                target_table = table
                break
        if target_table:
            break

    if not target_table:
        raise HTTPException(status_code=422, detail="Could not find 13F holdings table in HTML")

    rows = target_table.find_all("tr")

    # Detect header row
    header_row_idx = None
    column_headers = []
    for idx, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        texts = [c.get_text(strip=True).upper() for c in cells]
        has_cusip = any("CUSIP" in t for t in texts)
        has_issuer = any("NAME OF ISSUER" in t or "ISSUER" in t for t in texts)
        has_value = any("VALUE" in t or "DOLLAR" in t or "(TO THE NEAREST" in t for t in texts)
        if has_cusip and has_issuer and has_value:
            header_row_idx = idx
            column_headers = texts
            break
        elif has_cusip and has_issuer:
            header_row_idx = idx
            column_headers = texts
            break

    if header_row_idx is None:
        raise HTTPException(status_code=422, detail="Could not find header row in 13F table")

    name_idx   = next((i for i, h in enumerate(column_headers) if "NAME" in h and "ISSUER" in h), 0)
    cusip_idx  = next((i for i, h in enumerate(column_headers) if "CUSIP" in h), -1)
    value_idx  = next((i for i, h in enumerate(column_headers) if "VALUE" in h or "DOLLAR" in h or "(TO THE NEAREST" in h), -1)
    shares_idx = next((i for i, h in enumerate(column_headers) if "SHRS" in h or "PRN AMT" in h or "SH/" in h or "PRN" in h), -1)
    title_idx  = next((i for i, h in enumerate(column_headers) if "TITLE" in h and "CLASS" in h), -1)

    # Put/call column
    put_call_idx = -1
    for i, h in enumerate(column_headers):
        h2 = h.replace("/", " ").replace("&", " ")
        if "PUT" in h2 and "CALL" in h2:
            put_call_idx = i
            break
        elif "PUT" in h2:
            put_call_idx = i
            break
        elif "CALL" in h2:
            put_call_idx = i
            break

    if cusip_idx == -1:
        raise HTTPException(status_code=422, detail="Could not find CUSIP column")

    holdings = []
    for row in rows[header_row_idx + 1:]:
        cells = row.find_all(["td", "th"])
        max_needed = max(name_idx, cusip_idx,
                         value_idx if value_idx != -1 else 0,
                         shares_idx if shares_idx != -1 else 0)
        if len(cells) <= max_needed:
            continue
        try:
            company_name = cells[name_idx].get_text(strip=True) if name_idx < len(cells) else ""
            cusip = cells[cusip_idx].get_text(strip=True) if cusip_idx < len(cells) else ""

            if not company_name or not cusip or len(cusip) != 9 or "OMB" in company_name.upper():
                continue

            value  = parse_number(cells[value_idx].get_text(strip=True)) if value_idx != -1 and value_idx < len(cells) else 0
            shares = parse_number(cells[shares_idx].get_text(strip=True)) if shares_idx != -1 and shares_idx < len(cells) else 0

            # Put/call
            put_call_value = ""
            if put_call_idx != -1 and put_call_idx < len(cells):
                pc_text = cells[put_call_idx].get_text(strip=True).upper()
                if pc_text in ("PUT", "CALL"):
                    put_call_value = pc_text
                elif "PUT" in pc_text or "CALL" in pc_text:
                    for part in pc_text.split():
                        if part in ("PUT", "CALL"):
                            put_call_value = part
                            break

            title_class = cells[title_idx].get_text(strip=True).upper() if title_idx != -1 and title_idx < len(cells) else ""
            is_option = put_call_value in ("PUT", "CALL")

            holdings.append({
                "company_name": company_name,
                "cusip": cusip,
                "value": value,
                "shares": shares,
                "share_type": "SH",
                "put_call": put_call_value,
                "title_of_class": title_class,
                "is_option": is_option,
                "option_type": put_call_value if is_option else None,
            })
        except Exception:
            continue

    return holdings


def extract_13f_data(cik: str, period_date: str, headers: dict) -> pd.DataFrame:
    """Full pipeline: find filing → find HTML table → parse → DataFrame."""
    accession, docs_url = get_filing_url(cik, period_date, headers)
    time.sleep(0.5)
    html_url = get_html_table_url(docs_url, headers)
    time.sleep(0.5)
    holdings = parse_13f_html(html_url, headers)
    if not holdings:
        raise HTTPException(status_code=422, detail=f"No holdings parsed for period {period_date}")
    df = pd.DataFrame(holdings)
    df["cik"] = cik
    df["period_date"] = period_date
    df["accession_number"] = accession
    return df


# ---------------------------------------------------------------------------
# Data processing — ported from notebook
# ---------------------------------------------------------------------------

def clean_holdings(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "is_option" in df.columns:
        df = df[~df["is_option"]]
    if "cusip" in df.columns:
        df = df[df["cusip"].str.len() == 9]
        df = df.drop_duplicates(subset=["cusip"], keep="first")
    for col in ("shares", "value"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df = df[(df.get("shares", pd.Series([1])) > 0) | (df.get("value", pd.Series([1])) > 0)]
    return df


def compare_holdings(old_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    old_clean = clean_holdings(old_df)
    new_clean = clean_holdings(new_df)

    old_dict = old_clean.set_index("cusip")[["company_name", "shares", "value"]].to_dict("index")
    new_dict = new_clean.set_index("cusip")[["company_name", "shares", "value"]].to_dict("index")
    all_cusips = set(old_dict) | set(new_dict)

    results = []
    for cusip in all_cusips:
        o = old_dict.get(cusip, {"company_name": "", "shares": 0, "value": 0})
        n = new_dict.get(cusip, {"company_name": "", "shares": 0, "value": 0})
        company = n["company_name"] or o["company_name"]

        shares_change = n["shares"] - o["shares"]
        value_change  = n["value"]  - o["value"]
        shares_pct = (shares_change / o["shares"] * 100) if o["shares"] > 0 else None
        value_pct  = (value_change  / o["value"]  * 100) if o["value"]  > 0 else None

        if   o["shares"] == 0 and n["shares"] > 0: change_type = "NEW"
        elif o["shares"] > 0 and n["shares"] == 0: change_type = "CLOSED"
        elif n["shares"] > o["shares"]:             change_type = "INCREASED"
        elif n["shares"] < o["shares"]:             change_type = "DECREASED"
        else:                                        change_type = "UNCHANGED"

        results.append({
            "cusip": cusip,
            "company_name": company,
            "old_shares": o["shares"], "new_shares": n["shares"],
            "shares_change": shares_change, "shares_change_pct": shares_pct,
            "old_value": o["value"],  "new_value": n["value"],
            "value_change": value_change, "value_change_pct": value_pct,
            "change_type": change_type,
        })
    return pd.DataFrame(results)


def calculate_portfolio_metrics(old_df: pd.DataFrame, new_df: pd.DataFrame, comparison_df: pd.DataFrame) -> dict:
    old_clean = clean_holdings(old_df)
    new_clean = clean_holdings(new_df)

    old_total = int(old_clean["value"].sum())
    new_total = int(new_clean["value"].sum())
    total_change = new_total - old_total
    total_change_pct = (total_change / old_total * 100) if old_total > 0 else 0

    old_pos = len(old_clean)
    new_pos = len(new_clean)

    old_top10 = int(old_clean.nlargest(10, "value")["value"].sum())
    new_top10 = int(new_clean.nlargest(10, "value")["value"].sum())
    old_conc = (old_top10 / old_total * 100) if old_total > 0 else 0
    new_conc = (new_top10 / new_total * 100) if new_total > 0 else 0

    new_count    = len(comparison_df[comparison_df["change_type"] == "NEW"])
    closed_count = len(comparison_df[comparison_df["change_type"] == "CLOSED"])
    avg_pos = (old_pos + new_pos) / 2
    turnover = ((new_count + closed_count) / avg_pos * 100) if avg_pos > 0 else 0

    return {
        "old_total_value": old_total,
        "new_total_value": new_total,
        "total_value_change": total_change,
        "total_value_change_pct": round(total_change_pct, 2),
        "old_positions": old_pos,
        "new_positions": new_pos,
        "positions_change": new_pos - old_pos,
        "old_top10_concentration": round(old_conc, 2),
        "new_top10_concentration": round(new_conc, 2),
        "concentration_change": round(new_conc - old_conc, 2),
        "new_positions_count": new_count,
        "closed_positions_count": closed_count,
        "turnover_rate": round(turnover, 2),
        "largest_increases": comparison_df[comparison_df["change_type"] == "INCREASED"]
            .nlargest(10, "value_change")[["company_name", "value_change", "value_change_pct"]].to_dict("records"),
        "largest_decreases": comparison_df[comparison_df["change_type"] == "DECREASED"]
            .nsmallest(10, "value_change")[["company_name", "value_change", "value_change_pct"]].to_dict("records"),
    }


# ---------------------------------------------------------------------------
# Sector mapping — SEC-only, no external APIs
# ---------------------------------------------------------------------------

_SEC_COMPANIES_CACHE = None

def load_sec_companies(headers: dict) -> pd.DataFrame:
    global _SEC_COMPANIES_CACHE
    if _SEC_COMPANIES_CACHE is not None:
        return _SEC_COMPANIES_CACHE
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=30)
    resp.raise_for_status()
    df = pd.DataFrame.from_dict(resp.json(), orient="index")
    df["title_normalized"] = df["title"].str.upper().str.strip()
    _SEC_COMPANIES_CACHE = df
    return df


def get_industry_sector_from_sic(sic_code) -> str:
    if not sic_code:
        return "Unknown"
    try:
        sic = int(sic_code)
    except (ValueError, TypeError):
        return "Unknown"

    if 100 <= sic <= 999:
        return "Agriculture, Mining & Construction"
    elif 1000 <= sic <= 1999:
        return "Mining & Construction"
    elif 2000 <= sic <= 3999:
        if 2000 <= sic <= 2099: return "Food & Beverage"
        elif 2800 <= sic <= 2899: return "Chemicals & Pharmaceuticals"
        elif 3600 <= sic <= 3699: return "Electronic & Electric Equipment"
        elif 3700 <= sic <= 3799: return "Transportation Equipment"
        elif 3800 <= sic <= 3899: return "Instruments & Related Products"
        else: return "Manufacturing"
    elif 4000 <= sic <= 4999:
        if 4800 <= sic <= 4899: return "Telecommunications"
        elif 4900 <= sic <= 4999: return "Electric, Gas & Sanitary Services"
        else: return "Transportation Services"
    elif 5000 <= sic <= 5199: return "Wholesale Trade"
    elif 5200 <= sic <= 5999: return "Retail Trade"
    elif 6000 <= sic <= 6799:
        if 6000 <= sic <= 6299: return "Banking & Financial Services"
        elif 6300 <= sic <= 6499: return "Insurance"
        elif 6500 <= sic <= 6599: return "Real Estate"
        elif 6700 <= sic <= 6799: return "Investment & Asset Management"
    elif 7000 <= sic <= 8999:
        if 7370 <= sic <= 7379: return "Technology & Software"
        elif 7300 <= sic <= 7399: return "Business Services"
        elif 8000 <= sic <= 8099: return "Healthcare Services"
        elif 7800 <= sic <= 7899: return "Entertainment & Recreation"
        else: return "Services"
    elif 9000 <= sic <= 9999: return "Public Administration"
    return "Unknown"


def get_sector_info(cusip: str, company_name: str, companies_df: pd.DataFrame, headers: dict) -> dict:
    try:
        name_norm = company_name.upper().strip()
        match = companies_df[companies_df["title_normalized"] == name_norm]
        cik_found = ticker = sic_code = sic_desc = None

        if not match.empty:
            row = match.iloc[0]
            cik_found = str(row["cik_str"]).zfill(10)
            ticker = row.get("ticker", "")
        else:
            # Partial match on first significant word
            clean = re.sub(r"\b(INC|CORP|LTD|LLC|CO|THE)\b", "", name_norm).strip()
            words = clean.split()
            if words and len(words[0]) > 2:
                partial = companies_df[
                    companies_df["title_normalized"].str.contains(words[0], case=False, na=False)
                ]
                if not partial.empty:
                    row = partial.iloc[0]
                    cik_found = str(row["cik_str"]).zfill(10)
                    ticker = row.get("ticker", "")

        if cik_found:
            sub = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik_found}.json",
                headers=headers, timeout=10
            )
            if sub.ok:
                sub_data = sub.json()
                sic_code = sub_data.get("sic")
                sic_desc = sub_data.get("sicDescription", "")
            time.sleep(0.15)

        return {
            "cusip": cusip,
            "ticker": ticker or "",
            "sector": get_industry_sector_from_sic(sic_code),
            "industry": sic_desc or "Unknown",
            "sic": str(sic_code) if sic_code else "",
            "sic_description": sic_desc or "",
        }
    except Exception:
        return {"cusip": cusip, "ticker": "", "sector": "Unknown",
                "industry": "Unknown", "sic": "", "sic_description": ""}


def enrich_with_sectors(df: pd.DataFrame, headers: dict) -> pd.DataFrame:
    companies_df = load_sec_companies(headers)
    unique = df[["cusip", "company_name"]].drop_duplicates("cusip")
    mappings = []
    for _, row in unique.iterrows():
        mappings.append(get_sector_info(row["cusip"], row["company_name"], companies_df, headers))
    mapping_df = pd.DataFrame(mappings)
    enriched = df.merge(mapping_df[["cusip", "ticker", "sector", "industry", "sic_description"]],
                        on="cusip", how="left")
    enriched["sector"] = enriched["sector"].fillna("Unknown")
    enriched["industry"] = enriched["industry"].fillna("Unknown")
    return enriched


def compare_sectors(old_enriched: pd.DataFrame, new_enriched: pd.DataFrame) -> pd.DataFrame:
    def agg(df):
        grp = df.groupby("sector").agg(total_value=("value", "sum"), num_positions=("cusip", "count")).reset_index()
        total = df["value"].sum()
        grp["pct_of_portfolio"] = grp["total_value"] / total * 100 if total > 0 else 0
        return grp

    old_s = agg(old_enriched)
    new_s = agg(new_enriched)
    cmp = pd.merge(old_s, new_s, on="sector", how="outer", suffixes=("_old", "_new")).fillna(0)
    cmp["value_change"] = cmp["total_value_new"] - cmp["total_value_old"]
    cmp["value_change_pct"] = (
        (cmp["value_change"] / cmp["total_value_old"] * 100)
        .replace([float("inf"), -float("inf")], 0).fillna(0)
    )
    cmp["pct_of_portfolio_change"] = cmp["pct_of_portfolio_new"] - cmp["pct_of_portfolio_old"]

    def classify(row):
        if row["total_value_old"] == 0 and row["total_value_new"] > 0: return "NEW"
        if row["total_value_old"] > 0 and row["total_value_new"] == 0: return "CLOSED"
        if row["total_value_new"] > row["total_value_old"]:             return "INCREASED"
        if row["total_value_new"] < row["total_value_old"]:             return "DECREASED"
        return "UNCHANGED"

    cmp["change_type"] = cmp.apply(classify, axis=1)
    return cmp.sort_values("value_change", ascending=False, key=abs)


# ---------------------------------------------------------------------------
# LLM context + query — ported from notebook
# ---------------------------------------------------------------------------

def prepare_context(old_enriched, new_enriched, comparison_df, sector_comparison, metrics) -> dict:
    def safe_records(df):
        return json.loads(df.to_json(orient="records", default_handler=str))

    return {
        "portfolio_summary": {
            "old_total_value": metrics["old_total_value"],
            "new_total_value": metrics["new_total_value"],
            "value_change": metrics["total_value_change"],
            "value_change_pct": metrics["total_value_change_pct"],
            "old_positions": metrics["old_positions"],
            "new_positions": metrics["new_positions"],
            "positions_change": metrics["positions_change"],
            "turnover_rate": metrics["turnover_rate"],
        },
        "position_changes": {
            "new":       safe_records(comparison_df[comparison_df["change_type"] == "NEW"][["company_name", "new_value", "cusip"]]),
            "closed":    safe_records(comparison_df[comparison_df["change_type"] == "CLOSED"][["company_name", "old_value", "cusip"]]),
            "increased": safe_records(comparison_df[comparison_df["change_type"] == "INCREASED"].nlargest(20, "value_change")[["company_name", "value_change", "value_change_pct", "old_value", "new_value", "cusip"]]),
            "decreased": safe_records(comparison_df[comparison_df["change_type"] == "DECREASED"].nsmallest(20, "value_change")[["company_name", "value_change", "value_change_pct", "old_value", "new_value", "cusip"]]),
        },
        "sector_analysis": {
            "sectors": safe_records(sector_comparison[[
                "sector", "total_value_old", "total_value_new",
                "value_change", "value_change_pct",
                "pct_of_portfolio_old", "pct_of_portfolio_new", "change_type"
            ]])
        },
        "current_holdings": {
            "top_20_by_value": safe_records(
                new_enriched.nlargest(20, "value")[["company_name", "sector", "value", "shares", "cusip"]]
            )
        },
    }


def create_system_prompt() -> str:
    return (
        "You are a financial analysis assistant specializing in 13F filings analysis.\n\n"
        "You have access to detailed portfolio holdings data including:\n"
        "- Position-level changes (new, closed, increased, decreased positions)\n"
        "- Sector-level aggregations and changes\n"
        "- Portfolio metrics (total value, concentration, turnover)\n"
        "- Individual company holdings with sector classifications\n\n"
        "When answering questions:\n"
        "1. Be precise with numbers — always include dollar amounts and percentages when relevant\n"
        "2. Format large numbers with commas (e.g., $1,234,567)\n"
        "3. If asked about sectors, note that some positions may be 'Unknown' due to data availability\n"
        "4. For company-specific questions, provide the name, value, and change information\n"
        "5. When comparing periods, clearly state 'previous period' vs 'current period' or use actual dates\n"
        "6. Be concise but comprehensive\n"
        "7. If data is not available, clearly state what is missing\n\n"
        "Remember: All values are in dollars (not thousands). Percentages should be 1–2 decimal places."
    )


def query_gemini(question: str, context: dict, api_key: str) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("models/gemini-2.5-flash")
    prompt = (
        f"{create_system_prompt()}\n\n"
        f"Portfolio Data Context:\n{json.dumps(context, indent=2)}\n\n"
        f"User Question: {question}\n\n"
        "Please answer the question using the portfolio data provided above. "
        "Be specific and cite actual numbers from the data."
    )
    response = model.generate_content(prompt)
    return response.text


# ---------------------------------------------------------------------------
# Validate Gemini key endpoint
# ---------------------------------------------------------------------------

@app.post("/validate-key")
def validate_key(payload: dict):
    """Quick check that a Gemini API key is valid."""
    api_key = payload.get("gemini_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="No API key provided")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("models/gemini-2.5-flash")
        model.generate_content("Say OK in one word")
        return {"valid": True}
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid API key: {str(e)}")


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    """
    Full pipeline:
      1. Validate dates
      2. Fetch both 13F filings from SEC EDGAR
      3. Compare holdings + calculate metrics
      4. Enrich with sector data (SEC-only, no external APIs)
      5. Ask Gemini the user's question
      6. Return answer + summary
    """
    # Validate date ordering
    if req.period_prev >= req.period_curr:
        raise HTTPException(
            status_code=400,
            detail="period_curr must be later than period_prev"
        )

    headers = make_headers(req.sec_email)

    # --- Step 1: Extract holdings ---
    old_df = extract_13f_data(req.cik, req.period_prev, headers)
    time.sleep(1)
    new_df = extract_13f_data(req.cik, req.period_curr, headers)

    # --- Step 2: Compare + metrics ---
    comparison_df = compare_holdings(old_df, new_df)
    metrics = calculate_portfolio_metrics(old_df, new_df, comparison_df)

    # --- Step 3: Sector enrichment ---
    old_enriched = enrich_with_sectors(clean_holdings(old_df), headers)
    new_enriched = enrich_with_sectors(clean_holdings(new_df), headers)
    sector_comparison = compare_sectors(old_enriched, new_enriched)

    # --- Step 4: Build LLM context ---
    context = prepare_context(old_enriched, new_enriched, comparison_df, sector_comparison, metrics)

    # --- Step 5: Query Gemini ---
    try:
        answer = query_gemini(req.question, context, req.gemini_api_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {str(e)}")

    # Strip DataFrame objects from metrics before returning (not JSON-serialisable)
    summary = {k: v for k, v in metrics.items()
               if not isinstance(v, (pd.DataFrame, pd.Series))}

    return AnalyzeResponse(
        answer=answer,
        portfolio_summary=summary,
        cik=req.cik,
        period_prev=req.period_prev,
        period_curr=req.period_curr,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}
