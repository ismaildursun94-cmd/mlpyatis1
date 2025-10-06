# server.py
# Yerel:  python -m uvicorn server:app --host 0.0.0.0 --port 8500 --reload
# Render: gunicorn -w 1 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT server:app

from __future__ import annotations

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import os, math
from typing import List, Optional
from importlib import import_module
from string import Template

# -------- Dosya yolları --------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(BASE_DIR, "Veri2024.xlsx")
PRED_LOS_XLSX = os.path.join(BASE_DIR, "PRED_LOS.xlsx")

# -------- Lazy model import --------
_model = None
_model_err: Optional[str] = None

def _get_model():
    global _model, _model_err
    if _model is not None:
        return _model
    if _model_err is not None:
        raise RuntimeError(_model_err)
    try:
        _model = import_module("proje")
        return _model
    except Exception as e:
        _model_err = f"Model yüklenemedi: {e}"
        raise RuntimeError(_model_err)

def _get_blend_w(default: float = 0.50) -> float:
    try:
        m = _get_model()
        return float(getattr(m, "XGB_RULE_BLEND", default))
    except Exception:
        return default

# -------- Form seçenekleri (tek sefer) --------
YASGRUP_LIST: List[str] = []
BOLUM_LIST: List[str]  = []
ICD_LIST: List[str]    = []
_OPTIONS_READY = False

def _safe_unique(series: pd.Series) -> List[str]:
    vals = (
        series.dropna()
        .astype(str)
        .map(lambda s: s.strip())
        .loc[lambda s: s.ne("")]
        .unique()
        .tolist()
    )
    return sorted(set(vals), key=lambda x: (len(x), x))

def _derive_yasgrup_if_needed(df: pd.DataFrame) -> pd.DataFrame:
    if "YaşGrup" in df.columns:
        return df
    if "Yaş" not in df.columns:
        df = df.copy()
        df["YaşGrup"] = None
        return df

    def _yas_to_group(y):
        try:
            y = float(y)
        except Exception:
            return None
        if y < 0: return None
        if y <= 1:  return "0-1"
        if y <= 5:  return "2-5"
        if y <= 10: return "5-10"
        if y <= 15: return "10-15"
        if y <= 25: return "15-25"
        if y <= 35: return "25-35"
        if y <= 50: return "35-50"
        if y <= 65: return "50-65"
        return "65+"

    df = df.copy()
    df["YaşGrup"] = df["Yaş"].apply(_yas_to_group)
    return df

def _load_options_once() -> None:
    global _OPTIONS_READY, YASGRUP_LIST, BOLUM_LIST, ICD_LIST
    if _OPTIONS_READY:
        return
    try:
        if os.path.exists(EXCEL_PATH):
            df = pd.read_excel(EXCEL_PATH)
            df = _derive_yasgrup_if_needed(df)

            if "ICD Kodu" in df.columns:
                raw = df["ICD Kodu"].dropna().astype(str).tolist()
                icds = set()
                for s in raw:
                    for p in [x.strip().upper() for x in s.replace(";", ",").split(",") if x.strip()]:
                        icds.add(p)
                ICD_LIST = sorted(icds)
            else:
                ICD_LIST = []

            YASGRUP_LIST = _safe_unique(df.get("YaşGrup", pd.Series([], dtype=object)))
            BOLUM_LIST   = _safe_unique(df.get("Bölüm",   pd.Series([], dtype=object)))

        elif os.path.exists(PRED_LOS_XLSX):
            df = pd.read_excel(PRED_LOS_XLSX)
            YASGRUP_LIST = _safe_unique(df.get("YaşGrup", pd.Series([], dtype=object)))
            BOLUM_LIST   = _safe_unique(df.get("Bölüm",   pd.Series([], dtype=object)))
            icd = set()
            for s in df.get("ICD_Set_Key", pd.Series([], dtype=object)).dropna().astype(str):
                for p in [x for x in s.split("||") if x]:
                    icd.add(p)
            ICD_LIST = sorted(icd)
        else:
            YASGRUP_LIST = ["15-25", "25-35", "35-50", "50-65", "65+"]
            BOLUM_LIST   = ["Dahiliye", "Kardiyoloji", "Genel Cerrahi"]
            ICD_LIST     = ["I10", "E11", "J18", "K35", "N39"]
        _OPTIONS_READY = True
    except Exception:
        YASGRUP_LIST = ["15-25", "25-35", "35-50", "50-65", "65+"]
        BOLUM_LIST   = ["Dahiliye", "Kardiyoloji", "Genel Cerrahi"]
        ICD_LIST     = ["I10", "E11", "J18", "K35", "N39"]
        _OPTIONS_READY = True

# -------- HTML (string.Template) --------
HTML_PAGE_TPL = Template(r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Yatış Günü Tahmin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; padding: 24px; background:#f7f7f9; }
    .card { background:#fff; max-width: 760px; margin: 0 auto; padding: 20px; border-radius: 14px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); }
    h1 { margin: 0 0 10px; font-size: 22px; }
    .row { display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 14px; }
    label { font-weight: 600; }
    select, input[type=text] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 10px; }
    .hint { color:#666; font-size: 13px; }
    .btn { display:inline-block; background:#2f6fed; color:#fff; border:none; border-radius: 10px; padding: 10px 16px; font-weight: 700; cursor:pointer; }
    .result { margin-top: 18px; padding: 12px; background: #f0f6ff; border: 1px solid #cfe3ff; border-radius: 10px; }
    .muted { color:#777; font-size: 13px; }
    .icd-box { height: 180px; }
    .top-note { font-size:13px; color:#666; margin-top:6px; }
    code { background:#f3f3f5; padding:2px 6px; border-radius:6px; }
    .warn { margin-top:12px; padding:10px; border-radius:10px; background:#fff5f5; border:1px solid #ffd5d5; color:#9a0000; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Yatış Günü Tahmin</h1>
    <div class="top-note">Seçtiğiniz YaşGrup + Bölüm + ICD seti için <b>Harman (Rule ∘ XGB_ENS)</b> döner.</div>

    $warn_block

    <form method="post" action="/tahmin">
      <div class="row">
        <div>
          <label>YaşGrup</label>
          <select name="yasgrup" required>
            $yas_opts
          </select>
        </div>
        <div>
          <label>Bölüm</label>
          <select name="bolum" required>
            $bolum_opts
          </select>
        </div>
        <div>
          <label>ICD (çoklu seç)</label>
          <select name="icd_list" multiple class="icd-box">
            $icd_opts
          </select>
          <div class="hint">CTRL/SHIFT ile çoklu seçim. Alternatif: aşağıya virgüllü yazabilirsiniz.</div>
        </div>
        <div>
          <label>ICD (virgülle yaz — opsiyonel)</label>
          <input type="text" name="icd_free" placeholder="Örn: I10, E11.9">
        </div>
      </div>
      <div style="margin-top: 12px;">
        <button class="btn" type="submit">Hesapla</button>
      </div>
    </form>

    $result_block

    <p class="muted" style="margin-top: 18px;">Sonuç: Harman (Rule ∘ XGB_ENS) → <b>Pred_Final_Rounded</b>.</p>
  </div>
</body>
</html>
""")

def _make_opts(values: List[str], selected: Optional[List[str]] = None) -> str:
    sel = set(selected or [])
    out = []
    for v in values:
        s = ' selected' if v in sel else ''
        out.append(f'<option value="{v}"{s}>{v}</option>')
    return "\n".join(out)

def _icd_key_from_inputs(icd_multi, icd_free_text: Optional[str]):
    icds: List[str] = []
    if icd_multi:
        if isinstance(icd_multi, list):
            icds.extend([str(x).strip().upper() for x in icd_multi if str(x).strip()])
        else:
            icds.append(str(icd_multi).strip().upper())
    if icd_free_text:
        for x in str(icd_free_text).replace(";", ",").split(","):
            x = x.strip().upper()
            if x:
                icds.append(x)
    icds = sorted(set(icds))
    key = "||".join(icds)
    return key, icds

# -------- FastAPI --------
app = FastAPI(title="Yatış Günü Tahmin API (Formlu + JSON)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.head("/")
def root_head():
    return Response(status_code=200)

@app.get("/", response_class=HTMLResponse)
def form_get():
    _load_options_once()
    warn = ""
    if not os.path.exists(EXCEL_PATH) and not os.path.exists(PRED_LOS_XLSX):
        warn = '<div class="warn">Uyarı: Veri dosyaları bulunamadı. Varsayılan seçeneklerle çalışıyor.</div>'
    page = HTML_PAGE_TPL.substitute(
        yas_opts=_make_opts(YASGRUP_LIST),
        bolum_opts=_make_opts(BOLUM_LIST),
        icd_opts=_make_opts(ICD_LIST),
        result_block="",
        warn_block=warn,
    )
    return HTMLResponse(page)

@app.post("/tahmin", response_class=HTMLResponse)
async def tahmin_post(
    yasgrup: str = Form(...),
    bolum: str = Form(...),
    icd_list: Optional[List[str]] = Form(default=None),
    icd_free: Optional[str] = Form(default=None),
):
    _load_options_once()
    try:
        m = _get_model()
    except Exception as e:
        error_html = f'<div class="warn"><b>Hata:</b> {e}</div>'
        page = HTML_PAGE_TPL.substitute(
            yas_opts=_make_opts(YASGRUP_LIST, [yasgrup]),
            bolum_opts=_make_opts(BOLUM_LIST, [bolum]),
            icd_opts=_make_opts(ICD_LIST, []),
            result_block=error_html,
            warn_block="",
        )
        return HTMLResponse(page, status_code=500)

    icd_key, icds = _icd_key_from_inputs(icd_list, icd_free)
    icd_key = m.clean_icd_set_key(icd_key)

    pred_rule, _meta = m.predict_one(yasgrup, bolum, icd_key)
    _, _, p_ens = m.xgb_predict_ens(yasgrup, bolum, icd_key, icds)

    if p_ens is not None and not (isinstance(p_ens, float) and (math.isnan(p_ens) or math.isinf(p_ens))):
        w = _get_blend_w(0.50)
        pred_final = (1.0 - w) * float(pred_rule) + w * float(p_ens)
    else:
        pred_final = float(pred_rule)

    pred_final_rounded = m.round_half_up(pred_final)

    result_html = f"""
    <div class="result">
      <div><b>Seçim</b>: YaşGrup=<code>{yasgrup}</code>, Bölüm=<code>{bolum}</code>, ICD=<code>{', '.join(icds) if icds else '(yok)'}</code></div>
      <div style="margin-top:8px;"><b>Tahminî Yatış Günü (Pred_Final_Rounded)</b>: <span style="font-size:20px;">{pred_final_rounded}</span></div>
    </div>
    """

    page = HTML_PAGE_TPL.substitute(
        yas_opts=_make_opts(YASGRUP_LIST, [yasgrup]),
        bolum_opts=_make_opts(BOLUM_LIST, [bolum]),
        icd_opts=_make_opts(ICD_LIST, icds),
        result_block=result_html,
        warn_block="",
    )
    return HTMLResponse(page)

@app.post("/api/predict")
async def api_predict(payload: dict):
    _load_options_once()
    try:
        m = _get_model()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    yasgrup = str(payload.get("yasgrup", "")).strip()
    bolum   = str(payload.get("bolum", "")).strip()
    icds_in = payload.get("icd", []) or []
    if not isinstance(icds_in, list):
        return JSONResponse({"error": "icd must be list"}, status_code=400)

    icd_key = m.clean_icd_set_key("||".join(sorted(set([str(x).strip().upper() for x in icds_in if str(x).strip()]))))
    pred_rule, _meta = m.predict_one(yasgrup, bolum, icd_key)
    _, _, p_ens = m.xgb_predict_ens(yasgrup, bolum, icd_key, icds_in)

    if p_ens is not None and not (isinstance(p_ens, float) and (math.isnan(p_ens) or math.isinf(p_ens))):
        w = _get_blend_w(0.50)
        pred_final = (1.0 - w) * float(pred_rule) + w * float(p_ens)
    else:
        pred_final = float(pred_rule)

    return {
        "yasgrup": yasgrup,
        "bolum": bolum,
        "icd": icds_in,
        "pred_rule": float(pred_rule),
        "pred_xgb_ens": (None if p_ens is None else float(p_ens)),
        "pred_final": float(pred_final),
        "pred_final_rounded": m.round_half_up(pred_final),
    }

@app.get("/api/options")
def api_options():
    _load_options_once()
    return {"yasgrup": YASGRUP_LIST, "bolum": BOLUM_LIST, "icd": ICD_LIST}