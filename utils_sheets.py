# -*- coding: utf-8 -*-
"""
utils_sheets.py (firma-robusto)
- Detección por firma: si el .xlsx no empieza con PK (zip), lo leo como CSV.
- Si el .csv falla, intento abrir como Excel (xlsx renombrado).
- Mantiene: detectar encabezado "SERVICIO", nombre=APELLIDOS+NOMBRES,
  suma de meses, cédula como texto, consultar_deuda, registrar_pago, obtener_hashes_existentes.
"""

import os, csv, unicodedata
from datetime import datetime

def _normalize_cols(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df

def _strip_accents_lower(s: str) -> str:
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))

def _to_str_id(x):
    if x is None:
        return ""
    s = str(x).strip().replace(" ", "").replace(",", "")
    if s.endswith(".0"):
        s = s[:-2]
    try:
        if "e" in s.lower():
            from decimal import Decimal
            s = str(Decimal(s).quantize(Decimal("1")))
    except Exception:
        pass
    return s

def _pick(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

def _detect_header_and_relabel(df_raw):
    import numpy as np
    cols = [str(c).strip().lower() for c in df_raw.columns]
    if all(c.startswith("unnamed") for c in cols):
        mask = df_raw.apply(lambda row: any(str(x).strip().upper() == "SERVICIO" for x in row), axis=1)
        idx = np.where(mask.values)[0]
        if len(idx):
            row_idx = int(idx[0])
            header = df_raw.iloc[row_idx].tolist()
            df = df_raw.iloc[row_idx + 1:].copy()
            df.columns = header
            return df
    return df_raw.copy()

def _is_probably_xlsx(path):
    try:
        with open(path, "rb") as f:
            sig = f.read(4)
        # archivos xlsx (Office Open XML) son ZIP (firma PK\x03\x04)
        return sig == b"PK\x03\x04"
    except Exception:
        return False

def _load_deuda_df():
    import pandas as pd

    path_xlsx = "deuda_clientes.xlsx"
    path_csv  = "deuda_clientes.csv"

    df_raw = None
    used = None

    # 1) Si existe .xlsx
    if os.path.exists(path_xlsx):
        if _is_probably_xlsx(path_xlsx):
            try:
                df_raw = pd.read_excel(path_xlsx, engine=None)
                used = f"{path_xlsx} (xlsx)"
            except Exception:
                df_raw = pd.read_excel(path_xlsx, engine="openpyxl")
                used = f"{path_xlsx} (xlsx/openpyxl)"
        else:
            # El archivo NO tiene firma ZIP => probablemente es CSV renombrado
            try:
                df_raw = pd.read_csv(path_xlsx, encoding="utf-8-sig")
                used = f"{path_xlsx} (csv-misnamed)"
            except Exception:
                # último intento: leer igualmente como excel
                df_raw = pd.read_excel(path_xlsx, engine="openpyxl")
                used = f"{path_xlsx} (forced-openpyxl)"

    # 2) Si aún no cargamos, probar .csv
    if df_raw is None and os.path.exists(path_csv):
        try:
            df_raw = pd.read_csv(path_csv, encoding="utf-8-sig")
            used = f"{path_csv} (csv)"
        except Exception:
            # puede ser xlsx renombrado a csv
            df_raw = pd.read_excel(path_csv, engine="openpyxl")
            used = f"{path_csv} (xlsx-misnamed)"

    if df_raw is None:
        raise FileNotFoundError("No se encontró deuda_clientes.xlsx / deuda_clientes.csv")

    # ---- normalización ----
    df = _detect_header_and_relabel(df_raw)
    df = _normalize_cols(df)

    col_servicio = _pick(df, ["servicio"])
    col_id       = _pick(df, ["cedula", "cédula", "id", "ruc", "identificacion", "identificación"])
    col_ap       = _pick(df, ["apellidos", "apellido", "apellidos_y_nombres"])
    col_no       = _pick(df, ["nombres", "nombre", "razon social", "razón social"])

    if col_ap and col_no:
        df["nombre"] = (df[col_ap].astype(str).str.strip() + " " + df[col_no].astype(str).str.strip()).str.replace(r"\s+", " ", regex=True)
    elif col_no:
        df["nombre"] = df[col_no].astype(str).str.strip()
    else:
        df["nombre"] = ""

    if col_servicio and col_servicio != "servicio":
        df = df.rename(columns={col_servicio: "servicio"})
    if col_id and col_id != "cedula":
        df = df.rename(columns={col_id: "cedula"})
    if col_ap and col_ap != "apellidos":
        df = df.rename(columns={col_ap: "apellidos"})
    if col_no and col_no != "nombres":
        df = df.rename(columns={col_no: "nombres"})

    for c in ["servicio", "apellidos", "nombres", "nombre"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()

    if "cedula" in df.columns:
        df["cedula"] = df["cedula"].apply(_to_str_id)
    else:
        df["cedula"] = ""

    meses = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    mes_cols = [m for m in meses if m in df.columns]
    for m in mes_cols:
        df[m] = pd.to_numeric(df[m], errors="coerce").fillna(0)
    if mes_cols:
        df["deuda"] = df[mes_cols].sum(axis=1)
    else:
        if "deuda" not in df.columns:
            df["deuda"] = 0

    # Orden amistoso
    front = [c for c in ["servicio","cedula","apellidos","nombres","nombre"] if c in df.columns]
    rest  = [c for c in df.columns if c not in front]
    df = df[front + rest]

    print(f"[deudas] Fuente usada: {used} | Registros: {len(df)}")
    return df, mes_cols

def consultar_deuda(cedula_o_nombre):
    try:
        df, mes_cols = _load_deuda_df()
    except Exception as e:
        print(f"[consultar_deuda] Error: {e}")
        return "⚠️ No se pudo cargar la base de deudas. Verifica el archivo."

    q = str(cedula_o_nombre or "").strip()
    if not q:
        return "⚠️ Ingresa una cédula o nombre."

    by_id = df[df["cedula"].astype(str).str.strip() == q]
    if not by_id.empty:
        fila = by_id.iloc[0]
        detalle = ""
        if mes_cols:
            meses_pos = [f"{m.capitalize()}: {float(fila[m]):.2f}" for m in mes_cols if float(fila[m]) > 0]
            if meses_pos:
                detalle = "\n📆 " + " | ".join(meses_pos)
        return f"👤 Cliente: {fila.get('nombre','')}\n🆔 Cédula: {fila.get('cedula','')}\n💰 Deuda total: ${float(fila.get('deuda',0)):.2f}{detalle}"

    qn = _strip_accents_lower(q)
    df["_nombre_norm"] = df["nombre"].map(_strip_accents_lower)
    hits = df[df["_nombre_norm"].str.contains(qn, na=False)]
    if not hits.empty:
        fila = hits.iloc[0]
        detalle = ""
        if mes_cols:
            meses_pos = [f"{m.capitalize()}: {float(fila[m]):.2f}" for m in mes_cols if float(fila[m]) > 0]
            if meses_pos:
                detalle = "\n📆 " + " | ".join(meses_pos)
        return f"👤 Cliente: {fila.get('nombre','')}\n🆔 Cédula: {fila.get('cedula','')}\n💰 Deuda total: ${float(fila.get('deuda',0)):.2f}{detalle}"

    return "❌ No se encontró deuda para ese cliente."

# ---------------------- Registro de pagos ----------------------
_PAGOS_PATH = "pagos_registrados.csv"
_PAGOS_FIELDS = ["ts","nombre","cedula","monto","fecha","documento","banco","image_ref","hash"]

def _ensure_pagos_file():
    if not os.path.exists(_PAGOS_PATH):
        with open(_PAGOS_PATH, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_PAGOS_FIELDS)
            w.writeheader()

def obtener_hashes_existentes():
    _ensure_pagos_file()
    hashes = set()
    try:
        with open(_PAGOS_PATH, "r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                h = (row.get("hash") or "").strip()
                if h:
                    hashes.add(h)
    except Exception as e:
        print(f"[obtener_hashes_existentes] Error: {e}")
    return hashes

def registrar_pago(nombre, cedula, monto, fecha, documento, banco, image_ref, img_hash):
    try:
        _ensure_pagos_file()
        if img_hash and img_hash in obtener_hashes_existentes():
            print("[registrar_pago] Duplicado por hash, no se registra.")
            return False
        from datetime import datetime as _dt
        row = {
            "ts": _dt.utcnow().isoformat(timespec="seconds") + "Z",
            "nombre": (nombre or "").strip(),
            "cedula": _to_str_id(cedula),
            "monto": str(monto or ""),
            "fecha": (fecha or "").strip(),
            "documento": (documento or "").strip(),
            "banco": (banco or "").strip(),
            "image_ref": (image_ref or "").strip(),
            "hash": (str(img_hash) or "").strip(),
        }
        with open(_PAGOS_PATH, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_PAGOS_FIELDS)
            w.writerow(row)
        return True
    except Exception as e:
        print(f"[registrar_pago] Error: {e}")
        return False
